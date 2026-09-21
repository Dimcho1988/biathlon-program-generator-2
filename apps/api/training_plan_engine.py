"""Reviewable seven-day physical-training drafts using the current model stack.

Versioned candidates for the active planner; no wellness multiplier or legacy planner.
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

from biathlon import hr_speed, recovery_v2, speed_duration, training_targets
from biathlon.constants import COMPONENTS, fresh_parameters
from biathlon.equivalence import DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM
from biathlon.periodization import build_periodization
from biathlon.physiology import _causal_tref, effective_from_direct_vector, linear_equivalence_coefficient
from biathlon.training_methods import METHODS, EXERCISES, VERSION as METHODS_VERSION, catalog, resolved_methods
from . import model_service

VERSION = "training-management-v2"
PARAMETER_VERSION = "management-parameters-v2"
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


def _taper_factor(periodization, day):
    return min([p.get("volume_factor", .5) for p in periodization.get("taper_windows", [])
                if p["start_date"] <= day.isoformat() <= p["end_date"]] or [1.])


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
    if method["structure"] == "METABOLIC_INTERVALS":
        p = method["interval_profile"]
        capacity = p["continuous_capacity_min"]
        source = "COACH_EFFORT_CAPACITY"
        if p.get("target_speed_kmh") and p.get("speed_basis") == "FLAT_EQUIVALENT" and speed:
            keys = set(speed.get("active_test_keys", []))
            anchors = [e["payload"] for e in speed.get("tests", []) if e.get("entry_key") in keys]
            if len({t["duration_s"] for t in anchors}) >= 2 and all(t.get("maximal") and t.get("test_mode", "STRICT") == "STRICT" for t in anchors):
                try:
                    curve = speed_duration.calibrated(anchors)
                    seconds = curve.inverse(p["target_speed_kmh"] / 3.6)
                    if min(t["duration_s"] for t in anchors) <= seconds <= max(t["duration_s"] for t in anchors):
                        capacity, source = seconds / 60, "SPEED_DURATION"
                except ValueError:
                    pass
        if source == "COACH_EFFORT_CAPACITY" and not 0 <= (today - date.fromisoformat(p["assessed_on"])).days <= 42:
            return None
        if p["work_seconds"] >= capacity * 60:
            return None
        return {"capacity_source": source, "capacity_minutes": capacity,
                "target_hr_bpm": None, "target_speed_kmh": p.get("target_speed_kmh"),
                "model_version": speed["model_version"] if source == "SPEED_DURATION" else "individual-effort-profile-v2", "fallback_reasons": [],
                "effort_profile": deepcopy(p), "hr_role": "OBSERVATION_ONLY",
                "speed_role": p.get("speed_basis", "ACTUAL"), "target_zone_working_bounds_bpm": None}
    if zone == "STR":
        return {"capacity_source": "STRENGTH_METHOD_PROFILE", "capacity_minutes": method["max_work_min"],
                "target_hr_bpm": None, "target_speed_kmh": None,
                "model_version": "strength-circuit-v2", "fallback_reasons": [],
                "capacity_role": "METHOD_WORK_LIMIT_NOT_METABOLIC_TREF"}
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
            "supported_test_duration_s": [min(t["duration_s"] for t in tests), max(t["duration_s"] for t in tests)] if tests else None,
            "speed_role": "FLAT_EQUIVALENT_REFERENCE_NOT_TERRAIN_PACE"}


def _block(kind, label, zone, minutes, hr, instructions, *, speed=None, repetition=None):
    seconds = round(float(minutes) * 60., 3)
    return {"kind": kind, "label": label, "zone": zone, "duration_min": seconds / 60., "duration_s": seconds,
            "target_hr_bpm": _round(hr) if hr is not None else None,
            "target_speed_kmh": _round(speed) if speed is not None else None,
            "repetition": repetition, "instructions": instructions}


def _blocks(method, work, evidence, settings):
    easy_low, easy_high = _working_band(settings, "Z1")
    easy = easy_low + .35 * (easy_high - easy_low)
    blocks = []
    if method["warmup_min"]:
        blocks.append(_block("WARMUP", "Загрявка", "Z1", method["warmup_min"], easy, "Плавно леко движение в Z1."))
    structure = method["structure"]
    if structure in {"STRENGTH_CIRCUIT", "AEROBIC_STRENGTH"}:
        circuits = int(work // 3) if structure == "STRENGTH_CIRCUIT" else 1
        if structure == "AEROBIC_STRENGTH":
            blocks.append(_block("WORK", "Леко аеробно движение", "Z1", work - 3, easy,
                                 "Контролирана лека работа; запази резерв за общата сила."))
        for circuit in range(circuits):
            for index, exercise in enumerate(EXERCISES):
                b = _block("WORK", f"Кръг {circuit+1}: {exercise}", "STR", 1/3, None, method["instructions"])
                b.update(strength_type="STR_END", reserve_repetitions=3, work_seconds=20)
                blocks.append(b)
                if index < len(EXERCISES) - 1:
                    b = _block("TRANSITION", "Смяна на упражнението", "STR", .5, None, "30 секунди преход; включени веднъж в общата силова експозиция.")
                    b["strength_type"] = "STR_END"
                    blocks.append(b)
            if circuit < circuits - 1:
                b = _block("RECOVERY", "Почивка между кръговете", "STR", 2, None, "Две минути почивка. Не добавяй повторения.")
                b["strength_type"] = "STR_END"
                blocks.append(b)
    elif structure in {"METABOLIC_INTERVALS", "THRESHOLD_HIGH"}:
        p = method["interval_profile"]
        if structure == "THRESHOLD_HIGH":
            blocks.append(_block("WORK", "Контролирана прагова част", "Z3", work,
                                 evidence["target_hr_bpm"], "Запази резерв; това е част от общата доза.", speed=evidence["target_speed_kmh"]))
            blocks.append(_block("RECOVERY", "Лек преход", "Z1", 3, easy, "Три минути леко движение преди бързите части."))
            high_work = evidence["combination_high_work_cap"] * min(1., work / evidence["primary_requested_work"])
        else:
            high_work = work
        reps = min(p["max_repetitions"], int((high_work * 60 + 1e-6) // p["work_seconds"]))
        if reps < p["min_repetitions"]:
            return []
        for rep in range(reps):
            b = _block("WORK", f"Отсечка {rep+1}/{reps}", p["zone"], p["work_seconds"] / 60,
                       None, method["instructions"], speed=p.get("target_speed_kmh"), repetition=rep+1)
            b.update(primary_control="EFFORT_AND_QUALITY", load_estimate="ZONE_UPPER_REFERENCE_NOT_MEASURED_HR",
                     reserve_repetitions=p["reserve_repetitions"], speed_basis=p.get("speed_basis", "ACTUAL"))
            blocks.append(b)
            if rep < reps - 1:
                blocks.append(_block("RECOVERY", "Леко движение между отсечките", "Z1", p["recovery_seconds"] / 60,
                                     easy, "Използвай цялата предписана активна почивка; без допълнителна пауза след последната отсечка."))
    elif structure == "ALTERNATING":
        z2 = evidence
        z1 = evidence["secondary_capacity"]
        for i in range(2):
            for zone, cap in (("Z1", z1), ("Z2", z2)):
                blocks.append(_block("WORK", f"Цикъл {i+1}: {zone}", zone, work / 4, cap["target_hr_bpm"],
                                     method["instructions"], speed=cap["target_speed_kmh"]))
    elif structure == "TWO_REPETITIONS":
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
        # Effort-led intervals carry a planning estimate, never a fabricated
        # measured HR or an instruction to chase the HR target.
        load_hr = block["target_hr_bpm"] if block["target_hr_bpm"] is not None else high
        coefficient = linear_equivalence_coefficient(load_hr, low, high,
                                                     DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM, is_z5=z == "Z5")
        direct[z] += block["duration_min"] * coefficient
    lower = (day - timedelta(days=40)).isoformat()
    upper = day.isoformat()
    technical = {z: _causal_tref(z, pd.Series([r["effective_load"] for r in rows
                    if r["zone"] == z and lower <= r["date"] < upper], dtype=float), parameters["base_loads"])
                 for z in COMPONENTS}
    vector = effective_from_direct_vector(direct, technical, parameters)
    return direct, dict(zip(COMPONENTS, map(float, vector))), technical


def _budgets(rows, day, taper=False, mesocycle_factor=1., *, actual_rows=None, targets=None):
    end = (day - timedelta(days=1)).isoformat()
    first40 = (day - timedelta(days=40)).isoformat()
    first7 = (day - timedelta(days=7)).isoformat()
    parameters = fresh_parameters()
    result = {}
    for z in COMPONENTS:
        previous = [r for r in (actual_rows if actual_rows is not None else rows) if r["zone"] == z and first40 <= r["date"] <= end]
        recent = [r for r in previous if r["date"] >= first7]
        mean40 = sum(r["effective_load"] for r in previous) / len(previous) if previous else 0.
        mean7 = sum(r["effective_load"] for r in recent) / len(recent) if recent else 0.
        previous50 = [r for r in rows if r["zone"] == z and
                      (day - timedelta(days=50)).isoformat() <= r["date"] <= end]
        mean50 = sum(r["effective_load"] for r in previous50) / len(previous50) if previous50 else 0.
        base = max(parameters["base_loads"][z], .5 * mean50)
        target = targets[z]["target"] if targets is not None else 7 * mean40 * mesocycle_factor * (.5 if taper else 1.)
        # Candidate day's prospective seven-day window: the oldest day from
        # the completed 7-day window has rolled out before adding today's dose.
        actual = sum(r["effective_load"] for r in rows if r["zone"] == z and
                     (day - timedelta(days=6)).isoformat() <= r["date"] <= day.isoformat())
        result[z] = {"e7_daily": _round(mean7), "e40_daily": _round(mean40),
                     "index_7_40": _round((base + mean7) / (base + mean40)),
                     "target_weekly_effective": _round(target), "rolling_7d_effective": _round(actual),
                     "deficit_effective": _round(max(0., target - actual)),
                     "target_basis": targets[z]["basis"] if targets is not None else "HALF_C40_TAPER_CEILING" if taper else "C40_MAINTENANCE_REFERENCE",
                     "goal_evidence": targets[z] if targets is not None else None,
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


def _long_term_outlook(profile, periodization, reference, accents, preferences, rows, today, limited):
    """Read-only target envelope using the same goals as the daily planner.

    No synthetic sessions or future recovery. Ratios use the current actual
    C40 and B50, frozen explicitly; they are targets, not forecast 7/40 values.
    Camps are calendar context, never an automatic permission to add load.
    """
    start = max(today, date.fromisoformat(profile["program_start"]))
    end = date.fromisoformat(profile["program_end"])
    anchor = date.fromisoformat(str(preferences.get("mesocycle_anchor_date", profile["program_start"])))
    length = preferences.get("mesocycle_length_weeks", 4)
    baseline = _budgets(rows, today)
    base_loads = fresh_parameters()["base_loads"]
    actual_base = {}
    for z in COMPONENTS:
        r50 = [r["effective_load"] for r in rows if r["zone"] == z and
               (today - timedelta(days=50)).isoformat() <= r["date"] < today.isoformat()]
        r40 = [r["effective_load"] for r in rows if r["zone"] == z and
               (today - timedelta(days=40)).isoformat() <= r["date"] < today.isoformat()]
        actual_base[z] = {"b50": max(base_loads[z], .5 * sum(r50) / len(r50)) if r50 else base_loads[z],
                          "c40": sum(r40) / len(r40) if r40 else 0., "known": len(r40) >= 20}
    weeks = []
    day = start
    while day <= end:
        left, right = day, min(day + timedelta(days=6), end)
        targets = {z: [] for z in COMPONENTS}
        phases, selected, meso_weeks = [], [], []
        while day <= right:
            period, taper = _phase(periodization, day)
            week = max(0, (day - anchor).days // 7) % length
            focus = _accents(period, accents)
            goals = training_targets.component_targets(reference, profile, focus, week, length,
                                                        period, taper, limited, _taper_factor(periodization, day))
            for z in COMPONENTS:
                targets[z].append(goals[z]["target"])
            phases.append(period)
            selected.extend(focus)
            meso_weeks.append(week + 1)
            day += timedelta(days=1)
        components = {}
        for z in COMPONENTS:
            target = sum(targets[z]) / len(targets[z])
            known = actual_base[z]["known"] or z in profile.get("component_targets_weekly", {})
            base = actual_base[z]
            components[z] = {"target_weekly_effective": _round(target) if known else None,
                             "target_index_7_40": _round((base["b50"] + target / 7) / (base["b50"] + base["c40"])) if base["known"] else None}
        weeks.append({"start_date": left.isoformat(), "end_date": right.isoformat(),
                      "days": (right - left).days + 1, "phases": list(dict.fromkeys(phases)),
                      "accents": list(dict.fromkeys(selected)), "mesocycle_weeks": list(dict.fromkeys(meso_weeks)),
                      "components": components})
    return {"schema_version": "training-outlook-v1", "as_of": today.isoformat(),
            "basis": "CURRENT_ACTUAL_REFERENCE_FROZEN", "targets_version": training_targets.VERSION,
            "reference_cutoff": reference["cutoff"], "limited": limited,
            "readiness_forecast": False, "automatic_camp_load_increase": False,
            "baseline": {z: {**actual_base[z], "actual_index_7_40": baseline[z]["index_7_40"] if actual_base[z]["known"] else None} for z in COMPONENTS},
            "weeks": weeks}


def generate_plan(repository, alias: str, profile: dict, *, start_date: date, now: datetime | None = None,
                  decisions: dict | None = None, locked_day: dict | None = None) -> dict:
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
    end_date = min(start_date + timedelta(days=6), program_end)
    horizon = (end_date - start_date).days + 1
    decisions = decisions or {}
    calendar_settings = _read_optional(repository, "athlete_planning_calendar", alias) or {"events": []}
    events = calendar_settings.get("events", [])
    events = [*events, *[{"event_type": "UNAVAILABLE", "start_date": d, "end_date": d,
                         "event_id": f"management-{d}", "name": choice["action"]}
                        for d, choice in decisions.items() if choice["action"] in {"SKIP", "REST"}]]
    preferences = _read_optional(repository, "athlete_planning_profile", alias) or {}
    accents = _read_optional(repository, "athlete_mesocycle_accent_preferences", alias)
    periodization = build_periodization(program_start, program_end, events,
                                        reentry_days_override=profile.get("reentry_days"),
                                        taper_days=profile.get("taper_days", 7), transition_days=profile.get("transition_days", 0))
    envelope = repository.active_activity_calendar(alias, today - timedelta(days=89), end_date) or {}
    snapshot = envelope.get("snapshot_payload") or {}
    source = snapshot.get("load_history") or {}
    rows = _daily_rows(source, today)
    actual_activities = [a for a in envelope.get("activities", []) if a.get("local_date", "") <= today.isoformat()]
    configs = model_service.ModelStore(repository).config(alias)
    warnings = list(periodization.get("warnings", []))
    warnings += [_warning("DRAFT_REQUIRES_COACH_REVIEW", "Проект за треньорски преглед; не е активирана програма."),
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
    methods = resolved_methods(profile)
    warnings.append(_warning("VERSIONED_COACHING_RULES", "Целите и прогресията използват видими начални треньорски правила. Нисък стрес сам по себе си не увеличава товара."))
    if not profile.get("race_duration_min"):
        warnings.append(_warning("RACE_DURATION_MISSING", "Добавете очакваната продължителност на основната дисциплина за по-точна специфична работа."))
    for missing in catalog(profile)["disabled"]:
        warnings.append(_warning("METHOD_PROFILE_" + missing["component"], missing["reason"]))
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
    reserved_race_days = {str(e["start_date"]) for e in events if e["event_type"] in {"MAIN_RACE", "CONTROL_RACE", "TEST"}
                          and start_date.isoformat() <= str(e["start_date"]) <= end_date.isoformat()
                          and not any(a.get("date") == str(e["start_date"]) for a in source.get("activities", []))}
    key_limit = max(0, key_limit - len(reserved_race_days))
    meso_anchor = date.fromisoformat(str(preferences.get("mesocycle_anchor_date", profile["program_start"])))
    meso_length = preferences.get("mesocycle_length_weeks", 4)
    def meso_at(day):
        week = max(0, (day - meso_anchor).days // 7) % meso_length
        period, _ = _phase(periodization, day)
        development = not limited and period in {"GENERAL_PREPARATION", "SPECIAL_PREPARATION"}
        return week, training_targets.cycle_factor(week, meso_length, profile.get("progression_percent", 5), development=development)
    target_reference = training_targets.development_reference(rows, today, meso_anchor, meso_length)
    meso_week, meso_factor = meso_at(start_date)
    weighted_factors = []
    for i in range(horizon):
        forecast_day = start_date + timedelta(days=i)
        phase, taper = _phase(periodization, forecast_day)
        reserved = any(e["event_type"] in {"MAIN_RACE", "CONTROL_RACE", "TEST", "UNAVAILABLE"}
                       and str(e["start_date"]) <= forecast_day.isoformat() <= str(e["end_date"]) for e in events)
        weighted_factors.append(0. if reserved or not phase else
                                available[forecast_day.weekday()] * meso_at(forecast_day)[1] * _taper_factor(periodization, forecast_day))
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
    if locked_day and locked_day.get("session") and locked_day["session"]["zone"] in {"Z3", "Z4", "Z5", "STR"}:
        last_key_day = date.fromisoformat(locked_day["date"])
        key_sessions += 1
    activation_eligible = all_sources_known and not blocked
    # Reserve scarce key-session slots before spending optional easy volume.
    # Actual Recovery is still checked chronologically, including easy days.
    key_slots = []
    for offset in range(horizon):
        d = start_date + timedelta(days=offset)
        period, taper = _phase(periodization, d)
        if limited or period not in {"GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"} or taper:
            continue
        if available[d.weekday()] < 40 or (preferences.get("intensity_days") and d.weekday() not in preferences["intensity_days"]):
            continue
        if any(e["event_type"] in {"MAIN_RACE", "CONTROL_RACE", "TEST", "UNAVAILABLE"} and
               str(e["start_date"]) <= d.isoformat() <= str(e["end_date"]) for e in events):
            continue
        if any(a.get("date") == d.isoformat() for a in source.get("activities", [])):
            continue
        previous_key = key_slots[-1] if key_slots else last_key_day
        if previous_key and (d - previous_key).days < 2:
            continue
        if len(key_slots) < max(0, key_limit - key_sessions):
            key_slots.append(d)
    for offset in range(horizon):
        day = start_date + timedelta(days=offset)
        key = day.isoformat()
        period, taper = _phase(periodization, day)
        recovery_before = recovery_v2.simulate(forecast_rows, configs["zones"], target=day)
        ready = {r["zone"]: r["readiness_percent"] for r in recovery_before["current"]}
        day_meso_week, day_meso_factor = meso_at(day)
        selected_accents = _accents(period, accents)
        goals = training_targets.component_targets(target_reference, profile, selected_accents, day_meso_week,
                                                   meso_length, period, taper, limited, _taper_factor(periodization, day))
        budgets = _budgets(forecast_rows, day, taper, day_meso_factor, actual_rows=rows, targets=goals)
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
            activation_eligible = False
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
            if key in decisions:
                item.update(status="SKIPPED" if decisions[key]["action"] == "SKIP" else "REST",
                            explanation="Отбелязано пропускане без наваксване." if decisions[key]["action"] == "SKIP" else "Почивка по индивидуално решение.")
        elif locked_day and locked_day["date"] == key and locked_day.get("session"):
            session = deepcopy(locked_day["session"])
            _, effective, _ = _canonical_load(session["blocks"], settings, forecast_rows, day)
            if any(ready[z] < 90 or effective[z] > budgets[z]["deficit_effective"] + .001 for z in COMPONENTS if effective[z] > 0) or session["total_minutes"] > remaining + .001:
                item.update(status="REVIEW_REQUIRED", explanation="Новите данни изискват преглед на днешната вече утвърдена задача.")
                activation_eligible = False
            else:
                forecast_rows = _with_forecast_day(forecast_rows, day, effective)
                after = recovery_v2.simulate(forecast_rows, configs["zones"], target=day)
                item.update(session=session, status="TRAINING", locked=True,
                            readiness_after={r["zone"]: _round(r["readiness_percent"]) for r in after["current"]},
                            explanation="Днешната утвърдена задача е запазена; адаптират се следващите дни.")
                remaining = max(0., remaining - session["total_minutes"])
                sessions += 1
        elif sessions >= session_limit:
            item["explanation"] = "Достигнат е предпочитаният брой сесии за седемдневния проект."
        else:
            selected_accents = _accents(period, accents)
            choices = []
            for method in methods:
                method = deepcopy(method)
                race_minutes = profile.get("race_duration_min")
                if race_minutes and method["zone"] == "Z3" and period in {"SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"}:
                    blend = min(1., max(0., (race_minutes - 50.) / 20.))
                    method["position"] = .85 - .30 * blend
                if sport not in method["sports"]:
                    continue
                z = method["zone"]
                is_key = z in {"Z3", "Z4", "Z5", "STR"}
                rejection = None
                if period not in method["periods"]:
                    rejection = ("PERIOD_NOT_SUPPORTED", "Методът не е включен в този период.")
                elif limited and method["purpose"] != "RECOVERY":
                    rejection = ("INSUFFICIENT_HISTORY", "При ограничена история са разрешени само леки ограничени предложения.")
                elif is_key and (key_sessions >= key_limit or (last_key_day and (day - last_key_day).days < 2)):
                    rejection = ("KEY_SESSION_LIMIT", "Достигнат е лимитът или липсва достатъчно разстояние между ключови сесии.")
                elif is_key and preferences.get("intensity_days") and day.weekday() not in preferences["intensity_days"]:
                    rejection = ("INTENSITY_DAY_PREFERENCE", "Денят не е избран за интензивна работа.")
                elif is_key and day not in key_slots:
                    rejection = ("KEY_SLOT_RESERVED", "Ключовите сесии са разположени първи в подходящите дни; този ден остава за лека работа или почивка.")
                elif method["structure"] == "THRESHOLD_HIGH" and not 0 <= (today - date.fromisoformat(method["interval_profile"]["assessed_on"])).days <= 42:
                    rejection = ("STALE_EFFORT_CAPACITY", "Индивидуалната опора за високото усилие трябва да се обнови.")
                elif method["structure"] == "THRESHOLD_HIGH" and ready[method["interval_profile"]["zone"]] < 90.:
                    rejection = ("HIGH_BLOCK_NOT_READY", "Високият компонент на комбинацията не е възстановен.")
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
                if method["structure"] == "METABOLIC_INTERVALS":
                    p = method["interval_profile"]
                    # Maintenance selects the minimum complete variant from
                    # the same approved profile; no second role multiplier.
                    fraction = p["total_capacity_ratio"] if purpose == "BUILDING" else p["min_repetitions"] * p["work_seconds"] / (60 * evidence["capacity_minutes"])
                elif z == "STR":
                    fraction = method["min_work_min"] / method["max_work_min"] if purpose == "MAINTENANCE" else 1.
                requested = evidence["capacity_minutes"] * fraction
                if method["structure"] in {"ALTERNATING", "AEROBIC_STRENGTH"}:
                    secondary = capacity_for({**method, "zone": "Z1", "position": .35, "structure": "CONTINUOUS"},
                                             settings, speed, context, today, profile.get("allow_expert_fallback", True))
                    if secondary is None:
                        continue
                    evidence["secondary_capacity"] = secondary
                    if method["structure"] == "ALTERNATING":
                        requested = min(requested, secondary["capacity_minutes"] * fraction)
                    else:
                        requested = min(requested, secondary["capacity_minutes"] * profile.get("maintenance_fraction", .3) * .5 + 3)
                if method["structure"] == "THRESHOLD_HIGH":
                    fraction *= .5
                    requested *= .5
                    p = method["interval_profile"]
                    high_capacity = capacity_for({**method, "zone": p["zone"], "structure": "METABOLIC_INTERVALS"},
                                                 settings, speed, context, today)
                    if high_capacity is None:
                        continue
                    evidence.update(primary_requested_work=requested,
                                    combination_high_work_cap=high_capacity["capacity_minutes"] * p["total_capacity_ratio"] * .5,
                                    secondary_capacity={**high_capacity,
                                                        "zone": p["zone"], "fraction": p["total_capacity_ratio"] * .5})
                event_specificity = training_targets.specificity(method, profile.get("race_duration_min"), period)
                evidence["specificity"] = event_specificity
                overhead = method["warmup_min"] + method["cooldown_min"] + method.get("recovery_min", 0.)
                limits = [{"code": "METHOD_WORK_CAP", "limit_minutes": method["max_work_min"]},
                          {"code": "DAILY_AVAILABLE_WORK", "limit_minutes": max(0., available[day.weekday()] - overhead)},
                          {"code": "REMAINING_WEEKLY_WORK", "limit_minutes": max(0., remaining - overhead)}]
                if event_specificity["work_cap_min"] is not None:
                    limits.append({"code": "RACE_DURATION_WORK_CAP", "limit_minutes": event_specificity["work_cap_min"]})
                if not is_key:
                    future_keys = sum(d > day for d in key_slots)
                    if future_keys:
                        limits.append({"code": "RESERVE_KEY_SESSION_TIME", "limit_minutes": max(0., remaining - 40 * future_keys - overhead)})
                if purpose == "RECOVERY" or limited:
                    limits.append({"code": "LOW_ABSOLUTE_RECOVERY_CAP", "limit_minutes": profile.get("recovery_session_cap_min", 30.)})
                if taper:
                    # Taper is an intentional reduction, never a 7/40 deficit
                    # to refill. Its volume cap applies even mid-draft.
                    limits.append({"code": "TAPER_DAILY_WORK_CAP", "limit_minutes": max(0., weekly_minutes / max(1, sum(v > 0 for v in available)) * _taper_factor(periodization, day) - overhead)})
                work = math.floor(min(requested, *(r["limit_minutes"] for r in limits)) * 2) / 2
                if work < method["min_work_min"]:
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": "INSUFFICIENT_DOSE_BUDGET", "reason": "Оставащият бюджет е под минималната работна доза на метода."})
                    continue
                blocks = _blocks(method, work, evidence, settings)
                # Whole repetitions/circuits, active rests and transitions all
                # have to fit. Lower work never authorizes a longer rest.
                while work >= method["min_work_min"] and blocks and sum(b["duration_min"] for b in blocks) > min(available[day.weekday()], remaining) + .001:
                    work -= .5
                    blocks = _blocks(method, work, evidence, settings)
                if not blocks or work < method["min_work_min"]:
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": "STRUCTURE_DOES_NOT_FIT", "reason": "Цели повторения, паузи и общата доза не се побират едновременно."})
                    continue
                direct, effective, technical = _canonical_load(blocks, settings, forecast_rows, day)
                if not limited:
                    def fits_component_budget(candidate):
                        return all(candidate[z] <= budgets[z]["deficit_effective"] + .001 for z in COMPONENTS)
                    if not fits_component_budget(effective):
                        minimum_blocks = _blocks(method, method["min_work_min"], evidence, settings)
                        _, minimum_load, _ = _canonical_load(minimum_blocks, settings, forecast_rows, day)
                        if not minimum_blocks or not fits_component_budget(minimum_load):
                            item["rejected_alternatives"].append({"method_id": method["id"], "code": "COMPONENT_BUDGET_EXHAUSTED", "reason": "Няма бюджет за минималния цял вариант на метода."})
                            continue
                        lo, hi = method["min_work_min"], work
                        for _ in range(24):
                            midpoint = (lo + hi) / 2
                            trial_blocks = _blocks(method, midpoint, evidence, settings)
                            _, trial_effective, _ = _canonical_load(trial_blocks, settings, forecast_rows, day)
                            if trial_blocks and fits_component_budget(trial_effective):
                                lo = midpoint
                            else:
                                hi = midpoint
                        work = math.floor(lo * 2) / 2
                        limits.append({"code": "ROLLING_7_40_COMPONENT_BUDGET", "limit_minutes": _round(work)})
                        if work < method["min_work_min"]:
                            item["rejected_alternatives"].append({"method_id": method["id"], "code": "COMPONENT_BUDGET_EXHAUSTED", "reason": "Приравненият товар, включително загрявката и разлива, надхвърля оставащия компонентен бюджет 7/40."})
                            continue
                        blocks = _blocks(method, work, evidence, settings)
                        if not blocks:
                            item["rejected_alternatives"].append({"method_id": method["id"], "code": "MINIMUM_STRUCTURE_NOT_MET", "reason": "Бюджетът не допуска минималната цяла структура."})
                            continue
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
                if not is_key and any(
                    any(r["readiness_percent"] < 90 for r in recovery_v2.simulate(after_rows, configs["zones"], target=d)["current"] if r["zone"] in {"Z1", *selected_accents})
                    for d in key_slots if day < d <= day + timedelta(days=2)
                ):
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": "RESERVE_KEY_SESSION_RECOVERY", "reason": "Леката добавка би затруднила готовността за предстоящата ключова сесия."})
                    continue
                total = sum(b["duration_min"] for b in blocks)
                actual_work = sum(b["duration_min"] for b in blocks if b["kind"] == "WORK")
                evidence.update(fraction=fraction, requested_work_minutes=_round(requested + evidence.get("combination_high_work_cap", 0.)), prescribed_work_minutes=_round(actual_work),
                                requested_primary_work_minutes=_round(requested),
                                primary_work_budget_minutes=_round(work), applied_fraction=_round(work / evidence["capacity_minutes"]), dose_reduced=work + .01 < requested,
                                limits=limits, technical_spill_reference={z: _round(v) for z, v in technical.items()},
                                technical_spill_reference_role="CANONICAL_E_ONLY_NOT_DOSE_CAPACITY",
                                source_method_id=method["source_id"], source_method_version=method["source_version"],
                                explanation="Капацитетът е от индивидуалната крива, когато целта е подкрепена от тестове и ТИ; иначе от видимата експертна референция. Делът на метода и дневният/седмичният бюджет ограничават работата. Recovery е отделна проверка, а не множител 0,90.")
                if method["structure"] == "METABOLIC_INTERVALS":
                    evidence["explanation"] = "Устойчивостта е индивидуално зададена за описаното усилие и средство. Цели повторения и активни паузи използват общия методен бюджет. Пулсът е наблюдение; не определя усилието или дозата. Прогнозният приравнен товар използва горната зонова референция и ще бъде заменен с реалното изпълнение."
                elif z == "STR":
                    evidence["explanation"] = "Дозата е от отделния силов профил, без метаболитен Tref. Работата, преходите и почивките се отчитат веднъж в силовата експозиция; загрявката и разпускането имат собствен аеробен товар. Остават поне 3 качествени повторения в резерв."
                if method["structure"] in {"ALTERNATING", "AEROBIC_STRENGTH", "THRESHOLD_HIGH"}:
                    evidence["combination_allocation"] = "ONE_SHARED_SESSION_BUDGET_REDUCED_COMPONENT_DOSES"
                    evidence["explanation"] += " Комбинираните части споделят дозата и общото време; не получават две пълни изграждащи дози."
                normalized_deficit = budgets[z]["deficit_effective"] / max(1., budgets[z]["target_weekly_effective"])
                score = normalized_deficit + (1. if z in selected_accents else 0.)
                if purpose == "RECOVERY":
                    score -= 1.
                if purpose == "BUILDING":
                    score += .1
                if is_key and day in key_slots:
                    score += 3.
                if method["structure"] in {"ALTERNATING", "AEROBIC_STRENGTH", "THRESHOLD_HIGH"}:
                    score += .15
                if any(d.get("session", {}).get("method_id") == method["id"] for d in result_days if d.get("session")):
                    score -= .35
                choices.append((score, method["id"], {"method_id": method["id"], "title": method["title"],
                                "sport": sport, "zone": z, "purpose": purpose, "blocks": blocks,
                                "main_work_minutes": _round(actual_work), "total_minutes": _round(total),
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
                if session["zone"] in {"Z3", "Z4", "Z5", "STR"}:
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
                  "mesocycle_factor_policy": "ACTUAL_REFERENCE_BOUNDED_DEVELOPMENT_DELOAD_0.78", "automatic_volume_progression": True,
                  "progression_percent": profile.get("progression_percent", 5), "target_reference": target_reference,
                  "targets_version": training_targets.VERSION, "reserved_key_dates": [d.isoformat() for d in key_slots],
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
            "activation_eligible": activation_eligible,
            "source": provenance, "periodization": periodization, "days": result_days,
            "long_term": _long_term_outlook(profile, periodization, target_reference, accents, preferences, rows, today, limited),
            "parameters": parameters, "warnings": warnings, "catalog": catalog(profile),
            "summary": {"sessions": sessions, "key_sessions": key_sessions,
                        "planned_minutes": _round(sum(d["session"]["total_minutes"] for d in result_days if d["session"])),
                        "unused_weekly_minutes": _round(max(0., remaining)), "requires_review": True}}
