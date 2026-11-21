"""Worker-owned energy report, pinned recordings and dated personal inputs."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Literal
from pydantic import Field
from fastapi import HTTPException
from .response_monitoring import InputModel
from .response_service import ResponseStore
from . import energy_model as model
from . import nutrition_recommendations as nutrition


class NutritionProfile(InputModel):
    effective_from: date
    sex: Literal["MALE", "FEMALE"] | None = None
    weight_kg: float | None = Field(default=None, gt=20, le=250, allow_inf_nan=False)
    height_cm: float | None = Field(default=None, ge=100, le=230, allow_inf_nan=False)
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    resting_kcal: float | None = Field(
        default=None, ge=500, le=4000, allow_inf_nan=False
    )
    outside_factor: float = Field(default=1.2, ge=1, le=1.8, allow_inf_nan=False)
    outside_cho_fraction: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    strength_met: float = Field(default=3.5, ge=1, le=12, allow_inf_nan=False)
    strength_cho_fraction: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
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
        if r[0] >= (profile.get("effective_from") or "1900-01-01")
        and (day - date.fromisoformat(r[0])).days <= 30
    ]
    return max(values)[2] if values else profile.get("weight_kg")


def resolved_profile(entries, wellness, imported, day):
    manual = profile_on(entries, day)
    base = NutritionProfile(effective_from=day).model_dump(mode="json", exclude={"expected_revision"})
    # Sex/year of birth and adult height describe the person; current imported
    # body mass has an observation date and is never projected into the past.
    base.update({k: imported[k] for k in ("sex", "height_cm", "birth_year") if imported.get(k) is not None})
    weights = [r for r in imported.get("weight_observations", []) if r["day"] <= str(day)]
    base["effective_from"] = "1900-01-01"
    if weights:
        observation = max(weights, key=lambda r: r["day"])
        base["weight_kg"] = observation["weight_kg"]
        base["effective_from"] = observation["day"]
    elif imported.get("observed_on", "9999") <= str(day) and imported.get("weight_kg"):
        base["weight_kg"] = imported["weight_kg"]
        base["effective_from"] = imported["observed_on"]
    automatic_weight_from = base["effective_from"]
    if manual:
        base.update({k: v for k, v in manual["payload"].items() if v is not None})
        if not manual["payload"].get("weight_kg"):
            base["effective_from"] = automatic_weight_from
    base["weight_kg"] = weight_on(entries, wellness, base, day)
    base["effective_from"] = manual["observed_date"] if manual else str(day)
    return base


def resting(profile, weight, day):
    if profile.get("resting_kcal") is not None:
        return profile["resting_kcal"], "ENTERED_RESTING"
    if (not weight or not profile.get("height_cm") or not profile.get("sex")
            or not profile.get("birth_year") or day.year - profile["birth_year"] - 1 < 18):
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
        or (body.birth_year is not None and (body.birth_year > body.effective_from.year
            or today.year - body.birth_year > 100))
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
    tomorrow = today + timedelta(days=1)
    end = end or tomorrow
    start = start or end - timedelta(days=29)
    if start <= date.min + timedelta(days=8) or start > end or (end - start).days >= 90 or end > tomorrow:
        raise HTTPException(422, "Изберете период от 1 до 90 дни до утре включително.")
    # Fetch the same donors even for a single-day request or a changed chart range.
    history_start = min(start, today) - timedelta(days=7)
    entries = ResponseStore(repository).entries(alias)
    calendar = repository.active_activity_calendar(
        alias, history_start - timedelta(days=1), min(end, today)
    )
    if not calendar:
        raise HTTPException(409, "Липсва обработена история на активностите.")
    snapshot = calendar.get("snapshot_payload") or {}
    wellness = snapshot.get("wellness_calendar") or []
    imported = snapshot.get("nutrition_body_inputs") or {}
    catalog = [r for r in calendar["activities"] if r.get("local_date", "9999") <= str(today)]
    days = {}
    contexts = {}
    inputs = None
    for i in range((end - history_start).days + 1):
        day = history_start + timedelta(days=i)
        p = resolved_profile(entries, wellness, imported, day)
        days[str(day)] = {
            "day": str(day),
            "activities": [],
            "profile": p,
            "weight_kg": p["weight_kg"],
        }
    selected = {
        r["latest_shadow_run_key"]: r
        for r in catalog
        if not model.is_strength(r.get("sport"))
        and r.get("latest_shadow_run_key")
        and r.get("local_date") in days
        and days[r["local_date"]]["weight_kg"]
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
        if not duration:
            continue
        identity = (meta["sport"], day)
        if identity not in contexts and supports_speed_load(meta.get("sport")):
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
            sex=p.get("sex"),
            weight=d["weight_kg"],
            hrmax=dated.hrmax_bpm,
            bounds=dated.zone_bounds_bpm,
            sport=meta["sport"],
            parameters=contexts.get(identity),
            duration_s=duration,
            resting_kcal=resting(p, d["weight_kg"], day)[0],
            fallback_cho=p["outside_cho_fraction"],
        )
        fingerprint = sha256(
            json.dumps(
                [calculation_key, model.CALCULATION_VERSION, key, args],
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
            d = days[meta["local_date"]]
            p = d["profile"]
            duration = max(meta.get("elapsed_time_s") or 0, meta.get("moving_time_s") or 0)
            if model.is_strength(meta.get("sport")) and d["weight_kg"] and duration > 0:
                rmr, _ = resting(p, d["weight_kg"], date.fromisoformat(d["day"]))
                reports[meta["activity_ref"]] = {
                    **model.strength_activity(
                        weight=d["weight_kg"], duration_s=duration,
                        met=p.get("strength_met", 3.5),
                        cho=p.get("strength_cho_fraction", 0.5), resting_kcal=rmr,
                    ),
                    "activity_ref": meta["activity_ref"], "sport": meta["sport"],
                    "start_at_utc": meta.get("start_at_utc"),
                }
            elif meta["activity_ref"] not in reports and d["weight_kg"] and duration > 0:
                # A catalog-only activity still gets a transparent sport/time prior.
                # Absence of a pinned recording is not evidence of rest.
                day = date.fromisoformat(d["day"])
                dated = at(settings, day)
                reports[meta["activity_ref"]] = {
                    **model.activity({}, sex=p.get("sex"), weight=d["weight_kg"],
                        hrmax=dated.hrmax_bpm, bounds=dated.zone_bounds_bpm,
                        sport=meta["sport"], parameters=None, duration_s=duration,
                        resting_kcal=resting(p, d["weight_kg"], day)[0],
                        fallback_cho=p["outside_cho_fraction"]),
                    "activity_ref": meta["activity_ref"], "sport": meta["sport"],
                    "start_at_utc": meta.get("start_at_utc"),
                }
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
        estimated = sum(a.get("estimated_seconds", 0) for a in d["activities"])
        unknown = max(0.0, duration - known - estimated)
        for a in d["activities"]:
            if a["kcal"] is not None:
                model.add(act, a)
        complete = all(
            a["kcal"] is not None and a["duration_s"] > 0
            and a["duration_s"] - a.get("accounted_seconds", a.get("covered_seconds", 0)) <= 1e-6
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
            if day <= today and rmr and d["weight_kg"] and duration <= day_seconds and not invalid_time
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
        reasons = []
        if day > today:
            reasons.append("Бъдещ ден: хранителната цел е предварителна; няма отчетен енергоразход.")
        if day == today:
            reasons.append("Текущ ден: показана е сумата до момента.")
        if not d["weight_kg"]:
            reasons.append("Липсва тегло за тази дата.")
        if not rmr:
            reasons.append("Липсват данни за разхода в покой.")
        if unknown > 1e-6:
            reasons.append(f"Без оценка остават {unknown / 60:.1f} min от активностите.")
        if estimated > 1e-6:
            reasons.append(f"За {estimated / 60:.1f} min е използвана резервна оценка за покой, ниска интензивност или вида активност.")
        if any(a["duration_s"] <= 0 for a in d["activities"]):
            reasons.append("Има активност без валидна продължителност.")
        if invalid_time or duration > day_seconds:
            reasons.append("Проверете припокриващи се записи или активности през полунощ; дневният сбор може да е неточен.")
        if not in_range and day <= today:
            reasons.append("Датата е извън обработената история.")
        d.update(
            activity=model.grams(act),
            outside=outside,
            total=model.grams(total) if ready else None,
            accounted=model.grams(total),
            coverage_percent=100 * known / duration if duration else None,
            estimated_activity_seconds=estimated,
            unknown_activity_seconds=unknown,
            status="FORECAST" if day > today else "MODELLED" if ready else "PARTIAL",
            resting_basis=basis,
            reason=" ".join(reasons) or None,
            outside_factor=p["outside_factor"],
            energy_sources={
                "resting_outside_kcal": outside["kcal"] / p["outside_factor"] if outside else None,
                "movement_outside_kcal": outside["kcal"] * (1 - 1 / p["outside_factor"]) if outside else None,
                "activity_kcal": act["kcal"] if any(a["kcal"] is not None for a in d["activities"]) or outside else None,
            },
        )
    visible_days = [d for key, d in days.items() if key >= str(start)]
    for d in visible_days:
        day = date.fromisoformat(d["day"])
        d["targets"], d["nutrition_basis"] = nutrition.recommend(
            day, today, days, resolved_profile(entries, wellness, imported, day)
        )
    latest = profile_on(entries, end)
    profile = resolved_profile(entries, wellness, imported, end)
    missing = [k for k in ("sex", "weight_kg", "birth_year") if not profile.get(k)]
    if not profile.get("height_cm") and not profile.get("resting_kcal"):
        missing.append("height_cm")
    return {
        "menu_guidance": nutrition.MENU_GUIDANCE,
        "nutrition_version": nutrition.VERSION,
        "schema_version": model.VERSION,
        "calculation_version": model.CALCULATION_VERSION,
        "period_start": str(start),
        "period_end": str(end),
        "today": str(today),
        "tomorrow": str(tomorrow),
        "timezone": settings.timezone,
        "evaluated_at": evaluated_at.isoformat(),
        "generation_id": calendar.get("generation_id"),
        "revision": calendar.get("revision"),
        "profile": profile if latest or imported or profile.get("weight_kg") else None,
        "profile_missing": missing,
        "profile_source_note": "Наличните лични данни се използват автоматично. Теглото следва датираните измервания от дневника и Intervals.icu; липсващите данни се попълват еднократно.",
        "profile_revision": latest["revision"] if latest else 0,
        "profiles": [
            {
                "effective_from": r["observed_date"],
                "revision": r["revision"],
                **r["payload"],
            }
            for r in profile_entries(entries)
        ],
        "days": visible_days,
        "assumptions": [
            "Измерен пулс по участъци; при липса — позиция в същата зона по наличния скоростен модел. Това преобразуване не е независимо валидирано за енергоразход.",
            "Кубичният модел се прилага при 50–100% HRmax. Под 50% се използва работен преход от покоя при 30% HRmax до долната граница на регресията; над 100% се използва горната граница. Тези участъци са резервни оценки. HRmod не заменя измерения пулс.",
            "Всяка записана активност с тегло и продължителност получава оценка. При липса на приложим пулс/скорост се използва ориентир по вида активност (MET); при налична само сурова скорост — груби скоростни диапазони за бягане, ходене и колоездене. Това не е индивидуално измерен разход.",
            "Записаните паузи без други данни се оценяват като покой. Нулева скорост при бягане навън, ходене или ски без пулс се оценява като покой; сама по себе си нулевата GPS скорост при тренажор или сила не доказва покой. Липсващият сигнал получава оценка за вида активност.",
            "При резервна MET оценка въглехидратният дял следва допускането в профила; MET не измерва субстратното разпределение. При ски и ролкови ски резервният ориентир е 8,5 MET; при неразпознат вид активност — работно допускане 3,5 MET.",
            "Сила: средна оценка за цялата записана сесия, включително почивките. По подразбиране 3,5 MET (2024 Adult Compendium, код 02054); kcal = MET × 3,5 × kg / 200 × min. Коефициентът се коригира в профила. Разходът включва покоя за същото време, който не се добавя повторно.",
            "При силата въглехидратният дял от небелтъчната енергия е отделно допускане (начално 50%); белтъчният дял 3,28% е екстраполация от упражнения за издръжливост. Това разпределение не е измерено и не се извежда от MET.",
            "Белтъчини: 3,28% от енергията при активност и 0,81 mg/kg/min извън нея са групови ориентири, не индивидуални измервания. Прилагането им към кратки/интервални активности е екстраполация.",
            "Извън активности: разходът в покой × оставащото време × избрания фактор. Въглехидратният дял е допускане от профила. Крачките не се добавят втори път.",
            "Грамовете са енергийни еквиваленти (4/9/4 kcal/g), не измерена загуба на тъкани. Хранителният прием се препоръчва отделно.",
            "Дневният разход предполага, че всички активности са записани. Хранителната цел е отделна прогноза от предходните 7 завършени дни: среден разход плюс 25% от положителното или отрицателното отклонение на последния ден. Текущите частични данни не участват. За утре целта е предварителна до приключване на днешния ден.",
            "При липсващи дни се използват наличните пълни дни в същия седемдневен период, без нулево попълване. При липса на пълен последен ден няма корекция. Коефициентът 0,25 не гарантира точност и не е физиологично валидиран. Бъдещата тренировъчна програма не променя автоматично тази цел.",
            "Хранителни белтъчини: 1,6 g/kg до 40 целеви kcal/kg; линейно до 1,9 g/kg при 80 kcal/kg; над това 1,9 g/kg. Без завършена история — резервни 1,8 g/kg. Това е работно правило. След белтъчините останалата енергия следва съотношението от сумарните въглехидратни и мастни kcal за използваните дни, не средното на дневните проценти.",
        ],
        "references": [
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC6839090/",
            "https://pubmed.ncbi.nlm.nih.gov/21660838/",
            "https://pubmed.ncbi.nlm.nih.gov/40135445/",
            "https://pubmed.ncbi.nlm.nih.gov/40117058/",
            "https://pubmed.ncbi.nlm.nih.gov/2305711/",
            "https://pacompendium.com/conditioning-exercise/",
            "https://pacompendium.com/running/",
            "https://pacompendium.com/walking/",
            "https://pacompendium.com/bicycling/",
            "https://pacompendium.com/winter-activities/",
            "https://pacompendium.com/water-activities/",
        ],
    }
