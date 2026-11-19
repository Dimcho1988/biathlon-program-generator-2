"""Worker-owned energy report, pinned recordings and dated personal inputs."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Literal
from pydantic import Field
from fastapi import HTTPException
from .response_monitoring import InputModel
from .response_service import ResponseStore
from . import energy_model as model


class NutritionProfile(InputModel):
    effective_from: date
    sex: Literal["MALE", "FEMALE"]
    weight_kg: float = Field(gt=20, le=250, allow_inf_nan=False)
    height_cm: float | None = Field(default=None, ge=100, le=230, allow_inf_nan=False)
    birth_year: int = Field(ge=1900, le=2100)
    resting_kcal: float | None = Field(
        default=None, ge=500, le=4000, allow_inf_nan=False
    )
    outside_factor: float = Field(default=1.2, ge=1, le=1.8, allow_inf_nan=False)
    outside_cho_fraction: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    preferences: str = Field(default="", max_length=1000)
    allergies: str = Field(default="", max_length=1000)


def profile_entries(entries):
    return sorted(
        (r for r in entries if r["kind"] == "NUTRITION"),
        key=lambda r: r["observed_date"],
    )


def profile_on(entries, day):
    rows = [r for r in profile_entries(entries) if r["observed_date"] <= str(day)]
    return rows[-1] if rows else None


def weight_on(entries, wellness, profile, day):
    # Use only dated observations at/before the day, never future/current body mass backwards.
    values = []
    for r in entries:
        if r["kind"] == "WEIGHT" and r["observed_date"] <= str(day):
            value = r["payload"].get("morning_kg")
            if model.number(value) and value > 0:
                values.append((r["observed_date"], 1, value))
    for r in wellness:
        value = (r.get("metrics", {}).get("weight") or {}).get("value")
        if r.get("date", "9999") <= str(day) and model.number(value) and value > 0:
            values.append((r["date"], 0, value))
    values = [
        r
        for r in values
        if r[0] >= profile["effective_from"]
        and (day - date.fromisoformat(r[0])).days <= 30
    ]
    return max(values)[2] if values else profile["weight_kg"]


def resting(profile, weight, day):
    if profile.get("resting_kcal") is not None:
        return profile["resting_kcal"], "ENTERED_RESTING"
    if not profile.get("height_cm") or day.year - profile["birth_year"] - 1 < 18:
        return None, "MISSING_RESTING_INPUTS"
    return (
        10 * weight
        + 6.25 * profile["height_cm"]
        - 5 * (day.year - profile["birth_year"])
        + (5 if profile["sex"] == "MALE" else -161),
        "MIFFLIN_ESTIMATE",
    )


def save_profile(repository, alias, body, actor):
    settings = repository.athlete_settings(alias)
    if settings is None:
        raise HTTPException(409, "Първо попълнете настройките за пулса.")
    today = datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone)).date()
    if (
        body.effective_from > today
        or body.birth_year > body.effective_from.year
        or today.year - body.birth_year > 100
    ):
        raise HTTPException(422, "Проверете началната дата и годината на раждане.")
    payload = body.model_dump(mode="json", exclude={"expected_revision"})
    return ResponseStore(repository).save(
        alias,
        "NUTRITION",
        str(body.effective_from),
        str(body.effective_from),
        payload,
        body.expected_revision,
        str(actor),
    )


def history(repository, alias, start=None, end=None, now=None):
    from . import model_service
    from vflat_b65.sports import supports_speed_load
    from .hr_settings_history import at
    from .component_load_context import context_from_speed_view

    settings = repository.athlete_settings(alias)
    if settings is None:
        raise HTTPException(409, "Първо попълнете настройките за пулса.")
    evaluated_at = (now or datetime.now(timezone.utc)).astimezone(
        ZoneInfo(settings.timezone)
    )
    today = evaluated_at.date()
    end = end or today
    start = start or end - timedelta(days=29)
    if start <= date.min or start > end or (end - start).days >= 90 or end > today:
        raise HTTPException(422, "Изберете период от 1 до 90 дни до днес.")
    entries = ResponseStore(repository).entries(alias)
    calendar = repository.active_activity_calendar(
        alias, start - timedelta(days=1), end
    )
    if not calendar:
        raise HTTPException(409, "Липсва обработена история на активностите.")
    snapshot = calendar.get("snapshot_payload") or {}
    wellness = snapshot.get("wellness_calendar") or []
    catalog = calendar["activities"]
    days = {}
    contexts = {}
    inputs = None
    for i in range((end - start).days + 1):
        day = start + timedelta(days=i)
        entry = profile_on(entries, day)
        p = entry["payload"] if entry else None
        days[str(day)] = {
            "day": str(day),
            "activities": [],
            "profile": p,
            "weight_kg": weight_on(entries, wellness, p, day) if p else None,
        }
    selected = {
        r["latest_shadow_run_key"]: r
        for r in catalog
        if supports_speed_load(r.get("sport"))
        and r.get("latest_shadow_run_key")
        and r.get("local_date") in days
        and days[r["local_date"]]["profile"]
    }
    from hashlib import sha256
    import json
    from .activity_derivations import reuse
    from .preparation_identity import source_fingerprint

    calculation_key = source_fingerprint("pages") if selected else None
    jobs = {}
    for key, meta in selected.items():
        d = days[meta["local_date"]]
        p = d["profile"]
        day = date.fromisoformat(d["day"])
        dated = at(settings, day)
        duration = max(meta.get("elapsed_time_s") or 0, meta.get("moving_time_s") or 0)
        if not duration or not dated.hrmax_bpm:
            continue
        identity = (meta["sport"], day)
        if identity not in contexts:
            if inputs is None:
                inputs = model_service.speed_view_inputs(repository, alias)
            if inputs["calendar"].get("generation_id") != calendar.get("generation_id"):
                raise HTTPException(409, "Данните се обновяват. Заредете отново.")
            view = model_service.speed_view(
                repository,
                alias,
                meta["sport"],
                _inputs=model_service.historical_speed_inputs(inputs, day),
                _capacity_only=True,
            )
            contexts[identity] = context_from_speed_view(view).get(
                "comparison_parameters"
            )
        args = dict(
            sex=p["sex"],
            weight=d["weight_kg"],
            hrmax=dated.hrmax_bpm,
            bounds=dated.zone_bounds_bpm,
            sport=meta["sport"],
            parameters=contexts[identity],
            duration_s=duration,
        )
        fingerprint = sha256(
            json.dumps(
                [calculation_key, model.VERSION, key, args],
                sort_keys=True,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        jobs[fingerprint] = (key, meta, args)

    def calculate(missing):
        indexed = {jobs[k][0]: k for k in missing}
        results = {}
        for key, row in repository.iter_activity_speed_history_samples(
            alias, tuple(indexed)
        ):
            f = indexed[key]
            _, meta, args = jobs[f]
            if row["activity_ref"] != meta["activity_ref"]:
                raise ValueError("Pinned activity identity mismatch")
            results[f] = model.activity(row["shadow_payload"], **args)
        return results

    cached = reuse(repository, alias, "ENERGY", list(jobs), calculate) if jobs else {}
    reports = {}
    for f, report in cached.items():
        _, meta, _ = jobs[f]
        reports[meta["activity_ref"]] = {
            **report,
            "activity_ref": meta["activity_ref"],
            "sport": meta["sport"],
            "start_at_utc": meta.get("start_at_utc"),
        }

    for meta in catalog:
        if meta.get("local_date") in days:
            days[meta["local_date"]]["activities"].append(
                reports.get(meta["activity_ref"])
                or {
                    "activity_ref": meta["activity_ref"],
                    "sport": meta["sport"],
                    "kcal": None,
                    "coverage_percent": 0.0,
                    "duration_s": max(
                        meta.get("elapsed_time_s") or 0, meta.get("moving_time_s") or 0
                    ),
                    "reason": "MISSING_INPUTS_OR_UNSUPPORTED_ACTIVITY",
                    "start_at_utc": meta.get("start_at_utc"),
                    "timeline": [],
                }
            )
    crossing_days = set()
    for meta in catalog:
        stamp = meta.get("start_at_utc")
        duration = max(meta.get("elapsed_time_s") or 0, meta.get("moving_time_s") or 0)
        if stamp:
            t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            a = t.astimezone(ZoneInfo(settings.timezone)).date()
            b = (
                (t + timedelta(seconds=duration))
                .astimezone(ZoneInfo(settings.timezone))
                .date()
            )
            if a != b:
                crossing_days.update(
                    str(a + timedelta(days=i)) for i in range((b - a).days + 1)
                )
    for key, d in days.items():
        p = d.pop("profile")
        day = date.fromisoformat(key)
        total = model.empty()
        act = model.empty()
        duration = sum(a["duration_s"] for a in d["activities"])
        known = sum(a.get("covered_seconds", 0) for a in d["activities"])
        for a in d["activities"]:
            if a["kcal"] is not None:
                model.add(act, a)
        complete = all(
            a["kcal"] is not None and a["coverage_percent"] >= 99.9
            for a in d["activities"]
        )
        # Reject overlapping/cross-midnight records instead of double-counting a day.
        spans = []
        invalid_time = key in crossing_days
        for a in d["activities"]:
            stamp = a.get("start_at_utc")
            if not stamp:
                invalid_time = True
                continue
            t = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(
                ZoneInfo(settings.timezone)
            )
            stop = (
                t.astimezone(timezone.utc) + timedelta(seconds=a["duration_s"])
            ).astimezone(ZoneInfo(settings.timezone))
            if stop.date() != t.date() or t.date() != day:
                invalid_time = True
            spans.append((t, stop))
        spans.sort()
        if any(b[0] < a[1] for a, b in zip(spans, spans[1:])):
            invalid_time = True
        rmr, basis = resting(p, d["weight_kg"], day) if p else (None, "MISSING_PROFILE")
        midnight = datetime.combine(
            day, datetime.min.time(), ZoneInfo(settings.timezone)
        )
        next_midnight = datetime.combine(
            day + timedelta(days=1), datetime.min.time(), ZoneInfo(settings.timezone)
        )
        day_seconds = (
            next_midnight.astimezone(timezone.utc) - midnight.astimezone(timezone.utc)
        ).total_seconds()
        if day == today:
            day_seconds = (
                evaluated_at.astimezone(timezone.utc)
                - midnight.astimezone(timezone.utc)
            ).total_seconds()
        outside = (
            model.outside_energy(
                max(0, day_seconds - duration),
                d["weight_kg"],
                rmr,
                p["outside_factor"],
                p["outside_cho_fraction"],
            )
            if rmr and p and duration <= day_seconds and not invalid_time
            else None
        )
        model.add(total, act)
        if outside:
            model.add(total, outside)
        # Snapshot coverage does not prove unrecorded activity absent; status is explicitly modelled.
        source_start = (snapshot.get("load_history") or {}).get("period_start")
        source_end = (snapshot.get("load_history") or {}).get("period_end")
        in_range = bool(
            source_start and source_end and source_start <= key <= source_end
        )
        ready = bool(
            p and outside and complete and in_range and not invalid_time and day < today
        )
        d.update(
            activity=model.grams(act),
            outside=outside,
            total=model.grams(total) if ready else None,
            accounted=model.grams(total),
            coverage_percent=100 * known / duration if duration else None,
            status="MODELLED" if ready else "PARTIAL",
            resting_basis=basis,
            reason=(
                None
                if ready
                else "Текущ ден, липсващ профил/покритие, неподдържан участък или припокриващи се записи."
            ),
            targets=model.dietary_targets(
                total["kcal"] if ready else None,
                d["weight_kg"],
                duration / 3600,
                bool(p and day.year - p["birth_year"] - 1 >= 18),
            ),
        )
    latest = profile_on(entries, end)
    return {
        "menu_guidance": "За хранителен режим първо прочети energy за избрания ден. Разграничавай окислени\nсубстрати от препоръчителен прием; белтъчният прием не е равен на окислените белтъчини.\nИзползвай targets като ориентир за поддържане на теглото, не задавай автоматичен\nдефицит. Ако targets липсват или денят е частичен, обясни липсата и не измисляй\nточен дневен прием. Преди конкретно меню провери посочени алергии, непоносимости\nи хранителни предпочитания; ако не са уточнени, попитай. Дай конкретни храни,\nграмажи, сурово/сготвено тегло, разпределение преди/след тренировка и сбор на\nориентировъчните kcal и макронутриенти за всяко хранене и деня. Провери сумите\nспрямо целта, обозначи хранителните стойности като приблизителни. Това е\nпредложено меню, не отчетен прием или доказан енергиен баланс. Не екстраполирай\nмодела за покой или за непокрити активности. За лица под 18 г. не предписвай\nавтоматични количествени диети; насочи към индивидуален преглед от специалист.\n",
        "schema_version": model.VERSION,
        "period_start": str(start),
        "period_end": str(end),
        "today": str(today),
        "timezone": settings.timezone,
        "evaluated_at": evaluated_at.isoformat(),
        "generation_id": calendar.get("generation_id"),
        "revision": calendar.get("revision"),
        "profile": latest["payload"] if latest else None,
        "profile_revision": latest["revision"] if latest else 0,
        "profiles": [
            {
                "effective_from": r["observed_date"],
                "revision": r["revision"],
                **r["payload"],
            }
            for r in profile_entries(entries)
        ],
        "days": list(days.values()),
        "assumptions": [
            "Измерен пулс по участъци; при липса — позиция в същата зона по наличния скоростен модел. Това преобразуване не е независимо валидирано за енергоразход.",
            "Кубичният модел се прилага при 50–100% HRmax. Извън обхвата разходът остава непокрит. HRmod не заменя измерения пулс в тази регресия.",
            "Белтъчини: 3,28% от енергията при активност и 0,81 mg/kg/min извън нея са групови ориентири, не индивидуални измервания. Прилагането им към кратки/интервални активности е екстраполация.",
            "Извън активности: разходът в покой × оставащото време × избрания фактор. Въглехидратният дял е допускане от профила. Крачките не се добавят втори път.",
            "Грамовете са енергийни еквиваленти (4/9/4 kcal/g), не измерена загуба на тъкани. Хранителният прием се препоръчва отделно.",
            "Дневният разход предполага, че всички активности са записани. Текущият ден няма завършена дневна цел.",
        ],
        "references": [
            "https://pubmed.ncbi.nlm.nih.gov/40135445/",
            "https://pubmed.ncbi.nlm.nih.gov/40117058/",
            "https://pubmed.ncbi.nlm.nih.gov/2305711/",
        ],
    }
