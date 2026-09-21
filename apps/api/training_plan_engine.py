"""Reviewable seven-day physical-training drafts using the current model stack.

No activation, external calendar write, wellness multiplier or legacy planner.
Capacity (speed-duration / expert continuous Tref) is explicitly separate from
historical C40 and the technical denominator used by canonical E spillover.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from zoneinfo import ZoneInfo

import pandas as pd

from biathlon import hr_speed, recovery_v2, speed_duration
from biathlon.constants import COMPONENTS, fresh_parameters
from biathlon.equivalence import DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM
from biathlon.periodization import build_periodization
from biathlon.physiology import _causal_tref, effective_from_direct_vector, linear_equivalence_coefficient
from biathlon.training_methods import METHODS, VERSION as METHODS_VERSION, catalog
from . import model_service

VERSION = "training-management-draft-v1"
PARAMETER_VERSION = "management-pilot-parameters-v1"
Z1_WORKING_BAND_WIDTH_BPM = 20.
PRIORITIES = {
    "RE_ENTRY": ("Z1", "STR"),
    "GENERAL_PREPARATION": ("Z2", "Z3", "STR", "Z1"),
    "SPECIAL_PREPARATION": ("Z3", "Z4", "Z2", "Z1"),
    "PRECOMPETITION": ("Z4", "Z3", "Z5", "Z1"),
    "COMPETITION": ("Z3", "Z4", "Z1"),
    "TRANSITION": ("Z1",),
}


def _payload(value):
    if value is None:
        return None
    return value.to_payload() if hasattr(value, "to_payload") else dict(value)


def _read_optional(repository, method, alias):
    reader = getattr(repository, method, None)
    return _payload(reader(alias)) if reader else None


def _round(value):
    return round(float(value), 3)


def _warning(code, message):
    return {"code": code, "message": message}


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _working_band(settings, zone):
    index = int(zone[1:]) - 1
    low, high = map(float, settings.zone_bounds_bpm[index:index + 2])
    # Z1's classification floor may be 50 bpm; it is not an exercise target.
    # This explicit pilot band leaves canonical classification/E unchanged.
    if zone == "Z1":
        low = max(low, high - Z1_WORKING_BAND_WIDTH_BPM)
    return low, high


def _daily_rows(source, today):
    rows = [{"date": r["date"], "zone": r["zone"], "effective_load": r["effective_load"]}
            for r in source.get("daily", []) if r["date"] <= today.isoformat()]
    rows += [{"date": r["date"], "zone": "STR", "effective_load": r["effective_load"]}
             for r in (source.get("strength") or {}).get("daily", []) if r["date"] <= today.isoformat()]
    # Validation retains missing days as unknown; never reconstruct daily loads
    # from a reported weekly total or a historical average.
    seen = set()
    for row in rows:
        key = row["date"], row["zone"]
        if key in seen or row["zone"] not in COMPONENTS:
            raise ValueError("Invalid canonical daily history")
        date.fromisoformat(row["date"])
        recovery_v2.number(row["effective_load"], "canonical E")
        seen.add(key)
    return rows


def _phase(periodization, day):
    key = day.isoformat()
    phase = next((p for p in periodization["phases"] if p["start_date"] <= key <= p["end_date"]), None)
    taper = any(p["start_date"] <= key <= p["end_date"] for p in periodization.get("taper_windows", []))
    return (phase["kind"] if phase else None), taper


def _with_forecast_day(rows, day, effective):
    # Existing actual-session days are excluded before this function. On a
    # covered zero-load day, replace the zero with the single proposed dose.
    key = day.isoformat()
    return [r for r in rows if r["date"] != key] + [
        {"date": key, "zone": z, "effective_load": float(effective.get(z, 0.))} for z in COMPONENTS]


def _capacity_context(speed, settings):
    if not speed or speed.get("status") != "CALIBRATED":
        return None, [], ["NO_INDIVIDUAL_SPEED_CURVE"]
    keys = set(speed.get("active_test_keys", []))
    tests = [e["payload"] for e in speed.get("tests", []) if e.get("entry_key") in keys]
    reasons = []
    if any(t.get("test_mode", "STRICT") != "STRICT" or not t.get("maximal", False) for t in tests):
        reasons.append("EXPLORATORY_OR_NONMAXIMAL_TESTS")
    if len({float(t["duration_s"]) for t in tests}) < 2:
        reasons.append("INSUFFICIENT_INDEPENDENT_TEST_DURATIONS")
    if not settings.hrmax_bpm:
        reasons.append("HRMAX_REQUIRED_FOR_HR_SPEED_MAPPING")
    if reasons:
        return None, tests, reasons
    curve = speed_duration.calibrated(tests)
    corrections = [speed.get("zone_corrections", {}).get(z, 0.) for z in speed_duration.VOLUME_RANGES_MIN]
    curve, _ = speed_duration.adjusted(curve, tests, None, [], corrections,
                                    duration_centers=hr_speed.volume_duration_centers(settings.zone_bounds_bpm))
    predictor = hr_speed.Predictor(curve, settings.zone_bounds_bpm, settings.hrmax_bpm, speed["index_summary"])
    return predictor, tests, []


def capacity_for(method, settings, speed, context, today, allow_fallback=True):
    """Select exactly one capacity source, without a second TI/volume factor."""
    zone = method["zone"]
    low, high = _working_band(settings, zone)
    target_hr = float(low + method["position"] * (high - low))
    predictor, tests, initial_reasons = context
    reasons = list(initial_reasons)
    duration = velocity = None
    if predictor is not None:
        recent = (speed.get("index_window") or {}).get("last_activity_date")
        if not recent or (today - date.fromisoformat(recent)).days > 14:
            reasons.append("STALE_HR_SPEED_INDEX")
        if (speed.get("index_summary", {}).get(zone) or {}).get("count", 0) < 3:
            reasons.append("INSUFFICIENT_COMPARABLE_INDEX_OBSERVATIONS")
        try:
            duration = predictor.duration(target_hr)
            if predictor.metadata(target_hr)["hr_prediction_source"] != "INDEX":
                reasons.append("HR_MAPPING_USES_EXPERT_ANCHOR")
            # The broad reference curve is not an athlete-supported domain.
            if not min(t["duration_s"] for t in tests) <= duration <= max(t["duration_s"] for t in tests):
                reasons.append("OUTSIDE_OBSERVED_TEST_DURATION_SUPPORT")
            velocity = predictor.speed_for_hr(target_hr)
        except ValueError:
            reasons.append("OUTSIDE_HR_SPEED_PREDICTION_RANGE")
    if not reasons and duration is not None:
        source = "SPEED_DURATION"
        minutes = duration / 60.
        model_version = speed["model_version"]
    else:
        if not allow_fallback:
            return None
        # Expert continuous capacity at the upper zone edge; explicitly NOT
        # the canonical load history's bounded 7*E40 variable also named Tref.
        upper_capacity = sum(hr_speed.TMAX_RANGES_S[zone]) / 120.
        coefficient = linear_equivalence_coefficient(target_hr, low, high,
                                                     DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM)
        minutes = upper_capacity / max(.1, coefficient)
        source = "EXPERT_CONTINUOUS_TREF"
        velocity = None
        model_version = "expert-continuous-capacity-v1"
    return {"capacity_source": source, "capacity_minutes": _round(minutes),
            "target_hr_bpm": _round(target_hr), "target_speed_kmh": _round(velocity) if velocity else None,
            "model_version": model_version, "fallback_reasons": reasons,
            "target_zone_working_bounds_bpm": [low, high],
            "supported_test_duration_s": [min(t["duration_s"] for t in tests), max(t["duration_s"] for t in tests)] if tests else None}


def _block(kind, label, zone, minutes, hr, instructions, *, speed=None, repetition=None):
    return {"kind": kind, "label": label, "zone": zone, "duration_min": _round(minutes),
            "target_hr_bpm": _round(hr) if hr is not None else None,
            "target_speed_kmh": _round(speed) if speed is not None else None,
            "repetition": repetition, "instructions": instructions}


def _blocks(method, work, evidence, settings):
    easy_low, easy_high = _working_band(settings, "Z1")
    easy = easy_low + .35 * (easy_high - easy_low)
    blocks = []
    if method["warmup_min"]:
        blocks.append(_block("WARMUP", "Загрявка", "Z1", method["warmup_min"], easy, "Плавно леко движение в Z1."))
    if method["structure"] == "TWO_REPETITIONS":
        for rep in (1, 2):
            blocks.append(_block("WORK", f"Работна част {rep}/2", method["zone"], work / 2,
                                 evidence["target_hr_bpm"], method["instructions"],
                                 speed=evidence["target_speed_kmh"], repetition=rep))
            if rep == 1:
                blocks.append(_block("RECOVERY", "Активна почивка между повторенията", "Z1", method["recovery_min"],
                                     easy, "Много леко движение. Няма допълнителна почивка след последното повторение."))
    elif method["structure"] == "THREE_PROGRESSIVE_BLOCKS":
        low, high = settings.zone_bounds_bpm[1:3]
        for part, position in enumerate((.2, .5, .85), start=1):
            blocks.append(_block("WORK", f"Постепенна част {part}/3", "Z2", work / 3,
                                 low + position * (high - low), method["instructions"], repetition=part,
                                 speed=evidence["target_speed_kmh"] if part == 3 else None))
    else:
        blocks.append(_block("WORK", "Основна работа", method["zone"], work, evidence["target_hr_bpm"],
                             method["instructions"], speed=evidence["target_speed_kmh"]))
    if method["cooldown_min"]:
        blocks.append(_block("COOLDOWN", "Разпускане", "Z1", method["cooldown_min"], easy, "Постепенно намали усилието."))
    return blocks


def _canonical_load(blocks, settings, rows, day):
    parameters = fresh_parameters()
    direct = {z: 0. for z in COMPONENTS}
    for block in blocks:
        z = block["zone"]
        if z == "STR":
            direct[z] += block["duration_min"]
            continue
        idx = int(z[1:]) - 1
        low, high = settings.zone_bounds_bpm[idx:idx + 2]
        coefficient = linear_equivalence_coefficient(block["target_hr_bpm"], low, high,
                                                     DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM, is_z5=z == "Z5")
        direct[z] += block["duration_min"] * coefficient
    lower = (day - timedelta(days=40)).isoformat()
    upper = day.isoformat()
    technical = {z: _causal_tref(z, pd.Series([r["effective_load"] for r in rows
                    if r["zone"] == z and lower <= r["date"] < upper], dtype=float), parameters["base_loads"])
                 for z in COMPONENTS}
    vector = effective_from_direct_vector(direct, technical, parameters)
    return direct, dict(zip(COMPONENTS, map(float, vector))), technical


def _budgets(rows, day, taper=False, mesocycle_factor=1.):
    end = (day - timedelta(days=1)).isoformat()
    first40 = (day - timedelta(days=40)).isoformat()
    first7 = (day - timedelta(days=7)).isoformat()
    parameters = fresh_parameters()
    result = {}
    for z in COMPONENTS:
        previous = [r for r in rows if r["zone"] == z and first40 <= r["date"] <= end]
        recent = [r for r in previous if r["date"] >= first7]
        mean40 = sum(r["effective_load"] for r in previous) / len(previous) if previous else 0.
        mean7 = sum(r["effective_load"] for r in recent) / len(recent) if recent else 0.
        previous50 = [r for r in rows if r["zone"] == z and
                      (day - timedelta(days=50)).isoformat() <= r["date"] <= end]
        mean50 = sum(r["effective_load"] for r in previous50) / len(previous50) if previous50 else 0.
        base = max(parameters["base_loads"][z], .5 * mean50)
        target = 7 * mean40 * mesocycle_factor * (.5 if taper else 1.)
        # Candidate day's prospective seven-day window: the oldest day from
        # the completed 7-day window has rolled out before adding today's dose.
        actual = sum(r["effective_load"] for r in rows if r["zone"] == z and
                     (day - timedelta(days=6)).isoformat() <= r["date"] <= day.isoformat())
        result[z] = {"e7_daily": _round(mean7), "e40_daily": _round(mean40),
                     "index_7_40": _round((base + mean7) / (base + mean40)),
                     "target_weekly_effective": _round(target), "rolling_7d_effective": _round(actual),
                     "deficit_effective": _round(max(0., target - actual)),
                     "target_basis": "HALF_C40_TAPER_CEILING" if taper else "C40_MAINTENANCE_REFERENCE",
                     "prospective_window_start": (day - timedelta(days=6)).isoformat(),
                     "prospective_window_end": day.isoformat(),
                     "is_required_catchup": False}
    return result


def _accents(period, preferences):
    ordered = list(PRIORITIES.get(period, ("Z1",)))
    if not preferences:
        return ordered[:2]
    manual = list(preferences.get("manual_components", []))
    mode = preferences.get("accent_mode", "AUTO")
    limit = preferences.get("accent_limit", 2)
    if mode == "MANUAL":
        return manual
    return (manual + [z for z in ordered if z not in manual])[:limit] if mode == "HYBRID" else ordered[:limit]


def generate_plan(repository, alias: str, profile: dict, *, start_date: date, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    settings = repository.athlete_settings(alias)
    if settings is None:
        raise ValueError("Настройте пулсовите граници преди генериране.")
    today = now.astimezone(ZoneInfo(settings.timezone)).date()
    if start_date < today or start_date > today + timedelta(days=7):
        raise ValueError("Началото на седемдневния проект трябва да е днес или в следващите 7 дни.")
    sport = profile.get("actual_sport") or profile["sport"]
    if sport not in {"Run", "NordicSki", "RollerSki"}:
        raise ValueError("Средството не се поддържа от текущите методни профили.")
    program_start, program_end = map(date.fromisoformat, (profile["program_start"], profile["program_end"]))
    end_date = start_date + timedelta(days=6)
    calendar_settings = _read_optional(repository, "athlete_planning_calendar", alias) or {"events": []}
    events = calendar_settings.get("events", [])
    preferences = _read_optional(repository, "athlete_planning_profile", alias) or {}
    accents = _read_optional(repository, "athlete_mesocycle_accent_preferences", alias)
    periodization = build_periodization(program_start, program_end, events,
                                        reentry_days_override=profile.get("reentry_days"),
                                        taper_days=profile.get("taper_days", 7))
    envelope = repository.active_activity_calendar(alias, today - timedelta(days=89), end_date) or {}
    snapshot = envelope.get("snapshot_payload") or {}
    source = snapshot.get("load_history") or {}
    rows = _daily_rows(source, today)
    actual_activities = [a for a in envelope.get("activities", []) if a.get("local_date", "") <= today.isoformat()]
    configs = model_service.ModelStore(repository).config(alias)
    warnings = list(periodization.get("warnings", []))
    warnings += [_warning("DRAFT_REQUIRES_COACH_REVIEW", "Проект за треньорски преглед; не е активирана програма."),
                 _warning("PILOT_METHOD_SUBSET", "Първата версия предписва само начални профили за Z1–Z3. Z4, Z5 и сила остават за отделно методическо утвърждаване."),
                 _warning("LOAD_ONLY", "Wellness и стресът са диагностични; не променят автоматично дозите или Recovery."),
                 _warning("CALENDAR_DAY_RESOLUTION", "Прогнозата е по календарни дни, с най-много една предложена сесия дневно.")]
    quality = source.get("quality") or {}
    completed = sorted({r["date"] for r in rows if r["date"] < today.isoformat()})
    history_days = len([d for d in completed if d >= (today - timedelta(days=40)).isoformat()])
    component_history_days = {z: len({r["date"] for r in rows if r["zone"] == z and
                                  (today - timedelta(days=40)).isoformat() <= r["date"] < today.isoformat()}) for z in COMPONENTS}
    as_of = source.get("period_end")
    missing_days = max(0, (today - date.fromisoformat(as_of)).days) if as_of else None
    limited = min(component_history_days.values()) < 20 or bool(quality.get("limited_activities") or quality.get("excluded_activities"))
    if limited:
        warnings.append(_warning("LIMITED_LOAD_HISTORY", "Историята е кратка или съдържа непълни активности. Само ограничени леки предложения; липсващата умора не се приема за нулева."))
    warnings.append(_warning("PILOT_MAINTENANCE_CEILING", "7/40 ограничава предложеното до историческата компонентна база; автоматично увеличаване на целите и въвеждане на нов компонент още не са разрешени."))
    warnings.append(_warning("PROFILE_CONTEXT_NOT_CALIBRATION", "Възрастта, стажът, дисциплината и състезателната продължителност са запазен контекст. Тази начална версия не извежда автоматични коефициенти за дозиране от тях."))
    blocked = missing_days is not None and missing_days > 1
    if missing_days:
        limited = True
        warnings.append(_warning("STALE_LOAD_SNAPSHOT", "Има непокрити дни след последния анализ. Обновете активностите; липсата на запис не доказва почивка."))
    if start_date > today + timedelta(days=1):
        limited = True
        warnings.append(_warning("UNKNOWN_INTERVENING_LOAD", "Началото е след повече от един ден. Междинните тренировки още не са известни; проектът е ограничен и трябва да се преизчисли преди изпълнение."))
    speed = model_service.speed_view(repository, alias, sport) if envelope else None
    if speed and (speed.get("source_generation_id") != envelope.get("generation_id") or
                  speed.get("source_revision") != envelope.get("revision")):
        blocked = True
        warnings.append(_warning("INPUT_GENERATION_CHANGED", "Анализът се е обновил по време на генерирането. Генерирайте отново с една съгласувана версия."))
    context = _capacity_context(speed, settings)
    if speed and speed.get("sport") != sport:
        blocked = True
        warnings.append(_warning("SPEED_SPORT_MISMATCH", "Оценката скорост–време е за различно средство и не може да се използва за този проект."))
    manual_hours = profile.get("recent_weekly_hours")
    history_activities = [a for a in source.get("activities", [])
                          if (today - timedelta(days=28)).isoformat() <= a["date"] < today.isoformat()]
    covered28 = len([d for d in completed if d >= (today - timedelta(days=28)).isoformat()])
    if covered28 >= 14 and history_activities:
        mode_activities = [a for a in history_activities if a.get("sport") == sport]
        weekly_minutes = sum(float(a.get("duration_min") or 0.) for a in mode_activities) * 7 / covered28
        volume_source = "ACTUAL_SAME_SPORT_28_DAY_MEAN"
        if not mode_activities:
            limited = True
            warnings.append(_warning("NO_ACTUAL_MODE_EXPOSURE", "Няма скорошен обем за избраното средство. Обем от друг спорт не разрешава същия обем тук; нужен е преглед и ограничено въвеждане."))
            if manual_hours:
                weekly_minutes = sum(manual_hours) / len(manual_hours) * 60
                volume_source = "REPORTED_FOUR_WEEK_MEAN_UNVERIFIED_MODE"
    elif manual_hours:
        weekly_minutes = sum(manual_hours) / len(manual_hours) * 60
        volume_source = "REPORTED_FOUR_WEEK_MEAN"
        limited = True
        warnings.append(_warning("AGGREGATE_HISTORY_ONLY", "Въведеният седмичен обем ограничава проекта, но не се превръща в измислени дневни товари или известна готовност."))
    else:
        weekly_minutes = 0.
        volume_source = "MISSING"
        blocked = True
        warnings.append(_warning("WEEKLY_VOLUME_REQUIRED", "Нужна е достатъчна реална история или обемите от последните четири седмици."))
    available = list(profile["available_minutes"])
    for weekday in preferences.get("rest_days", []):
        available[weekday] = 0
    if preferences.get("double_session_days") or preferences.get("double_threshold_enabled"):
        warnings.append(_warning("DOUBLE_SESSIONS_NOT_GENERATED", "Запазени са предпочитания за двойни сесии. Този проект предлага най-много една сесия на ден."))
    session_limit = min(7, preferences.get("sessions_per_week", 7))
    key_limit = min(profile.get("max_key_sessions_per_week", 2), preferences.get("max_key_sessions_per_week", 3))
    meso_anchor = date.fromisoformat(str(preferences.get("mesocycle_anchor_date", profile["program_start"])))
    meso_length = preferences.get("mesocycle_length_weeks", 4)
    def meso_at(day):
        week = max(0, (day - meso_anchor).days // 7) % meso_length
        # Keep the existing first/recovery factors, cap all building weeks at
        # 1.0 until progression is approved; honor the configured cycle length.
        return week, .78 if week == meso_length - 1 else .96 if week == 0 else 1.
    meso_week, meso_factor = meso_at(start_date)
    weighted_factors = []
    for i in range(7):
        forecast_day = start_date + timedelta(days=i)
        phase, taper = _phase(periodization, forecast_day)
        reserved = any(e["event_type"] in {"MAIN_RACE", "CONTROL_RACE", "TEST", "UNAVAILABLE"}
                       and str(e["start_date"]) <= forecast_day.isoformat() <= str(e["end_date"]) for e in events)
        weighted_factors.append(0. if reserved or not phase else
                                available[forecast_day.weekday()] * meso_at(forecast_day)[1] * (.5 if taper else 1.))
    weekly_ceiling = min(sum(available), weekly_minutes * sum(weighted_factors) / max(1., sum(available)))
    remaining = weekly_ceiling
    if start_date == today:
        remaining = max(0., remaining - sum(float(a.get("duration_min") or 0.) for a in source.get("activities", []) if a["date"] == today.isoformat()))
    all_sources_known = not limited and missing_days == 0
    forecast_known = all_sources_known
    result_days = []
    existing_in_draft = [a for a in source.get("activities", []) if start_date.isoformat() <= a["date"] <= end_date.isoformat()]
    sessions = len(existing_in_draft)
    key_sessions = sum(any(z.get("raw_time_min", 0) >= 5 and z["zone"] in {"Z3", "Z4", "Z5"}
                           for z in a.get("zones", [])) for a in existing_in_draft)
    forecast_rows = deepcopy(rows)
    recent_key_dates = [date.fromisoformat(a["date"]) for a in source.get("activities", [])
                        if a["date"] <= today.isoformat() and any(
                            z.get("raw_time_min", 0) >= 5 and z["zone"] in {"Z3", "Z4", "Z5"}
                            for z in a.get("zones", []))]
    last_key_day = max(recent_key_dates) if recent_key_dates else None
    for offset in range(7):
        day = start_date + timedelta(days=offset)
        key = day.isoformat()
        period, taper = _phase(periodization, day)
        recovery_before = recovery_v2.simulate(forecast_rows, configs["zones"], target=day)
        ready = {r["zone"]: r["readiness_percent"] for r in recovery_before["current"]}
        day_meso_week, day_meso_factor = meso_at(day)
        budgets = _budgets(forecast_rows, day, taper, day_meso_factor)
        visible_ready = {z: _round(ready[z]) if forecast_known else None for z in COMPONENTS}
        item = {"date": key, "status": "REST", "period": period, "taper": taper,
                "session": None, "readiness_before": visible_ready, "readiness_after": dict(visible_ready),
                "load_budget": {"remaining_weekly_minutes": _round(max(0., remaining)), "components": budgets,
                                "mesocycle_week_index": day_meso_week, "mesocycle_factor": day_meso_factor},
                "rejected_alternatives": [], "explanation": "Почивка; не е необходимо да се запълва всяка свободна минута."}
        events_today = [e for e in events if str(e["start_date"]) <= key <= str(e["end_date"])]
        existing = [a for a in actual_activities if a.get("local_date") == key]
        if not existing:
            existing = [a for a in source.get("activities", []) if a["date"] == key]
        known_actual_load = any(r["date"] == key and r["effective_load"] > 0 for r in rows)
        if existing:
            item.update(status="EXISTING_ACTIVITY", explanation="Има реално изпълнена активност. Не се добавя втора планирана доза за същия ден.",
                        activity_refs=[a.get("activity_ref") for a in existing])
        elif known_actual_load:
            item.update(status="EXISTING_ACTIVITY", explanation="За деня има реален приравнен товар, но липсва пълно описание на сесията. Не се добавя и не се заменя тренировъчна доза.", activity_refs=[])
            blocked = True
            warnings.append(_warning("ACTUAL_LOAD_WITHOUT_SESSION_METADATA", "Има реален товар без достатъчно данни за сесията. Нужен е обновен анализ преди продължаване на проекта."))
        elif blocked:
            item.update(status="REVIEW_REQUIRED", explanation="Липсва надеждна основа за дозиране. Обновете данните и прегледайте предупрежденията.")
        elif not period:
            item.update(status="UNAVAILABLE", explanation="Денят е извън зададения период на програмата.")
        elif any(e["event_type"] in {"MAIN_RACE", "CONTROL_RACE", "TEST"} for e in events_today):
            item.update(status="RACE", readiness_after={z: None for z in COMPONENTS},
                        explanation="Стартът/тестът е фиксиран в календара. Не се добавя друга тренировка; товарът му изисква реален запис.")
            # Race load is unknown; every subsequent day must be recalculated
            # after import. A zero race dose would falsely release hard work.
            blocked = True
            forecast_known = False
            warnings.append(_warning("RACE_LOAD_PENDING", "След старт или тест следващите дни изискват преизчисление от реалното натоварване."))
        elif any(e["event_type"] == "UNAVAILABLE" for e in events_today) or available[day.weekday()] <= 0:
            item.update(status="UNAVAILABLE", explanation="Почивен или недостъпен ден според календара и предпочитанията.")
        elif sessions >= session_limit:
            item["explanation"] = "Достигнат е предпочитаният брой сесии за седемдневния проект."
        else:
            selected_accents = _accents(period, accents)
            choices = []
            for method in METHODS:
                if sport not in method["sports"]:
                    continue
                z = method["zone"]
                rejection = None
                if period not in method["periods"]:
                    rejection = ("PERIOD_NOT_SUPPORTED", "Методът не е включен в този период.")
                elif limited and method["purpose"] != "RECOVERY":
                    rejection = ("INSUFFICIENT_HISTORY", "При ограничена история са разрешени само леки ограничени предложения.")
                elif z == "Z3" and (key_sessions >= key_limit or (last_key_day and (day - last_key_day).days < 2)):
                    rejection = ("KEY_SESSION_LIMIT", "Достигнат е лимитът или липсва достатъчно разстояние между ключови сесии.")
                elif z == "Z3" and preferences.get("intensity_days") and day.weekday() not in preferences["intensity_days"]:
                    rejection = ("INTENSITY_DAY_PREFERENCE", "Денят не е избран за интензивна работа.")
                elif ready[z] < 90.:
                    rejection = ("RECOVERY_BELOW_90", f"Прогнозната готовност за {z} е {_round(ready[z])}%, под 90%.")
                elif not limited and budgets[z]["target_weekly_effective"] <= 0:
                    rejection = ("NO_COMPONENT_TARGET", "Липсва установена компонентна база. Въвеждането на нов развиващ товар изисква отделна треньорска цел.")
                elif z != "Z1" and ready["Z1"] < 90.:
                    rejection = ("WARMUP_NOT_READY", "Не е възстановен компонентът за загрявката и разпускането.")
                if rejection:
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": rejection[0], "reason": rejection[1]})
                    continue
                evidence = capacity_for(method, settings, speed, context, today, profile.get("allow_expert_fallback", True))
                if evidence is None:
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": "CAPACITY_UNAVAILABLE", "reason": "Няма подкрепена индивидуална оценка, а експертният резерв е изключен."})
                    continue
                purpose = method["purpose"]
                if purpose == "BUILDING" and (z not in selected_accents or taper):
                    purpose = "MAINTENANCE"
                fraction = profile.get("maintenance_fraction", .3) if purpose in {"MAINTENANCE", "RECOVERY"} else profile.get("reentry_fraction", .4) if period == "RE_ENTRY" else profile.get("building_fraction", .5)
                requested = evidence["capacity_minutes"] * fraction
                overhead = method["warmup_min"] + method["cooldown_min"] + method.get("recovery_min", 0.)
                limits = [{"code": "METHOD_WORK_CAP", "limit_minutes": method["max_work_min"]},
                          {"code": "DAILY_AVAILABLE_WORK", "limit_minutes": max(0., available[day.weekday()] - overhead)},
                          {"code": "REMAINING_WEEKLY_WORK", "limit_minutes": max(0., remaining - overhead)}]
                if purpose == "RECOVERY" or limited:
                    limits.append({"code": "LOW_ABSOLUTE_RECOVERY_CAP", "limit_minutes": profile.get("recovery_session_cap_min", 30.)})
                if taper:
                    # Taper is an intentional reduction, never a 7/40 deficit
                    # to refill. Its volume cap applies even mid-draft.
                    limits.append({"code": "TAPER_DAILY_WORK_CAP", "limit_minutes": max(0., weekly_minutes / max(1, sum(v > 0 for v in available)) * .5 - overhead)})
                work = math.floor(min(requested, *(r["limit_minutes"] for r in limits)) * 2) / 2
                if work < method["min_work_min"]:
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": "INSUFFICIENT_DOSE_BUDGET", "reason": "Оставащият бюджет е под минималната работна доза на метода."})
                    continue
                blocks = _blocks(method, work, evidence, settings)
                direct, effective, technical = _canonical_load(blocks, settings, forecast_rows, day)
                if not limited:
                    def fits_component_budget(candidate):
                        return all(candidate[z] <= budgets[z]["deficit_effective"] + .001 for z in COMPONENTS)
                    if not fits_component_budget(effective):
                        lo, hi = 0., work
                        for _ in range(24):
                            midpoint = (lo + hi) / 2
                            trial_blocks = _blocks(method, midpoint, evidence, settings)
                            _, trial_effective, _ = _canonical_load(trial_blocks, settings, forecast_rows, day)
                            if fits_component_budget(trial_effective):
                                lo = midpoint
                            else:
                                hi = midpoint
                        work = math.floor(lo * 2) / 2
                        limits.append({"code": "ROLLING_7_40_COMPONENT_BUDGET", "limit_minutes": _round(work)})
                        if work < method["min_work_min"]:
                            item["rejected_alternatives"].append({"method_id": method["id"], "code": "COMPONENT_BUDGET_EXHAUSTED", "reason": "Приравненият товар, включително загрявката и разлива, надхвърля оставащия компонентен бюджет 7/40."})
                            continue
                        blocks = _blocks(method, work, evidence, settings)
                        direct, effective, technical = _canonical_load(blocks, settings, forecast_rows, day)
                after_rows = _with_forecast_day(forecast_rows, day, effective)
                after = recovery_v2.simulate(after_rows, configs["zones"], target=day)
                near_races = [e for e in events if e["event_type"] in {"MAIN_RACE", "CONTROL_RACE"}
                              and key < str(e["start_date"]) <= (day + timedelta(days=7)).isoformat()]
                race_conflict = False
                for race in near_races:
                    race_recovery = recovery_v2.simulate(after_rows, configs["zones"], target=date.fromisoformat(str(race["start_date"])))
                    if any(r["readiness_percent"] < 90. for r in race_recovery["current"] if r["zone"] != "STR"):
                        race_conflict = True
                        break
                if race_conflict:
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": "RACE_RECOVERY_CONFLICT", "reason": "Прогнозното възстановяване след тази доза не достига 90% преди близък старт."})
                    continue
                total = sum(b["duration_min"] for b in blocks)
                evidence.update(fraction=fraction, requested_work_minutes=_round(requested), prescribed_work_minutes=_round(work),
                                applied_fraction=_round(work / evidence["capacity_minutes"]), dose_reduced=work + .01 < requested,
                                limits=limits, technical_spill_reference={z: _round(v) for z, v in technical.items()},
                                technical_spill_reference_role="CANONICAL_E_ONLY_NOT_DOSE_CAPACITY",
                                source_method_id=method["source_id"], source_method_version=method["source_version"],
                                explanation="Капацитетът е от индивидуалната крива, когато целта е подкрепена от тестове и ТИ; иначе от видимата експертна референция. Делът на метода и дневният/седмичният бюджет ограничават работата. Recovery е отделна проверка, а не множител 0,90.")
                normalized_deficit = budgets[z]["deficit_effective"] / max(1., budgets[z]["target_weekly_effective"])
                score = normalized_deficit + (1. if z in selected_accents else 0.)
                if purpose == "RECOVERY":
                    score -= 1.
                if purpose == "BUILDING":
                    score += .1
                choices.append((score, method["id"], {"method_id": method["id"], "title": method["title"],
                                "sport": sport, "zone": z, "purpose": purpose, "blocks": blocks,
                                "main_work_minutes": _round(work), "total_minutes": _round(total),
                                "canonical_effective_load": {z: _round(v) for z, v in effective.items()},
                                "direct_equivalent_minutes": {z: _round(v) for z, v in direct.items()},
                                "dose_evidence": evidence}, after_rows, after))
            if choices:
                choices.sort(key=lambda c: (-c[0], c[1]))
                _, _, session, forecast_rows, after = choices[0]
                after_ready = {r["zone"]: _round(r["readiness_percent"]) for r in after["current"]}
                item.update(status="TRAINING", session=session,
                            readiness_after=after_ready if forecast_known else {z: None for z in COMPONENTS},
                            explanation=f"{session['title']}: {session['main_work_minutes']:g} минути основна работа, {session['total_minutes']:g} минути общо. Избор според периода, компонентния дефицит и готовността.")
                item["rejected_alternatives"] += [{"method_id": c[1], "code": "LOWER_CURRENT_PRIORITY", "reason": "Допустим метод с по-нисък текущ приоритет; не се добавя втора пълна доза."} for c in choices[1:]]
                remaining -= session["total_minutes"]
                sessions += 1
                if session["zone"] == "Z3":
                    key_sessions += 1
                    last_key_day = day
            else:
                item["explanation"] = "Няма метод с едновременно подходяща доза, бюджет и готовност. Почивката е допустим резултат."
        result_days.append(item)
        if item["session"] is None and item["status"] in {"REST", "UNAVAILABLE"}:
            forecast_rows = _with_forecast_day(forecast_rows, day, {})
    parameters = {"version": PARAMETER_VERSION, "status": "COACH_HEURISTICS_FOR_REVIEW",
                  "recovery_mode": "LOAD_ONLY", "ready_threshold_percent": 90,
                  "building_fraction": profile.get("building_fraction", .5), "maintenance_fraction": profile.get("maintenance_fraction", .3),
                  "reentry_fraction": profile.get("reentry_fraction", .4), "recovery_session_cap_min": profile.get("recovery_session_cap_min", 30),
                  "weekly_volume_source": volume_source, "baseline_weekly_minutes": _round(weekly_minutes),
                  "weekly_minutes_ceiling": _round(weekly_ceiling), "mesocycle_week_index": meso_week,
                  "mesocycle_factor": meso_factor, "mesocycle_length_weeks": meso_length,
                  "mesocycle_factor_policy": "FIRST_0.96_MIDDLE_MAX_1.0_LAST_0.78", "automatic_volume_progression": False,
                  "taper_volume_factor": .5, "minimum_test_anchors": 2, "minimum_zone_index_activities": 3,
                  "max_index_age_days": 14, "history_days_for_unrestricted_draft": 20,
                  "z1_working_band_width_bpm": Z1_WORKING_BAND_WIDTH_BPM,
                  "recovery_config_revision": configs["expected_revision"], "recovery_settings": configs["zones"],
                  "expert_continuous_capacity_upper_edge_min": {z: sum(r) / 120 for z, r in hr_speed.TMAX_RANGES_S.items()},
                  "canonical_effective_load": {k: fresh_parameters()[k] for k in ("cascade", "spill_fraction", "spill_threshold_fraction")}}
    provenance = {"generation_id": envelope.get("generation_id"), "revision": envelope.get("revision"),
                  "as_of": as_of, "history_days": history_days, "quality": quality,
                  "component_history_days": component_history_days,
                  "speed_model_version": speed.get("model_version") if speed else None,
                  "recovery_model_version": recovery_v2.VERSION, "method_catalog_version": METHODS_VERSION,
                  "speed_active_test_keys": speed.get("active_test_keys", []) if speed else [],
                  "readiness_known": all_sources_known, "known_history_only_forecast": not all_sources_known,
                  "snapshot_fingerprint": _hash(snapshot), "settings_fingerprint": _hash({"bounds": settings.zone_bounds_bpm, "hrmax": settings.hrmax_bpm, "timezone": settings.timezone})}
    fingerprint = _hash({"engine": VERSION, "source": provenance, "profile": profile, "events": events,
                         "preferences": preferences, "accents": accents, "parameters": parameters,
                         "speed": speed, "start_date": start_date.isoformat()})
    all_blocked = all(d["status"] == "REVIEW_REQUIRED" for d in result_days)
    return {"schema_version": "planning-draft-v1", "engine_version": VERSION, "fingerprint": fingerprint,
            "status": "BLOCKED" if all_blocked else "LIMITED_DRAFT" if limited or blocked else "DRAFT",
            "generated_at": now.isoformat(), "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
            "source": provenance, "periodization": periodization, "days": result_days,
            "parameters": parameters, "warnings": warnings, "catalog": catalog(),
            "summary": {"sessions": sessions, "key_sessions": key_sessions,
                        "planned_minutes": _round(sum(d["session"]["total_minutes"] for d in result_days if d["session"])),
                        "unused_weekly_minutes": _round(max(0., remaining)), "requires_review": True}}
