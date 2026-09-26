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

from biathlon import hr_speed, recovery_v2, speed_duration, training_targets, planning_controls, planning_history, planning_schedule, planning_allocation, load_progression, mesocycle_focus
from biathlon.constants import COMPONENTS, fresh_parameters
from biathlon.equivalence import DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM, equivalence_slope
from biathlon.periodization import build_periodization
from biathlon.physiology import _causal_tref, effective_from_direct_vector, linear_equivalence_coefficient
from biathlon.training_methods import METHODS, EXERCISES, VERSION as METHODS_VERSION, catalog, resolved_methods
from . import model_service, load_adaptation, race_duration
from .response_service import ResponseStore

VERSION = "training-management-v16"
PARAMETER_VERSION = "management-parameters-v16"
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


def _budget_rejection(method_id, effective, budgets, profile):
    blocked = [z for z in COMPONENTS if effective[z] > budgets[z]["deficit_effective"] + .001]
    manual = [z for z in blocked if z in profile.get("component_targets_weekly", {})]
    details = "; ".join(f"{z}: нужни {effective[z]:.1f}, остават {budgets[z]['deficit_effective']:.1f}" for z in blocked)
    reason = f"Няма бюджет за минималния цял вариант: {details} приравнени минути."
    if manual:
        reason += " Ръчната цел за " + ", ".join(manual) + " замества автоматичната цел от историята и 7/40."
    return {"method_id": method_id, "code": "COMPONENT_BUDGET_EXHAUSTED", "reason": reason,
            "blocking_components": blocked, "manual_target_components": manual}


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


def _add_forecast_session(rows, day, effective):
    current = {r["zone"]: r["effective_load"] for r in rows if r["date"] == day.isoformat()}
    return _with_forecast_day(rows, day, {z: current.get(z, 0.) + effective.get(z, 0.) for z in COMPONENTS})


def _candidate_load(blocks, settings, rows, day, *, technical_reference=None):
    direct, effective, technical = {z: 0. for z in COMPONENTS}, {z: 0. for z in COMPONENTS}, {}
    for slot in sorted({b.get("session_index", 1) for b in blocks}):
        d, e, technical = _canonical_load([b for b in blocks if b.get("session_index", 1) == slot], settings, rows, day, technical_reference=technical_reference)
        for z in COMPONENTS:
            direct[z] += d[z]
            effective[z] += e[z]
    return direct, effective, technical


def _session_parts(session, settings, rows, day, slot_index):
    if not session.get("double_threshold"):
        return [{**session, "slot": slot_index + 1}]
    parts = []
    for slot in (1, 2):
        blocks = [{k: v for k, v in b.items() if k != "session_index"} for b in session["blocks"] if b["session_index"] == slot]
        direct, effective, _ = _canonical_load(blocks, settings, rows, day)
        evidence = deepcopy(session["dose_evidence"])
        shared_fraction = evidence.get("applied_structure_fraction")
        part_requested = evidence["requested_primary_work_minutes"]/2 if "requested_primary_work_minutes" in evidence else evidence["requested_work_minutes"]/2
        metadata = (session.get("paired_session") or {}) if slot == 2 else {}
        if metadata:
            primary_capacity = evidence["capacity_minutes"] * evidence.get("effort_profile", {}).get("total_capacity_ratio", 1.)
            paired = evidence["paired_capacity"]
            part_requested *= paired["capacity_minutes"] * paired.get("effort_profile", {}).get("total_capacity_ratio", 1.) / primary_capacity
            evidence = {**evidence, **evidence["paired_capacity"]}
        work = _round(sum(b["duration_min"] for b in blocks if b["kind"] == "WORK"))
        evidence.update(shared_day_work_minutes=session["main_work_minutes"], shared_day_dose=True,
                        prescribed_work_minutes=work, requested_work_minutes=_round(part_requested),
                        fraction=part_requested/evidence["capacity_minutes"], applied_fraction=work/evidence["capacity_minutes"],
                        shared_day_structure_fraction=shared_fraction,
                        applied_structure_fraction=_round(_dose_usage(blocks, evidence, metadata.get("zone", session["zone"]))))
        parts.append({**session, **metadata, "slot": slot, "title": f"Прагова сесия {slot}: {metadata.get('zone', session['zone'])}", "blocks": blocks,
                      "main_work_minutes": work, "total_minutes": _round(sum(b["duration_min"] for b in blocks)), "dose_evidence": evidence,
                      "canonical_effective_load": {z: _round(v) for z, v in effective.items()},
                      "direct_equivalent_minutes": {z: _round(v) for z, v in direct.items()}})
    return parts


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
    if any(r != "INSUFFICIENT_INDEPENDENT_TEST_DURATIONS" for r in reasons) or not tests:
        return None, tests, reasons
    curve = speed_duration.calibrated(tests)
    corrections = [speed.get("zone_corrections", {}).get(z, 0.) for z in speed_duration.VOLUME_RANGES_MIN]
    curve, _ = speed_duration.adjusted(curve, tests, None, [], corrections,
                                    duration_centers=hr_speed.volume_duration_centers(settings.zone_bounds_bpm))
    predictor = hr_speed.Predictor(curve, settings.zone_bounds_bpm, settings.hrmax_bpm, speed["index_summary"])
    return predictor, tests, reasons


def capacity_for(method, settings, speed, context, today, allow_fallback=True, *, use_model_prior=False):
    """Select exactly one capacity source, without a second TI/volume factor."""
    zone = method["zone"]
    if method["structure"] == "MODEL_INTERVALS":
        if zone == "Z5":
            # HR-speed stops at the Z4/Z5 boundary. Never extrapolate a Z5
            # Tref from HR or silently inherit Z4's expert duration.
            predictor, tests, reasons = context
            if predictor is None or reasons or not speed:
                return None
            recent = (speed.get("index_window") or {}).get("last_activity_date")
            if not recent or (today-date.fromisoformat(recent)).days > 14 or (speed.get("index_summary", {}).get("Z4") or {}).get("count", 0) < 3:
                return None
            boundary_hr = settings.zone_bounds_bpm[4]
            if predictor.metadata(boundary_hr)["hr_prediction_source"] != "INDEX":
                return None
            boundary_speed = predictor.speed_for_hr(boundary_hr)
            candidates = [t for t in tests if 180 <= t["duration_s"] <= 600
                          and t["speed_kmh"] > boundary_speed
                          and t.get("day") and 0 <= (today-date.fromisoformat(t["day"])).days <= 42]
            if not candidates:
                return None
            anchor = min(candidates, key=lambda t: abs(t["duration_s"]-300))
            base = {"capacity_source": "SPEED_DURATION_TEST_ANCHOR", "capacity_minutes": anchor["duration_s"]/60,
                    "target_hr_bpm": None, "target_speed_kmh": anchor["speed_kmh"],
                    "model_version": speed["model_version"], "fallback_reasons": [],
                    "capacity_confidence": "RECENT_MAXIMAL_TEST_ABOVE_SUPPORTED_Z4_BOUNDARY",
                    "test_anchor": deepcopy(anchor), "boundary_speed_kmh": boundary_speed}
        else:
            base = capacity_for({**method, "structure": "CONTINUOUS"}, settings, speed, context, today, allow_fallback, use_model_prior=use_model_prior)
        if base is None:
            return None
        p = {**method["interval_template"], "sport": method.get("actual_sport", method["sports"][0]),
             "continuous_capacity_min": base["capacity_minutes"], "target_speed_kmh": base["target_speed_kmh"],
             "speed_basis": "FLAT_EQUIVALENT", "effort": method["instructions"]}
        if p["work_seconds"] >= base["capacity_minutes"]*60:
            return None
        return {**base, "target_hr_bpm": None, "hr_role": "OBSERVATION_ONLY", "effort_profile": p,
                "implementation_profile": method["implementation_profile"],
                "capacity_role": "MODEL_ESTIMATE_FOR_REPEATABLE_EFFORT_NOT_MEASURED_TTE"}
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
                    curve, _ = speed_duration.adjusted(curve, anchors, None, [],
                        [speed.get("zone_corrections", {}).get(z, 0.) for z in speed_duration.VOLUME_RANGES_MIN],
                        duration_centers=hr_speed.volume_duration_centers(settings.zone_bounds_bpm))
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
    prior_reasons = {"INSUFFICIENT_INDEPENDENT_TEST_DURATIONS", "OUTSIDE_OBSERVED_TEST_DURATION_SUPPORT"}
    prior_allowed = use_model_prior and allow_fallback and reasons and set(reasons) <= prior_reasons
    if (not reasons or prior_allowed) and duration is not None:
        source = "SPEED_DURATION_PRIOR" if prior_allowed else "SPEED_DURATION"
        minutes = duration / 60.
        model_version = speed["model_version"]
        if prior_allowed:
            # The individually scaled expert shape is still an estimate. Bound
            # its duration by the existing expert upper envelope at this effort.
            coeff = linear_equivalence_coefficient(target_hr, low, high, DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM)
            minutes = min(minutes, max(hr_speed.TMAX_RANGES_S[zone])/60/max(.1, coeff))
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
            "capacity_confidence": "INDIVIDUAL_TESTS_AND_RECENT_INDEX" if source == "SPEED_DURATION" else "INDIVIDUALLY_SCALED_EXPERT_SHAPE" if source == "SPEED_DURATION_PRIOR" else "EXPERT_REFERENCE",
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
    if method.get("double_threshold"):
        single = {**method, "double_threshold": False, "min_work_min": method["min_work_min"] / 2}
        half = _blocks(single, work / 2, evidence, settings)
        if not half:
            return []
        if method.get("paired_method"):
            other = method["paired_method"]
            paired = evidence["paired_capacity"]
            primary_capacity = evidence["capacity_minutes"] * evidence.get("effort_profile", {}).get("total_capacity_ratio", 1.)
            paired_capacity = paired["capacity_minutes"] * paired.get("effort_profile", {}).get("total_capacity_ratio", 1.)
            other_work = min(other["max_work_min"], work/2*paired_capacity/primary_capacity)
            if work/2 < method["single_min_work_min"] or other_work < other["min_work_min"]:
                return []
            second = _blocks(other, other_work, paired, settings)
            return ([{**b, "session_index": 1} for b in half] + [{**b, "session_index": 2} for b in second]) if second else []
        return [{**deepcopy(b), "session_index": slot} for slot in (1, 2) for b in half]
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
    elif structure in {"METABOLIC_INTERVALS", "MODEL_INTERVALS", "THRESHOLD_HIGH"}:
        p = evidence["effort_profile"] if structure == "MODEL_INTERVALS" else method["interval_profile"]
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
    elif structure == "AEROBIC_SUPPORT":
        secondary = evidence["secondary_capacity"]
        blocks.append(_block("WORK", "Лека аеробна част", "Z1", 2*work,
                             secondary["target_hr_bpm"], method["instructions"], speed=secondary["target_speed_kmh"]))
        blocks.append(_block("WORK", "Поддържаща част в Z3", "Z3", work,
                             evidence["target_hr_bpm"], method["instructions"], speed=evidence["target_speed_kmh"]))
    elif structure == "THRESHOLD_REPETITIONS":
        reps = min(8, max(2, math.ceil(work/12)))
        duration = math.floor(work/reps*2)/2
        if duration < 6:
            return []
        for rep in range(reps):
            blocks.append(_block("WORK", f"Работна част {rep+1}/{reps}", "Z3", duration,
                                 evidence["target_hr_bpm"], method["instructions"], speed=evidence["target_speed_kmh"], repetition=rep+1))
            if rep < reps-1:
                blocks.append(_block("RECOVERY", "Активна почивка", "Z1", 2, easy, "Две минути леко движение."))
    elif structure == "CRUISE_ALTERNATING":
        cycles = min(10, int(work//15))
        if cycles < 3:
            return []
        for i in range(cycles):
            for zone, minutes, cap in (("Z2", 10, evidence), ("Z1", 5, evidence["secondary_capacity"])):
                blocks.append(_block("WORK", f"Цикъл {i+1}: {zone}", zone, minutes, cap["target_hr_bpm"],
                                     method["instructions"], speed=cap["target_speed_kmh"]))
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


def _canonical_load(blocks, settings, rows, day, *, technical_reference=None):
    parameters = fresh_parameters()
    direct = {z: 0. for z in COMPONENTS}
    for block in blocks:
        z = block["zone"]
        if z == "STR":
            direct[z] += block["duration_min"]
            continue
        idx = int(z[1:]) - 1
        low, high = settings.zone_bounds_bpm[idx:idx + 2]
        if z == "Z5":
            high = settings.hrmax_bpm or high
        # Effort-led intervals carry a planning estimate, never a fabricated
        # measured HR or an instruction to chase the HR target.
        load_hr = block["target_hr_bpm"] if block["target_hr_bpm"] is not None else high
        coefficient = linear_equivalence_coefficient(load_hr, low, high,
                                                     equivalence_slope(z), is_z5=z == "Z5")
        direct[z] += block["duration_min"] * coefficient
    lower = (day - timedelta(days=40)).isoformat()
    upper = day.isoformat()
    technical = technical_reference if technical_reference is not None else {z: _causal_tref(z, pd.Series([r["effective_load"] for r in rows
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


def _goals(profile, day, period, taper, reference, accents, week, length, rows, today, limited, taper_factor, progression=None, *, periodization=None, support_limited=None, actual_reference=None, support_readiness=None):
    automatic = load_progression.accents(profile, period, list(PRIORITIES.get(period, ("Z1",))))
    state = planning_controls.resolve(profile, day, period, automatic, periodization=periodization)
    if state is not None:
        support = mesocycle_focus.recovery_support({**state, "kind": "RECOVERY"}, profile, rows, today,
                   limited=limited if support_limited is None else support_limited,
                   taper=taper or period not in mesocycle_focus.PREPARATION, readiness=support_readiness)
        state = support if state["kind"] == "RECOVERY" else state
        state["recovery_support_components"] = support["accents"]
    focus = state["accents"] if state else _accents(period, accents)
    legacy = training_targets.component_targets(reference, profile, focus, week, length, period, taper, limited, taper_factor) if state is None else {}
    # Actual observations and their cutoff are constant throughout this read.
    # The policy functions only read this reference; daily goals remain fresh.
    if actual_reference is None:
        actual_reference = planning_controls.reference(rows, today)
    goals = planning_controls.goals(profile, state, actual_reference, legacy,
                                     limited=limited, taper_factor=taper_factor)
    goals = load_progression.apply(goals, profile, state, progression, day, period, taper, limited,
                                   taper_factor, actual_reference)
    goals = mesocycle_focus.cap_recovery(goals, profile, state, actual_reference)
    return goals, focus, state


def progression_context(repository, alias, profile, source, rows, today, periodization=None):
    config = load_progression.settings(profile)
    if not config or not profile.get("planning_controls"):
        return None
    adaptation = load_adaptation.assess(ResponseStore(repository).entries(alias), today, rows=rows) if config["feedback_enabled"] else None
    from .management_store import ManagementStore
    retained = ManagementStore(repository).progression_reference(alias)
    settings = repository.athlete_settings(alias)
    physiology = {"bounds": list(settings.zone_bounds_bpm), "hrmax": settings.hrmax_bpm} if settings else None
    return load_progression.context(profile, source, rows, today, adaptation, retained=retained,
                                    physiology=physiology, periodization=periodization)


def _dose_usage(blocks, evidence, zone):
    """One work-dose budget across mixed parts and all interval repetitions."""
    capacities = {zone: evidence["capacity_minutes"]}
    if evidence.get("effort_profile"):
        capacities[zone] *= evidence["effort_profile"]["total_capacity_ratio"]
    secondary = evidence.get("secondary_capacity")
    if secondary:
        secondary_zone = secondary.get("zone", "Z1")
        capacities[secondary_zone] = secondary["capacity_minutes"] * secondary.get("effort_profile", {}).get("total_capacity_ratio", 1.)
    paired = evidence.get("paired_capacity")
    if paired:
        capacities[paired["zone"]] = paired["capacity_minutes"] * paired.get("effort_profile", {}).get("total_capacity_ratio", 1.)
    by_effort = evidence.get("capacity_by_effort", {})
    total = 0.
    for b in blocks:
        if b["kind"] != "WORK" or b["zone"] == "STR" or b["zone"] not in capacities:
            continue
        effort = f"{b['zone']}:{b['target_hr_bpm']:.3f}" if b.get("target_hr_bpm") is not None else None
        total += b["duration_min"]/by_effort.get(effort, capacities[b["zone"]])
    return total


def _volume_ceiling(profile, periodization, events, available, baseline, start, end, preferences, limited):
    anchor = date.fromisoformat(str(preferences.get("mesocycle_anchor_date", profile["program_start"])))
    length = preferences.get("mesocycle_length_weeks", 4)
    weighted = available_window = 0.
    for i in range((end-start).days+1):
        day = start+timedelta(days=i)
        phase, _ = _phase(periodization, day)
        reserved = any(e["event_type"] in {"MAIN_RACE", "CONTROL_RACE", "TEST", "UNAVAILABLE"}
                       and str(e["start_date"]) <= day.isoformat() <= str(e["end_date"]) for e in events)
        if reserved or not phase:
            continue
        available_window += available[day.weekday()]
        state = planning_controls.resolve(profile, day, phase, list(PRIORITIES.get(phase, ("Z1",))))
        week = max(0, (day-anchor).days//7) % length
        factor = state["volume_factor"] if state else training_targets.cycle_factor(
            week, length, profile.get("progression_percent", 5),
            development=not limited and phase in {"GENERAL_PREPARATION", "SPECIAL_PREPARATION"})
        weighted += available[day.weekday()]*factor*_taper_factor(periodization, day)
    return min(available_window, baseline*weighted/max(1., sum(available)))


def _volume_estimate(profile, components, baseline, actual_base, days):
    """Display-only duration equivalent at the observed load/time mix.

    Zone targets govern dosing. This approximation is not a second time quota
    and cannot convert an individual component's effective load into raw time.
    """
    zones = [z for z in COMPONENTS if z != "STR" or profile.get("strength_enabled")]
    observed = sum(actual_base[z]["c40"]*7 for z in zones)
    target = sum(components[z]["target_weekly_effective"] or 0. for z in zones)
    return _round(baseline*target/observed*days/7) if observed > 0 else None


def _long_term_outlook(profile, periodization, reference, accents, preferences, rows, today, limited, *, volume=None, events=None, progression=None):
    """Read-only coach targets before the daily planner's eligibility gates.

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
                          "c40": sum(r40) / len(r40) if r40 else 0., "known": len(r40) >= planning_history.MINIMUM_DAYS}
    weeks = []
    actual_reference = planning_controls.reference(rows, today)
    day = start
    while day <= end:
        # Preserve the coach's microcycle boundaries. A rolling display starting
        # today would blend adjacent waves and hide the peak of a stress week.
        left = day
        right = min(day + timedelta(days=6 - (day - anchor).days % 7), end)
        targets = {z: [] for z in COMPONENTS}
        q_targets = {z: [] for z in COMPONENTS}
        phases, selected, meso_weeks = [], [], []
        while day <= right:
            period, taper = _phase(periodization, day)
            week = max(0, (day - anchor).days // 7) % length
            goals, focus, cycle = _goals(profile, day, period, taper, reference, accents, week, length,
                                          rows, today, limited if progression else False, _taper_factor(periodization, day), progression, periodization=periodization, support_limited=limited, actual_reference=actual_reference)
            for z in COMPONENTS:
                targets[z].append(goals[z]["target"])
                # Strength has no aerobic cascade: its direct Q equals its E.
                q_targets[z].append(goals[z]["target"] if z == "STR" else goals[z].get("target_weekly_q"))
            phases.append(period)
            selected.extend(focus)
            meso_weeks.append(cycle["week"] if cycle else week + 1)
            day += timedelta(days=1)
        components = {}
        for z in COMPONENTS:
            target = sum(targets[z]) / len(targets[z])
            known = actual_base[z]["known"] or z in profile.get("component_targets_weekly", {})
            base = actual_base[z]
            q_known = all(v is not None for v in q_targets[z]) and (z != "STR" or known)
            components[z] = {"target_weekly_q": _round(sum(q_targets[z])/len(q_targets[z])) if q_known else None,
                             "target_period_q": _round(sum(q_targets[z])/7) if q_known else None,
                             "target_weekly_effective": _round(target) if known else None,
                             "target_period_effective": _round(sum(targets[z])/7) if known else None,
                             "target_index_7_40": _round((base["b50"] + target / 7) / (base["b50"] + base["c40"])) if base["known"] else None}
        weeks.append({"start_date": left.isoformat(), "end_date": right.isoformat(),
                      "days": (right - left).days + 1, "cycle": cycle, "phases": list(dict.fromkeys(phases)),
                      "accents": list(dict.fromkeys(selected)), "mesocycle_weeks": list(dict.fromkeys(meso_weeks)),
                      "components": components,
                      **({"volume_budget_minutes": _volume_estimate(profile, components, volume["baseline_weekly_minutes"], actual_base, (right-left).days+1),
                          "volume_role": "HISTORICAL_MIX_EQUIVALENT_NOT_TIME_LIMIT"} if volume else {})})
    return {"schema_version": "training-outlook-v1", "as_of": today.isoformat(), "progression": load_progression.public_context(progression),
            "basis": "CURRENT_ACTUAL_REFERENCE_FROZEN", "targets_version": training_targets.VERSION,
            "reference_cutoff": reference["cutoff"], "limited": limited,
            "goal_role": "COACH_TARGETS_BEFORE_DAILY_GATES",
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
    calendar_settings = _read_optional(repository, "athlete_planning_calendar", alias) or {"events": []}
    profile, horizon_context = planning_schedule.horizon(profile, calendar_settings["events"])
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
    controls = profile.get("planning_controls")
    primary_sport = sport
    training_sports = (controls.get("training_sports") or [sport]) if controls else [sport]
    if controls:
        preferences = {"sessions_per_week": controls["sessions_per_week"], "rest_days": [],
                       "intensity_days": controls["intensity_days"], "strength_days": controls["strength_days"],
                       "long_session_day": controls["long_session_day"], "max_key_sessions_per_week": profile.get("max_key_sessions_per_week", 2),
                       "mesocycle_anchor_date": controls.get("mesocycle_anchor") or profile["program_start"],
                       "mesocycle_length_weeks": len(controls["wave"])}
    accents = _read_optional(repository, "athlete_mesocycle_accent_preferences", alias)
    calendar_reader = getattr(repository, "active_planning_calendar", None) or repository.active_activity_calendar
    envelope = calendar_reader(alias, today - timedelta(days=89), end_date) or {}
    snapshot = envelope.get("snapshot_payload") or {}
    source = snapshot.get("load_history") or {}
    rows = _daily_rows(source, today)
    volume_evidence = planning_controls.volume_history(source, today, 0, gap_days=(controls or {}).get("history_gap_days", planning_history.DEFAULT_GAP_DAYS))
    history_policy = volume_evidence["history_policy"]
    reentry_days, reentry_reason = planning_history.reentry(profile, volume_evidence)
    periodization = build_periodization(program_start, program_end, events,
                                        reentry_days_override=reentry_days,
                                        taper_days=profile.get("taper_days", 7), transition_days=profile.get("transition_days", 0))
    periodization["entry_basis"] = {"days_override": reentry_days, "reason": reentry_reason}

    actual_activities = [a for a in envelope.get("activities", []) if a.get("local_date", "") <= today.isoformat()]
    configs = model_service.ModelStore(repository).config(alias)
    warnings = list(periodization.get("warnings", []))
    warnings += [_warning("DRAFT_REQUIRES_COACH_REVIEW", "Проект за треньорски преглед; не е активирана програма."),
                 _warning("LOAD_ONLY", "Recovery запазва модела само от натоварването. Наблюдаваната реакция се оценява отделно според настройките за прираст."),
                 _warning("CALENDAR_DAY_RESOLUTION", "Готовността е за началото на деня. Всички сесии споделят дневния бюджет; сумарният им товар определя прогнозата за следващите дни. Няма отделна прогноза за възстановяване между сутрешна и следобедна сесия.")]
    if horizon_context["outside_main_races"]:
        warnings.append(_warning("MAIN_RACE_OUTSIDE_HORIZON", "Основен старт е извън избрания край на подготовката. Променете края или изберете автоматичен период до основния старт."))
    quality = source.get("quality") or {}
    completed = sorted({r["date"] for r in rows if r["date"] < today.isoformat()})
    history_days = len([d for d in completed if d >= (today - timedelta(days=40)).isoformat()])
    component_history_days = {z: len({r["date"] for r in rows if r["zone"] == z and
                                  (today - timedelta(days=40)).isoformat() <= r["date"] < today.isoformat()}) for z in COMPONENTS}
    as_of = source.get("period_end")
    missing_days = max(0, (today - date.fromisoformat(as_of)).days) if as_of else None
    limited = not history_policy["usable"] or min(component_history_days.values()) < planning_history.MINIMUM_DAYS or bool(quality.get("limited_activities") or quality.get("excluded_activities"))
    equivalence_changed = not load_progression.history_matches(source, {"bounds": list(settings.zone_bounds_bpm), "hrmax": settings.hrmax_bpm})
    if equivalence_changed:
        limited = True
        warnings.append(_warning("EQUIVALENCE_REANALYSIS_REQUIRED", "Нужен е нов анализ на активностите по текущите пулсови граници и приравняване (Z5: 5% за удар). До тогава новите дози са задържани."))
    if limited:
        warnings.append(_warning("LIMITED_LOAD_HISTORY", "Историята е кратка или съдържа непълни активности. Само ограничени леки предложения; липсващата умора не се приема за нулева."))
    methods = resolved_methods(profile)
    warnings.append(_warning("VERSIONED_COACHING_RULES", "Целите и прогресията използват видими начални треньорски правила. Нисък стрес сам по себе си не увеличава товара."))
    if (controls and controls.get("double_threshold_days") and "Z4" in controls.get("double_threshold_components", [])
            and not any(p.get("zone") == "Z4" and p.get("goal") == "THRESHOLD" for p in profile.get("interval_profiles", []))):
        warnings.append(_warning("DOUBLE_THRESHOLD_PROFILE_REQUIRED", "За прагова част в Z4 е нужен индивидуален прагoв интервален профил. Профил за аеробна мощност не го замества."))
    for missing in catalog(profile)["disabled"]:
        warnings.append(_warning("METHOD_PROFILE_" + missing["component"], missing["reason"]))
    blocked = equivalence_changed or (missing_days is not None and missing_days > 1)
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
    speed_by_sport = {sport: speed}
    context_by_sport = {sport: context}
    for other in training_sports:
        if other == sport:
            continue
        other_speed = model_service.speed_view(repository, alias, other) if envelope else None
        if other_speed and (other_speed.get("sport") != other or other_speed.get("source_generation_id") != envelope.get("generation_id") or other_speed.get("source_revision") != envelope.get("revision")):
            blocked = True
            warnings.append(_warning("INPUT_GENERATION_CHANGED", "Оценките по средства не са от една съгласувана версия. Обновете проекта."))
        speed_by_sport[other] = other_speed
        context_by_sport[other] = _capacity_context(other_speed, settings)
    if speed and speed.get("sport") != sport:
        blocked = True
        warnings.append(_warning("SPEED_SPORT_MISMATCH", "Оценката скорост–време е за различно средство и не може да се използва за този проект."))
    race_sport = profile["sport"]
    race_speed = speed_by_sport.get(race_sport)
    if race_speed is None and envelope and race_duration.distance_m(profile.get("discipline")) is not None:
        race_speed = model_service.speed_view(repository, alias, race_sport)
    event_duration = race_duration.resolve(profile, race_speed)
    if event_duration["source"] == "SPEED_DURATION" and (
            event_duration.get("source_generation_id") != envelope.get("generation_id") or
            event_duration.get("source_revision") != envelope.get("revision")):
        blocked = True
        warnings.append(_warning("INPUT_GENERATION_CHANGED", "Моделът за състезателната продължителност се е обновил. Генерирайте отново."))
    profile = race_duration.applied(profile, event_duration)
    # Resolve the same race component before constructing the growth calendar
    # in both the weekly generator and the read-only outlook.
    progression = progression_context(repository, alias, profile, source, rows, today, periodization)
    adaptation = (progression or {}).get("adaptation") or {}
    if adaptation.get("hold_for_reported_illness_or_pain"):
        blocked = True
        warnings.append(_warning("REPORTED_ILLNESS_OR_PAIN", load_adaptation.symptom_message(adaptation)))
    if not profile.get("race_duration_min"):
        warnings.append(_warning("RACE_DURATION_MISSING", "Няма индивидуална оценка за тази дистанция. Въведете приблизителната продължителност на основната дисциплина в профила."))
    by_sport_minutes = volume_evidence["reference_by_sport_weekly_minutes"]
    volume = planning_controls.volume_basis(profile, volume_evidence)
    weekly_minutes = volume["baseline_weekly_minutes"]
    volume_source = volume["weekly_volume_source"]
    historical_weekly_minutes = volume["historical_selected_weekly_minutes"]
    if volume["reported_history_only"]:
        limited = True
        warnings.append(_warning("AGGREGATE_HISTORY_ONLY", "Въведеният седмичен обем ограничава проекта, но не се превръща в измислени дневни товари или известна готовност."))
    if volume["missing_history"]:
        blocked = True
        warnings.append(_warning("WEEKLY_VOLUME_REQUIRED", "Нужна е достатъчна реална история или обемите от последните четири седмици."))
    if not any(by_sport_minutes.get(s, 0.) for s in training_sports):
        limited = True
        warnings.append(_warning("NO_ACTUAL_MODE_EXPOSURE", "Няма скорошен обем за избраните средства. Общият бюджет не замества опита и капацитета за конкретното средство; предложенията са ограничени."))
    available = planning_history.availability(profile)
    availability_mode = planning_history.availability_mode(profile)
    for weekday in preferences.get("rest_days", []):
        available[weekday] = 0
    session_limit = min(21, preferences.get("sessions_per_week", 7))
    schedule = planning_schedule.slots(profile, available, start_date, end_date)
    slot_counts = {d: count for d, _, count in schedule}
    threshold_days = (controls or {}).get("threshold_days", [])
    double_days = (controls or {}).get("double_threshold_days", [])
    key_days = sorted(set(preferences.get("intensity_days", []) + threshold_days + double_days))
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
        state = planning_controls.resolve(profile, day, period, list(PRIORITIES.get(period, ("Z1",))))
        return week, state["volume_factor"] if state else training_targets.cycle_factor(week, meso_length, profile.get("progression_percent", 5), development=development)
    target_reference = training_targets.development_reference(rows, today, meso_anchor, meso_length)
    meso_week, meso_factor = meso_at(start_date)
    # No independent history × wave cap in automatic mode. 7/40 budgets
    # are checked against every candidate's canonical load, including spill.
    available_window = sum(available[(start_date+timedelta(days=i)).weekday()] for i in range((end_date-start_date).days+1))
    component_governed = bool(controls) and not limited and not volume["reported_history_only"]
    weekly_ceiling = available_window if component_governed else _volume_ceiling(
        profile, periodization, events, available, weekly_minutes, start_date, end_date, preferences, limited)
    if controls and controls.get("weekly_target_hours") is not None:
        weekly_ceiling = min(weekly_ceiling, controls["weekly_target_hours"]*60*((end_date-start_date).days+1)/7)
    automatic_time = component_governed and availability_mode == "AUTO_HISTORY" and controls.get("weekly_target_hours") is None
    remaining = weekly_ceiling
    sport_spent = {s: sum(float(a.get("duration_min") or 0.) for a in source.get("activities", [])
                            if a.get("sport") == s and a["date"] == today.isoformat()) if start_date == today else 0.
                   for s in training_sports}
    strength_sessions = 0
    last_strength_day = max([date.fromisoformat(a["date"]) for a in source.get("activities", [])
                             if a.get("sport") == "WeightTraining" and a["date"] <= today.isoformat()] or [None])
    actual_window_minutes = sum(float(a.get("duration_min") or 0.) for a in source.get("activities", [])
                                if start_date.isoformat() <= a["date"] <= end_date.isoformat())
    remaining = max(0., remaining - actual_window_minutes)
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
    locked_sessions = planning_schedule.day_sessions(locked_day or {})
    if any(v.get("is_key_session", v["zone"] in {"Z3", "Z4", "Z5"}) for v in locked_sessions):
        last_key_day = date.fromisoformat(locked_day["date"])
        key_sessions += sum(v.get("is_key_session", v["zone"] in {"Z3", "Z4", "Z5"}) for v in locked_sessions)
    if any(v["zone"] == "STR" for v in locked_sessions):
        last_strength_day = date.fromisoformat(locked_day["date"])
        strength_sessions += sum(v["zone"] == "STR" for v in locked_sessions)
    activation_eligible = all_sources_known and not blocked
    # Reserve scarce key-session slots before spending optional easy volume.
    # Actual Recovery is still checked chronologically, including easy days.
    key_slots = []
    reserved_key_count = 0
    for offset in range(horizon):
        d = start_date + timedelta(days=offset)
        period, taper = _phase(periodization, d)
        if limited or period not in {"GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"} or taper:
            continue
        if (controls and preferences.get("long_session_day") == d.weekday()) or available[d.weekday()] < 40 or (key_days and d.weekday() not in key_days) or not slot_counts[d]:
            continue
        if any(e["event_type"] in {"MAIN_RACE", "CONTROL_RACE", "TEST", "UNAVAILABLE"} and
               str(e["start_date"]) <= d.isoformat() <= str(e["end_date"]) for e in events):
            continue
        if any(a.get("date") == d.isoformat() for a in source.get("activities", [])):
            continue
        previous_key = key_slots[-1] if key_slots else last_key_day
        if previous_key and (d - previous_key).days < 2:
            continue
        needed = 2 if d.weekday() in double_days and slot_counts[d] >= 2 else 1
        if reserved_key_count + needed <= max(0, key_limit - key_sessions):
            key_slots.append(d)
            reserved_key_count += needed
    covered_slots = {}
    # Establish the entire week's component intent before choosing methods.
    # Calendar changes inside the week retain separate rolling targets.
    goal_windows, day_goals = {}, {}
    for offset in range(horizon):
        d = start_date + timedelta(days=offset)
        period, taper = _phase(periodization, d)
        week, _ = meso_at(d)
        day_goals[d] = _goals(profile, d, period, taper, target_reference, accents,
                             week, meso_length, rows, today, limited, _taper_factor(periodization, d), progression, periodization=periodization)
        goal_windows[d] = day_goals[d][0]
    opportunities = {z: [] for z in COMPONENTS}
    for d, slot, count in schedule:
        if not count or any(e["event_type"] in {"MAIN_RACE", "CONTROL_RACE", "TEST", "UNAVAILABLE"}
                            and str(e["start_date"]) <= d.isoformat() <= str(e["end_date"]) for e in events):
            continue
        if any(a.get("date") == d.isoformat() for a in source.get("activities", [])):
            continue
        for z in COMPONENTS:
            if z in {"Z3", "Z4", "Z5"} and d not in key_slots and not (progression and z == "Z3" and key_slots and d > key_slots[-1]):
                continue
            if z in {"Z3", "Z4", "Z5"} and slot >= (2 if d.weekday() in double_days and count >= 2 else 1):
                continue
            if z == "STR" and (not profile.get("strength_enabled") or preferences.get("strength_days") and d.weekday() not in preferences["strength_days"]):
                continue
            opportunities[z].append((d, slot))
    for day, slot_index, slots_today in schedule:
        if slot_index < covered_slots.get(day, 0):
            continue
        prior_today = [d for d in result_days if d["date"] == day.isoformat()]
        if prior_today and any(d["status"] not in {"TRAINING", "REST"} or d.get("locked") for d in prior_today):
            continue
        day_spent = sum(v["total_minutes"] for d in prior_today for v in planning_schedule.day_sessions(d))
        day_available = max(0., available[day.weekday()] - day_spent)
        key = day.isoformat()
        period, taper = _phase(periodization, day)
        # Calendar-day Recovery cannot infer intraday recovery. Forecast the
        # cumulative load, but assess the bundle against day-start readiness.
        day_start_rows = [r for r in forecast_rows if r["date"] != key] + [r for r in rows if r["date"] == key]
        # Dose trials on this day use the same causal spill reference. Compute
        # it once; it is not a dose capacity and never reads future proposals.
        technical_reference = _canonical_load([], settings, day_start_rows, day)[2]
        def candidate_load(blocks):
            return _candidate_load(blocks, settings, day_start_rows, day, technical_reference=technical_reference)
        recovery_before = recovery_v2.simulate(day_start_rows, configs["zones"], target=day)
        ready = {r["zone"]: r["readiness_percent"] for r in recovery_before["current"]}
        day_meso_week, day_meso_factor = meso_at(day)
        goals, selected_accents, cycle_state = day_goals[day]
        if cycle_state and cycle_state["kind"] == "RECOVERY":
            goals, selected_accents, cycle_state = _goals(profile, day, period, taper, target_reference, accents,
                day_meso_week, meso_length, rows, today, limited, _taper_factor(periodization, day), progression,
                periodization=periodization, support_readiness=ready if forecast_known else {})
            goal_windows[day] = goals
        budgets = _budgets(forecast_rows, day, taper, day_meso_factor, actual_rows=rows, targets=goals)
        q_remaining = load_progression.remaining_q(source, result_days, day, goals)
        for z, remaining_q in q_remaining.items():
            budgets[z].update(target_weekly_q=goals[z]["target_weekly_q"], remaining_q=remaining_q)
        allocation = planning_allocation.quota(goal_windows, opportunities, forecast_rows, day, slot_index) if component_governed else None
        visible_ready = {z: _round(ready[z]) if forecast_known else None for z in COMPONENTS}
        item = {"date": key, "status": "REST", "period": period, "taper": taper, "cycle": cycle_state,
                "session": None, "slot": slot_index + 1, "readiness_scope": "DAY_START_BUNDLE_NOT_INTRADAY_FORECAST", "readiness_before": visible_ready, "readiness_after": dict(visible_ready),
                "load_budget": {"remaining_weekly_minutes": None if automatic_time else _round(max(0., remaining)), "components": budgets,
                                "mesocycle_week_index": day_meso_week, "mesocycle_factor": day_meso_factor},
                "rejected_alternatives": [], "explanation": "Почивка; не е необходимо да се запълва всяка свободна минута."}
        if allocation is not None:
            item["load_budget"]["component_allocation"] = {z: _round(v) for z, v in allocation.items()}
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
        elif not slots_today and available[day.weekday()] > 0 and not any(e["event_type"] == "UNAVAILABLE" for e in events_today):
            item.update(status="REST", explanation="Не е разпределена сесия според избрания седмичен брой и дните за ключови задачи.")
        elif any(e["event_type"] == "UNAVAILABLE" for e in events_today) or day_available <= 0:
            item.update(status="UNAVAILABLE", explanation="Почивен или недостъпен ден според календара и предпочитанията.")
            if key in decisions:
                item.update(status="SKIPPED" if decisions[key]["action"] == "SKIP" else "REST",
                            explanation="Отбелязано пропускане без наваксване." if decisions[key]["action"] == "SKIP" else "Почивка по индивидуално решение.")
        elif locked_day and locked_day["date"] == key and locked_sessions:
            preserved = deepcopy(locked_sessions)
            effective = {z: 0. for z in COMPONENTS}
            for session in preserved:
                _, load, _ = _canonical_load(session["blocks"], settings, day_start_rows, day)
                for z in COMPONENTS:
                    effective[z] += load[z]
            total = sum(v["total_minutes"] for v in preserved)
            if len(preserved) > slots_today or sessions + len(preserved) > session_limit or any(ready[z] < 90 or effective[z] > budgets[z]["deficit_effective"] + .001 for z in COMPONENTS if effective[z] > 0) or total > min(remaining, day_available) + .001:
                item.update(status="REVIEW_REQUIRED", explanation="Новите данни изискват преглед на днешните утвърдени задачи.")
                activation_eligible = False
            else:
                forecast_rows = _with_forecast_day(forecast_rows, day, effective)
                after = recovery_v2.simulate(forecast_rows, configs["zones"], target=day)
                item.update(session=preserved[0], sessions=preserved, status="TRAINING", locked=True,
                            readiness_after={r["zone"]: _round(r["readiness_percent"]) for r in after["current"]},
                            explanation="Днешните утвърдени задачи са запазени; адаптират се следващите дни.")
                remaining = max(0., remaining - total)
                for session in preserved:
                    if session["zone"] != "STR":
                        sport_spent[session["sport"]] = sport_spent.get(session["sport"], 0.) + session["total_minutes"]
                sessions += len(preserved)
        elif not automatic_time and remaining <= .001:
            item.update(status="UNAVAILABLE", time_limit_exhausted=True,
                        explanation="Лимитът за общо време в този период е изчерпан от изпълнените и вече планираните сесии. "
                        "Това е ограничение за време, а не предписание за възстановяване. Провери „Максимум общо време за 7 дни“ и наличните минути по дни в профила.")
            item["rejected_alternatives"].append({"method_id": "TIME_BUDGET", "code": "WEEKLY_TIME_LIMIT_EXHAUSTED",
                                                   "reason": item["explanation"]})
        elif sessions >= session_limit:
            item["explanation"] = "Достигнат е предпочитаният брой сесии за седемдневния проект."
        else:
            choices = []
            candidates = [(m_sport, m) for m_sport in training_sports for m in methods
                          if m_sport in m["sports"] and (m["zone"] != "STR" or m_sport == primary_sport)]
            if (day.weekday() in double_days and slot_index == 0 and slots_today >= 2 and not limited and not taper
                    and (not cycle_state or cycle_state["kind"] != "RECOVERY")
                    and sessions + 2 <= session_limit and key_sessions + 2 <= key_limit
                    and (profile.get("age_years") or 0) >= 18 and (profile.get("training_experience_years") or 0) >= 1):
                pairs = []
                for m_sport, m in candidates:
                    if m["zone"] not in (controls or {}).get("double_threshold_components", ["Z3"]):
                        continue
                    if not ((m["zone"] == "Z3" and m["structure"] in {"CONTINUOUS", "TWO_REPETITIONS", "THRESHOLD_REPETITIONS"}) or
                            (m["zone"] == "Z4" and m.get("interval_profile", {}).get("goal") == "THRESHOLD")):
                        continue
                    requested_zones = controls.get("double_threshold_components", ["Z3"])
                    if len(requested_zones) == 2:
                        if m["zone"] != requested_zones[0]:
                            continue
                        partners = [other for other_sport,other in candidates if other_sport == m_sport and other["zone"] == requested_zones[1]
                                    and (other.get("interval_profile", {}).get("goal") == "THRESHOLD" or other["zone"] == "Z3" and other["structure"] == "CONTINUOUS")]
                        for other in partners:
                            pairs.append((m_sport, {**deepcopy(m), "id": m["id"] + "-DOUBLE-" + other["id"], "title": "Двоен праг: " + "/".join(requested_zones),
                                "double_threshold": True, "paired_method": deepcopy(other), "single_min_work_min": m["min_work_min"], "min_work_min":2*m["min_work_min"]}))
                    else:
                        pairs.append((m_sport, {**deepcopy(m), "id": m["id"] + "-DOUBLE", "title": "Двоен праг: " + m["title"],
                                                "double_threshold": True, "min_work_min": 2*m["min_work_min"]}))
                candidates = pairs + candidates
            for sport, method in candidates:
                speed, context = speed_by_sport[sport], context_by_sport[sport]
                method = deepcopy(method)
                method["actual_sport"] = sport
                race_minutes = profile.get("race_duration_min")
                if race_minutes and method["zone"] == "Z3" and method["purpose"] != "SUPPORTING" and period in {"SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"}:
                    blend = min(1., max(0., (race_minutes - 50.) / 20.))
                    method["position"] = .85 - .30 * blend
                if sport not in method["sports"]:
                    continue
                z = method["zone"]
                supporting = method["purpose"] == "SUPPORTING"
                is_key = z in {"Z3", "Z4", "Z5"} and not supporting
                is_strength = z == "STR"
                is_threshold = z == "Z3" and method["structure"] in {"CONTINUOUS", "TWO_REPETITIONS", "THRESHOLD_REPETITIONS"} or method.get("interval_profile", {}).get("goal") == "THRESHOLD"
                threshold_choice = (controls or {}).get("threshold_method", "AUTO")
                race_development_zones = (([cycle_state["race_component"]] if period == "COMPETITION" else cycle_state["mesocycle_accents"])
                                          if cycle_state and cycle_state.get("race_component") else [])
                rejection = None
                if period not in method["periods"]:
                    rejection = ("PERIOD_NOT_SUPPORTED", "Методът не е включен в този период.")
                elif cycle_state and controls["accent_mode"] == "AUTO" and not cycle_state["explicit"] and period in {"PRECOMPETITION", "COMPETITION"} and method["purpose"] == "BUILDING" and not is_strength and z not in race_development_zones:
                    rejection = ("RACE_COMPONENT_PRIORITY", "Развиващата специална работа следва състезателните акценти за периода; останалите компоненти получават поддържане.")
                elif cycle_state and cycle_state["kind"] == "RECOVERY" and z in {"Z3", "Z4", "Z5", "STR"} and z not in selected_accents:
                    rejection = ("RECOVERY_COMPONENT_DELOAD", "Компонентът се разтоварва; допълваща работа е допустима само за избрания по-слабо натоварен компонент.")
                elif supporting and (not progression or taper or not key_slots or day <= key_slots[-1] or slot_index > 0):
                    rejection = ("SUPPORT_AFTER_KEY_WORK", "Поддържащият остатък се разпределя след основните задачи и само при свободен бюджет и готовност.")
                elif limited and method["purpose"] != "RECOVERY":
                    rejection = ("INSUFFICIENT_HISTORY", "При ограничена история са разрешени само леки ограничени предложения.")
                elif controls and not is_strength and not by_sport_minutes.get(sport, 0.) and method["purpose"] != "RECOVERY":
                    rejection = ("NO_ACTUAL_MODE_EXPOSURE", "За това средство няма скорошна история. Допуска се само ограничено леко въвеждане, независимо от опита в други спортове.")
                elif method["structure"] == "MODEL_INTERVALS" and (
                    (profile.get("age_years") is not None and profile["age_years"] < 18)
                    or (profile.get("training_experience_years") is not None and profile["training_experience_years"] < 1)
                    or len({a["date"] for a in source.get("activities", []) if a.get("sport") == sport
                            and a["date"] in history_policy["complete_dates"]
                            and any(r["zone"] == z and r.get("raw_time_min", 0) >= 1 for r in a.get("zones", []))}) < 2):
                    rejection = ("AUTO_INTERVAL_EXPOSURE_REQUIRED", f"Автоматичният метод за {z} изисква поне две скорошни активности с работа в този компонент и средство. Профилите за известни начинаещи и непълнолетни изискват отделна методика.")
                elif is_threshold and threshold_choice != "AUTO" and ((threshold_choice == "CONTINUOUS") != (method["structure"] == "CONTINUOUS")):
                    rejection = ("THRESHOLD_METHOD_PREFERENCE", "Избран е друг вид прагова тренировка в профила.")
                elif is_key and day.weekday() in set(threshold_days + double_days) and not is_threshold:
                    rejection = ("THRESHOLD_DAY_RESERVED", "Този ден е избран за прагова работа; метод за аеробна мощност не я замества.")
                elif is_threshold and threshold_days and day.weekday() not in set(threshold_days + double_days):
                    rejection = ("THRESHOLD_DAY_PREFERENCE", "Денят не е избран за прагова работа.")
                elif is_strength and (strength_sessions >= (controls or {}).get("max_strength_sessions", 2) or (last_strength_day and (day-last_strength_day).days < 2)):
                    rejection = ("STRENGTH_SESSION_LIMIT", "Достигнат е силовият лимит или липсва достатъчно разстояние между силовите сесии.")
                elif is_strength and preferences.get("strength_days") and day.weekday() not in preferences["strength_days"]:
                    rejection = ("STRENGTH_DAY_PREFERENCE", "Денят не е избран за силова работа.")
                elif is_key and (key_sessions >= key_limit or (last_key_day and (day - last_key_day).days < 2)):
                    rejection = ("KEY_SESSION_LIMIT", "Достигнат е лимитът или липсва достатъчно разстояние между ключови сесии.")
                elif is_key and key_days and day.weekday() not in key_days:
                    rejection = ("INTENSITY_DAY_PREFERENCE", "Денят не е избран за интензивна работа.")
                elif is_key and day not in key_slots:
                    rejection = ("KEY_SLOT_RESERVED", "Ключовите сесии са разположени първи в подходящите дни; този ден остава за лека работа или почивка.")
                elif method["structure"] == "THRESHOLD_HIGH" and not 0 <= (today - date.fromisoformat(method["interval_profile"]["assessed_on"])).days <= 42:
                    rejection = ("STALE_EFFORT_CAPACITY", "Индивидуалната опора за високото усилие трябва да се обнови.")
                elif method["structure"] == "THRESHOLD_HIGH" and ready[method["interval_profile"]["zone"]] < 90.:
                    rejection = ("HIGH_BLOCK_NOT_READY", "Високият компонент на комбинацията не е възстановен.")
                elif method.get("paired_method") and ready[method["paired_method"]["zone"]] < 90.:
                    rejection = ("PAIRED_THRESHOLD_NOT_READY", "Вторият компонент на двойния праг не е възстановен.")
                elif ready[z] < 90.:
                    rejection = ("RECOVERY_BELOW_90", f"Прогнозната готовност за {z} е {_round(ready[z])}%, под 90%.")
                elif not limited and budgets[z]["target_weekly_effective"] <= 0:
                    rejection = ("NO_COMPONENT_TARGET", "Липсва установена компонентна база. Въвеждането на нов развиващ товар изисква отделна треньорска цел.")
                elif allocation is not None and allocation[z] <= .001:
                    rejection = ("WEEKLY_NEED_COVERED", "Не остава разпределен товар за този компонент в седмицата. Свободната сесия не се запълва задължително.")
                elif z != "Z1" and ready["Z1"] < 90.:
                    rejection = ("WARMUP_NOT_READY", "Не е възстановен компонентът за загрявката и разпускането.")
                if rejection:
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": rejection[0], "reason": rejection[1]})
                    continue
                evidence = capacity_for(method, settings, speed, context, today, profile.get("allow_expert_fallback", True), use_model_prior=(controls or {}).get("capacity_policy") == "MODEL_WITH_PRIOR")
                if evidence is None:
                    reason = "За автоматична Z5 е нужен скорошен максимален тест над подкрепената граница Z4/Z5 или индивидуален интервален профил. Пулсът не дава отделен Z5 Tref." if method["structure"] == "MODEL_INTERVALS" and z == "Z5" else "Няма допустима оценка на капацитета за този метод и средство."
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": "CAPACITY_UNAVAILABLE", "reason": reason})
                    continue
                if method["structure"] == "THREE_PROGRESSIVE_BLOCKS":
                    capacities = [capacity_for({**method,"position":position}, settings, speed, context, today,
                        profile.get("allow_expert_fallback", True), use_model_prior=(controls or {}).get("capacity_policy") == "MODEL_WITH_PRIOR") for position in (.2,.5,.85)]
                    if any(c is None for c in capacities):
                        item["rejected_alternatives"].append({"method_id":method["id"],"code":"PROGRESSIVE_CAPACITY_UNAVAILABLE","reason":"Не е подкрепен капацитетът за всички части на прогресивния метод."})
                        continue
                    evidence["capacity_by_effort"] = {f"{z}:{c['target_hr_bpm']:.3f}":c["capacity_minutes"] for c in capacities}
                if method.get("paired_method"):
                    other = method["paired_method"]
                    paired = capacity_for(other, settings, speed, context, today, profile.get("allow_expert_fallback", True), use_model_prior=(controls or {}).get("capacity_policy") == "MODEL_WITH_PRIOR")
                    if paired is None or period not in other["periods"]:
                        item["rejected_alternatives"].append({"method_id":method["id"], "code":"PAIRED_CAPACITY_UNAVAILABLE", "reason":"Липсва актуален капацитет или метод за втория компонент на двойния праг."})
                        continue
                    evidence["paired_capacity"] = {**paired, "zone":other["zone"]}
                    primary_capacity = evidence["capacity_minutes"] * evidence.get("effort_profile", {}).get("total_capacity_ratio", 1.)
                    paired_capacity = paired["capacity_minutes"] * paired.get("effort_profile", {}).get("total_capacity_ratio", 1.)
                    method["min_work_min"] = math.ceil(4*max(method["single_min_work_min"], other["min_work_min"]*primary_capacity/paired_capacity))/2
                if method["structure"] in {"ALTERNATING", "CRUISE_ALTERNATING", "AEROBIC_STRENGTH", "AEROBIC_SUPPORT"}:
                    secondary = capacity_for({**method, "zone": "Z1", "position": .35, "structure": "CONTINUOUS"},
                                             settings, speed, context, today, profile.get("allow_expert_fallback", True), use_model_prior=(controls or {}).get("capacity_policy") == "MODEL_WITH_PRIOR")
                    if secondary is None:
                        continue
                    evidence["secondary_capacity"] = secondary
                purpose = method["purpose"]
                # Easy endurance can need a full building-method dose to carry
                # the weekly load, even while another quality is the accent.
                # This uses the existing coach fraction, never a new multiplier.
                endurance_allocation = False
                if allocation and z in {"Z1", "Z2"} and purpose == "BUILDING" and not taper and slot_index == 0:
                    maintenance_work = min(evidence["capacity_minutes"] * profile.get("maintenance_fraction", .3), method["max_work_min"])
                    maintenance_blocks = _blocks(method, maintenance_work, evidence, settings)
                    if maintenance_blocks:
                        _, maintenance_load, _ = candidate_load(maintenance_blocks)
                        endurance_allocation = allocation[z] > maintenance_load[z] + .5
                background_development = bool(cycle_state and cycle_state.get("background_development") and mesocycle_focus.growth_weight(cycle_state, z) > 0)
                if purpose == "BUILDING" and (z not in selected_accents and not endurance_allocation and not background_development or taper or slot_index > 0 or cycle_state and cycle_state["kind"] == "RECOVERY"):
                    purpose = "MAINTENANCE"
                fraction = profile.get("maintenance_fraction", .3) if purpose in {"MAINTENANCE", "RECOVERY", "SUPPORTING"} else profile.get("reentry_fraction", .4) if period == "RE_ENTRY" else profile.get("building_fraction", .5)
                if method["structure"] == "MODEL_INTERVALS":
                    p = method["interval_template"]
                    fraction = p["total_capacity_ratio"] if purpose == "BUILDING" else p["min_repetitions"] * p["work_seconds"] / (60 * evidence["capacity_minutes"])
                elif method["structure"] == "METABOLIC_INTERVALS":
                    p = method["interval_profile"]
                    # Maintenance selects the minimum complete variant from
                    # the same approved profile; no second role multiplier.
                    fraction = p["total_capacity_ratio"] if purpose == "BUILDING" else p["min_repetitions"] * p["work_seconds"] / (60 * evidence["capacity_minutes"])
                elif z == "STR":
                    fraction = method["min_work_min"] / method["max_work_min"] if purpose == "MAINTENANCE" else 1.
                requested = evidence["capacity_minutes"] * fraction
                dose_ceiling = (progression or {}).get("config", {}).get("max_dose_fraction", .8)
                if (progression and allocation and not limited and not taper and session_limit <= 3 and purpose == "BUILDING"
                        and method["structure"] in {"CONTINUOUS", "TWO_REPETITIONS", "THRESHOLD_REPETITIONS"}):
                    trial = _blocks(method, requested, evidence, settings)
                    if trial and allocation[z] > candidate_load(trial)[1][z] + .5:
                        fraction = dose_ceiling
                        requested = evidence["capacity_minutes"] * fraction
                        evidence["dose_expanded_for_limited_sessions"] = True
                if method["structure"] in {"ALTERNATING", "CRUISE_ALTERNATING", "AEROBIC_STRENGTH"}:
                    secondary = evidence["secondary_capacity"]
                    if method["structure"] in {"ALTERNATING", "CRUISE_ALTERNATING"}:
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
                overhead = (method["warmup_min"] + method["cooldown_min"] + method.get("recovery_min", 0.)) * (2 if method.get("double_threshold") else 1)
                if method.get("paired_method"):
                    other = method["paired_method"]
                    overhead = sum(m["warmup_min"] + m["cooldown_min"] + m.get("recovery_min",0.) for m in (method,other))
                limits = [{"code": "METHOD_WORK_CAP", "limit_minutes": method["max_work_min"]},
                          {"code": "TECHNICAL_SESSION_CEILING" if availability_mode == "AUTO_HISTORY" else "DAILY_AVAILABLE_WORK", "limit_minutes": max(0., day_available - overhead)}]
                if not automatic_time:
                    limits.append({"code": "REMAINING_WEEKLY_WORK", "limit_minutes": max(0., remaining - overhead)})
                session_ceiling = float("inf")
                if controls and not is_strength:
                    # The old cumulative weekly ceiling incorrectly made the
                    # athlete's total programme equal the history of one means.
                    # Keep the observed session exposure separate from total
                    # volume and from the capacity estimate for this exact means.
                    observed = volume_evidence["max_session_minutes_by_sport"].get(sport, 0.)
                    if not progression or not observed:
                        session_ceiling = observed * max(1., day_meso_factor) if observed else profile.get("recovery_session_cap_min", 30.) + overhead
                        limits.append({"code": "ACTUAL_SPORT_SESSION_EXPOSURE", "limit_minutes": max(0., session_ceiling*(2 if method.get("double_threshold") else 1)-overhead)})
                if event_specificity["work_cap_min"] is not None:
                    limits.append({"code": "RACE_DURATION_WORK_CAP", "limit_minutes": event_specificity["work_cap_min"]})
                if controls and not is_key and not is_strength and not automatic_time:
                    future_long = [day + timedelta(days=n) for n in range(1, (end_date-day).days+1)
                                   if (day+timedelta(days=n)).weekday() == controls.get("long_session_day")]
                    if future_long:
                        reserve = min(available[future_long[0].weekday()], weekly_ceiling/max(1, sum(v>0 for v in available))*1.5)
                        limits.append({"code": "RESERVE_LONG_SESSION_TIME", "limit_minutes": max(0., remaining-reserve-overhead)})
                if not is_key and not automatic_time:
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
                    time_limited = not automatic_time and remaining < overhead + method["min_work_min"]
                    item["rejected_alternatives"].append({"method_id": method["id"],
                        "code": "INSUFFICIENT_TIME_BUDGET" if time_limited else "INSUFFICIENT_DOSE_BUDGET",
                        "reason": (f"Остават {max(0., remaining):g} минути от лимита за периода; методът изисква поне {overhead + method['min_work_min']:g} минути с подготвителните части."
                                   if time_limited else "Оставащият бюджет е под минималната работна доза на метода.")})
                    continue
                blocks = _blocks(method, work, evidence, settings)
                # An interval's denominator is the WHOLE approved structure's
                # work capacity. Mixed components share the same fraction.
                max_usage = min(dose_ceiling, profile.get("maintenance_fraction", .3)) if supporting or cycle_state and cycle_state["kind"] == "RECOVERY" else dose_ceiling
                while blocks and _dose_usage(blocks, evidence, z) > max_usage + 1e-6 and work >= method["min_work_min"]:
                    work -= .5
                    blocks = _blocks(method, work, evidence, settings) if work >= method["min_work_min"] else []
                evidence.update(max_dose_fraction=max_usage, dose_capacity_basis="WHOLE_STRUCTURE_WORK_CAPACITY")
                limits.append({"code": "WHOLE_STRUCTURE_DOSE_FRACTION", "limit_minutes": work})
                # Whole repetitions/circuits, active rests and transitions all
                # have to fit. Lower work never authorizes a longer rest.
                while work >= method["min_work_min"] and blocks and sum(b["duration_min"] for b in blocks) > min(day_available, remaining, session_ceiling * (2 if method.get("double_threshold") else 1)) + .001:
                    work -= .5
                    blocks = _blocks(method, work, evidence, settings)
                if not blocks or work < method["min_work_min"]:
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": "STRUCTURE_DOES_NOT_FIT", "reason": "Цели повторения, паузи и общата доза не се побират едновременно."})
                    continue
                direct, effective, technical = candidate_load(blocks)
                # Allocate this opportunity's share before applying hard 7/40
                # caps. Whole minimum variants may cross a soft share, never a
                # rolling budget. Later slots see the resulting canonical load.
                if allocation and effective[z] > allocation[z] and not method.get("double_threshold"):
                    minimum_blocks = _blocks(method, method["min_work_min"], evidence, settings)
                    if minimum_blocks:
                        _, minimum_load, _ = candidate_load(minimum_blocks)
                        share = max(allocation[z], minimum_load[z])
                        lo, hi = method["min_work_min"], work
                        for _ in range(16):
                            midpoint = (lo+hi)/2
                            trial = _blocks(method, midpoint, evidence, settings)
                            _, trial_load, _ = candidate_load(trial)
                            if trial and trial_load[z] <= share + .001:
                                lo = midpoint
                            else:
                                hi = midpoint
                        work = max(method["min_work_min"], math.floor(lo*2)/2)
                        blocks = _blocks(method, work, evidence, settings)
                        direct, effective, technical = candidate_load(blocks)
                        limits.append({"code": "COMPONENT_SLOT_ALLOCATION", "limit_minutes": _round(work)})
                if not limited:
                    def fits_component_budget(candidate, candidate_q):
                        return (all(candidate[z] <= budgets[z]["deficit_effective"] + .001 for z in COMPONENTS)
                                and all(candidate_q[z] <= available_q + .001 for z, available_q in q_remaining.items()))
                    if not fits_component_budget(effective, direct):
                        minimum_blocks = _blocks(method, method["min_work_min"], evidence, settings)
                        minimum_q, minimum_load, _ = candidate_load(minimum_blocks)
                        if not minimum_blocks or not fits_component_budget(minimum_load, minimum_q):
                            q_blockers = [z for z,v in q_remaining.items() if minimum_q[z] > v + .001]
                            item["rejected_alternatives"].append({"method_id": method["id"], "code": "DIRECT_Q_PROGRESSION_BUDGET",
                                "reason": "Минималният вариант надвишава оставащия приравнен обем Q: " + "; ".join(f"{z}: нужни {minimum_q[z]:.1f}, остават {q_remaining[z]:.1f} мин" for z in q_blockers),
                                "blocking_components": q_blockers} if q_blockers else _budget_rejection(method["id"], minimum_load, budgets, profile))
                            continue
                        lo, hi = method["min_work_min"], work
                        for _ in range(24):
                            midpoint = (lo + hi) / 2
                            trial_blocks = _blocks(method, midpoint, evidence, settings)
                            trial_q, trial_effective, _ = candidate_load(trial_blocks)
                            if trial_blocks and fits_component_budget(trial_effective, trial_q):
                                lo = midpoint
                            else:
                                hi = midpoint
                        work = math.floor(lo * 2) / 2
                        limits.append({"code": "ROLLING_Q_AND_7_40_BUDGET", "limit_minutes": _round(work)})
                        if work < method["min_work_min"]:
                            item["rejected_alternatives"].append(_budget_rejection(method["id"], effective, budgets, profile))
                            continue
                        blocks = _blocks(method, work, evidence, settings)
                        if not blocks:
                            item["rejected_alternatives"].append({"method_id": method["id"], "code": "MINIMUM_STRUCTURE_NOT_MET", "reason": "Бюджетът не допуска минималната цяла структура."})
                            continue
                        direct, effective, technical = candidate_load(blocks)
                after_rows = _add_forecast_session(forecast_rows, day, effective)
                near_races = [e for e in events if e["event_type"] in {"MAIN_RACE", "CONTROL_RACE"}
                              and key < str(e["start_date"]) <= (day + timedelta(days=7)).isoformat()]
                def recovery_conflict(candidate_rows):
                    for race in near_races:
                        forecast = recovery_v2.simulate(candidate_rows, configs["zones"], target=date.fromisoformat(str(race["start_date"])))
                        if any(r["readiness_percent"] < 90. for r in forecast["current"] if r["zone"] != "STR"):
                            return "RACE_RECOVERY_CONFLICT", "Прогнозното възстановяване след тази доза не достига 90% преди близък старт."
                    if not is_key and any(
                        any(r["readiness_percent"] < 90 for r in recovery_v2.simulate(candidate_rows, configs["zones"], target=d)["current"] if r["zone"] in {"Z1", *selected_accents})
                        for d in key_slots if day < d <= day + timedelta(days=2)
                    ):
                        return "RESERVE_KEY_SESSION_RECOVERY", "Леката добавка би затруднила готовността за предстоящата ключова сесия."
                    return None
                conflict = recovery_conflict(after_rows)
                if conflict and allocation is not None:
                    # A large optional dose failing a future readiness check
                    # does not mean its minimum complete variant must fail.
                    minimum_blocks = _blocks(method, method["min_work_min"], evidence, settings)
                    _, minimum_load, _ = candidate_load(minimum_blocks)
                    if minimum_blocks and not recovery_conflict(_add_forecast_session(forecast_rows, day, minimum_load)):
                        lo, hi = method["min_work_min"], work
                        while hi-lo > .25:
                            midpoint = (lo+hi)/2
                            trial = _blocks(method, midpoint, evidence, settings)
                            _, trial_load, _ = candidate_load(trial)
                            if trial and not recovery_conflict(_add_forecast_session(forecast_rows, day, trial_load)):
                                lo = midpoint
                            else:
                                hi = midpoint
                        work = max(method["min_work_min"], math.floor(lo*2)/2)
                        blocks = _blocks(method, work, evidence, settings)
                        direct, effective, technical = candidate_load(blocks)
                        after_rows = _add_forecast_session(forecast_rows, day, effective)
                        conflict = recovery_conflict(after_rows)
                        limits.append({"code": "RECOVERY_RESERVATION_WORK_CAP", "limit_minutes": _round(work)})
                if conflict:
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": conflict[0], "reason": conflict[1]})
                    continue
                after = recovery_v2.simulate(after_rows, configs["zones"], target=day)
                total = sum(b["duration_min"] for b in blocks)
                actual_work = sum(b["duration_min"] for b in blocks if b["kind"] == "WORK")
                evidence.update(fraction=fraction, requested_work_minutes=_round(requested + evidence.get("combination_high_work_cap", 0.)), prescribed_work_minutes=_round(actual_work),
                                applied_structure_fraction=_round(_dose_usage(blocks, evidence, z)),
                                requested_primary_work_minutes=_round(requested),
                                primary_work_budget_minutes=_round(work), applied_fraction=_round(work / evidence["capacity_minutes"]), dose_reduced=work + .01 < requested,
                                limits=limits, technical_spill_reference={z: _round(v) for z, v in technical.items()},
                                technical_spill_reference_role="CANONICAL_E_ONLY_NOT_DOSE_CAPACITY",
                                source_method_id=method["source_id"], source_method_version=method["source_version"],
                                explanation="Капацитетът е от индивидуалната крива, когато целта е подкрепена от тестове и ТИ; иначе от видимата експертна референция. Делът на метода и дневният/седмичният бюджет ограничават работата. Recovery е отделна проверка, а не множител 0,90.")
                if method["structure"] == "MODEL_INTERVALS":
                    evidence["explanation"] = "Метаболитният профил задава общия работен бюджет, повторенията, паузите и резерв от две отсечки. Процентите за Z1–Z3 не се прилагат втори път. Сумарната работа може да надхвърли непрекъснатата устойчивост само в границите на този профил. Поддържането използва минималния цял вариант. Числата са видими начални треньорски настройки; 7/40 и Recovery могат да намалят дозата или да отложат сесията."
                elif method["structure"] == "METABOLIC_INTERVALS":
                    evidence["explanation"] = "Устойчивостта е индивидуално зададена за описаното усилие и средство. Цели повторения и активни паузи използват общия методен бюджет. Пулсът е наблюдение; не определя усилието или дозата. Прогнозният приравнен товар използва горната зонова референция и ще бъде заменен с реалното изпълнение."
                elif z == "STR":
                    evidence["explanation"] = "Дозата е от отделния силов профил, без метаболитен Tref. Работата, преходите и почивките се отчитат веднъж в силовата експозиция; загрявката и разпускането имат собствен аеробен товар. Остават поне 3 качествени повторения в резерв."
                if method["structure"] in {"ALTERNATING", "CRUISE_ALTERNATING", "AEROBIC_STRENGTH", "THRESHOLD_HIGH"}:
                    evidence["combination_allocation"] = "ONE_SHARED_SESSION_BUDGET_REDUCED_COMPONENT_DOSES"
                    evidence["explanation"] += " Комбинираните части споделят дозата и общото време; не получават две пълни изграждащи дози."
                if method.get("double_threshold"):
                    evidence["combination_allocation"] = "TWO_SESSIONS_ONE_THRESHOLD_WORK_BUDGET"
                    evidence["explanation"] += " Двойният праг дели една обща работна доза между две сесии със собствени загрявки, почивки и разпускане."
                if z != "STR":
                    evidence["explanation"] += f" Общият дял на цялата работна структура е до {max_usage*100:g}% от съответния капацитет."
                normalized_deficit = budgets[z]["deficit_effective"] / max(1., budgets[z]["target_weekly_effective"])
                priority_weight = mesocycle_focus.growth_weight(cycle_state, z) if cycle_state and cycle_state.get("component_indices") else float(z in selected_accents)
                score = (2. * planning_allocation.coverage(effective, allocation, selected_accents)
                         if allocation is not None else normalized_deficit) + priority_weight
                if period in {"PRECOMPETITION", "COMPETITION"} and sport == profile.get("actual_sport"):
                    score += .25
                # Cover qualities over actual execution plus this proposed
                # week, counting WORK blocks, never warm-up/cascade as coverage.
                trained_dates = {a["date"] for a in source.get("activities", []) if
                    (today-timedelta(days=28)).isoformat() <= a["date"] <= today.isoformat()
                    and ((z == "STR" and a.get("sport") == "WeightTraining") or any(r["zone"] == z and r.get("raw_time_min", 0) >= 1 for r in a.get("zones", [])))}
                proposed_dates = {d["date"] for d in result_days if d.get("session") and
                    sum(b["duration_min"] for b in d["session"]["blocks"] if b["kind"] == "WORK" and b["zone"] == z) >= 1}
                last_stimulus = max(trained_dates | proposed_dates, default=None)
                since = (day-date.fromisoformat(last_stimulus)).days if last_stimulus else 28
                maintenance_need = min(3., since/7)
                score += maintenance_need - (0. if allocation is not None else 2.5*len(proposed_dates))
                if is_key and len(key_slots) > 1 and day == key_slots[-1] and z not in selected_accents:
                    score += 1.5
                evidence["selection"] = {"accent": z in selected_accents, "role": purpose,
                                         "days_since_component_work": since, "proposed_component_days": len(proposed_dates),
                                         "maintenance_priority": maintenance_need}
                if allocation is not None:
                    evidence["selection"].update(component_allocation={k: _round(v) for k, v in allocation.items()},
                                                   coverage_score=_round(planning_allocation.coverage(effective, allocation, selected_accents)),
                                                   endurance_dose_from_weekly_need=endurance_allocation)
                    if endurance_allocation and purpose == "BUILDING" and z not in selected_accents:
                        evidence["explanation"] += " За необходимия седмичен аеробен обем е използвана изграждащата доза на метода, въпреки че компонентът не е основен акцент. Дозата остава в зададения треньорски процент и споделя общия бюджет."
                if is_strength and preferences.get("strength_days") and day.weekday() in preferences["strength_days"]:
                    score += 3.
                if preferences.get("long_session_day") == day.weekday() and z in {"Z1", "Z2"} and purpose != "RECOVERY":
                    score += 2.
                if controls and not is_strength:
                    score += .5 * max(0., 1.-sport_spent.get(sport, 0.)/max(1., by_sport_minutes.get(sport, 0.)))
                if purpose == "RECOVERY":
                    score -= 1.
                if purpose == "BUILDING":
                    score += .1
                if is_key and day in key_slots:
                    score += 3.
                if method["structure"] in {"ALTERNATING", "CRUISE_ALTERNATING", "AEROBIC_STRENGTH", "THRESHOLD_HIGH"}:
                    score += .15
                if any(d.get("session", {}).get("method_id") == method["id"] for d in result_days if d.get("session")):
                    score -= .02 if allocation is not None else .35
                if method.get("double_threshold"):
                    score += 10.
                choices.append((score, method["id"], {"method_id": method["id"], "title": method["title"],
                                "sport": sport, "zone": z, "purpose": purpose, "blocks": blocks, "is_key_session": is_key,
                                "double_threshold": method.get("double_threshold", False),
                                "paired_session": {"zone":method["paired_method"]["zone"], "method_id":method["paired_method"]["id"]} if method.get("paired_method") else None,
                                "main_work_minutes": _round(actual_work), "total_minutes": _round(total),
                                "canonical_effective_load": {z: _round(v) for z, v in effective.items()},
                                "direct_equivalent_minutes": {z: _round(v) for z, v in direct.items()},
                                "dose_evidence": evidence}, after_rows, after))
            if choices:
                choices.sort(key=lambda c: (-c[0], c[1]))
                _, _, session, forecast_rows, after = choices[0]
                after_ready = {r["zone"]: _round(r["readiness_percent"]) for r in after["current"]}
                parts = _session_parts(session, settings, day_start_rows, day, slot_index)
                covered_slots[day] = slot_index + len(parts)
                item.update(status="TRAINING", session=parts[0], sessions=parts,
                            readiness_after=after_ready if forecast_known else {z: None for z in COMPONENTS},
                            explanation=f"{session['title']}: {session['main_work_minutes']:g} минути основна работа, {session['total_minutes']:g} минути общо. " + ("Развитие на акцентния компонент." if session["purpose"] == "BUILDING" else "Поддържане на компонента извън основния акцент." if session["purpose"] == "MAINTENANCE" else "Лека възстановителна задача.") + " Дозата е проверена спрямо капацитета, 7/40 и Recovery.")
                item["rejected_alternatives"] += [{"method_id": c[1], "code": "LOWER_CURRENT_PRIORITY", "reason": "Допустим метод с по-нисък текущ приоритет; не се добавя втора пълна доза."} for c in choices[1:]]
                remaining -= session["total_minutes"]
                if session["zone"] != "STR":
                    sport_spent[session["sport"]] = sport_spent.get(session["sport"], 0.) + session["total_minutes"]
                sessions += len(parts)
                if session["zone"] == "STR":
                    strength_sessions += 1
                    last_strength_day = day
                if session.get("is_key_session", session["zone"] in {"Z3", "Z4", "Z5"}):
                    key_sessions += len(parts)
                    last_key_day = day
            else:
                item["explanation"] = "Няма метод с едновременно подходяща доза, бюджет и готовност. Почивката е допустим резултат."
                manual_blockers = sorted({z for r in item["rejected_alternatives"] for z in r.get("manual_target_components", [])})
                if manual_blockers:
                    item["explanation"] = ("Ръчна цел за " + ", ".join(manual_blockers)
                        + " ограничава тренировките. Провери „Индивидуални цели по компоненти“ в профила. "
                        "Стойностите са приравнени минути за 7 дни; ниска цел за Z1 ограничава и по-високите аеробни зони.")
        if day_spent and item["session"] is None:
            after_day = recovery_v2.simulate(forecast_rows, configs["zones"], target=day)
            item["readiness_after"] = {r["zone"]: _round(r["readiness_percent"]) if forecast_known else None for r in after_day["current"]}
        result_days.append(item)
        if item["session"] is None and item["status"] in {"REST", "UNAVAILABLE"} and not day_spent:
            forecast_rows = _with_forecast_day(forecast_rows, day, {})
    grouped = {}
    for item in result_days:
        key = item["date"]
        if key not in grouped:
            grouped[key] = {**item, "sessions": list(planning_schedule.day_sessions(item))}
        else:
            combined = grouped[key]
            combined["sessions"].extend(planning_schedule.day_sessions(item))
            combined["readiness_after"] = item["readiness_after"]
            combined["rejected_alternatives"].extend(item["rejected_alternatives"])
        combined = grouped[key]
        if combined["sessions"]:
            combined["session"] = combined["sessions"][0]
            combined["status"] = "TRAINING"
            combined["explanation"] = f"{len(combined['sessions'])} сесии · {sum(v['total_minutes'] for v in combined['sessions']):g} минути общо. Общ бюджет по компоненти; готовността е оценена за началото на деня."
    result_days = list(grouped.values())
    planned_sessions = [s for d in result_days for s in planning_schedule.day_sessions(d)]
    allocation_report = planning_allocation.report(goal_windows, rows, forecast_rows, result_days,
        sum(slot_counts.values()), session_limit, weekly_minutes) if component_governed and forecast_known and not blocked else None
    parameters = {"version": PARAMETER_VERSION, "race_duration": event_duration, "status": "COACH_HEURISTICS_FOR_REVIEW",
                  "recovery_mode": "LOAD_ONLY", "ready_threshold_percent": 90, "load_progression": load_progression.public_context(progression),
                  "building_fraction": profile.get("building_fraction", .5), "maintenance_fraction": profile.get("maintenance_fraction", .3),
                  "reentry_fraction": profile.get("reentry_fraction", .4), "recovery_session_cap_min": profile.get("recovery_session_cap_min", 30),
                  "weekly_volume_source": volume_source, "baseline_weekly_minutes": _round(weekly_minutes),
                  "historical_selected_weekly_minutes": _round(historical_weekly_minutes),
                  "historical_training_weekly_minutes": volume["historical_training_weekly_minutes"],
                  "available_weekly_minutes": sum(available) if availability_mode == "MANUAL" else None,
                  "availability_mode": availability_mode, "history_policy": history_policy, "training_sports": training_sports,
                  "planning_controls": controls, "volume_evidence": volume_evidence, "horizon": horizon_context,
                  "schedule_version": planning_schedule.VERSION, "max_sessions_per_day": 3,
                  "component_allocation_version": planning_allocation.VERSION,
                  "weekly_minutes_ceiling": None if automatic_time else _round(weekly_ceiling),
                  "weekly_time_limit_minutes": controls["weekly_target_hours"]*60 if controls and controls.get("weekly_target_hours") is not None else None,
                  "time_budget": None if automatic_time else {
                      "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
                      "period_limit_minutes": _round(weekly_ceiling),
                      "actual_minutes": _round(actual_window_minutes),
                      "planned_minutes": _round(sum(s["total_minutes"] for s in planned_sessions)),
                      "remaining_minutes": _round(max(0., remaining)),
                      "actual_excess_minutes": _round(max(0., actual_window_minutes-weekly_ceiling)),
                  },
                  "volume_governor": "COMPONENT_7_40" if component_governed else "LIMITED_HISTORY_ENVELOPE",
                  "mesocycle_week_index": meso_week,
                  "mesocycle_factor": meso_factor, "mesocycle_length_weeks": meso_length,
                  "mesocycle_factor_policy": "COMPONENT_7_40_TARGETS" if component_governed else "ACTUAL_REFERENCE_BOUNDED_DEVELOPMENT_DELOAD_0.78",
                  "automatic_volume_progression": not component_governed, "component_target_modulation": component_governed,
                  "progression_percent": profile.get("progression_percent", 5), "target_reference": target_reference,
                  "targets_version": training_targets.VERSION, "reserved_key_dates": [d.isoformat() for d in key_slots],
                  "taper_volume_factor": .5, "minimum_test_anchors": 2, "minimum_zone_index_activities": 3,
                  "max_index_age_days": 14, "history_days_for_unrestricted_draft": planning_history.MINIMUM_DAYS,
                  "z1_working_band_width_bpm": Z1_WORKING_BAND_WIDTH_BPM,
                  "recovery_config_revision": configs["expected_revision"], "recovery_settings": configs["zones"],
                  "expert_continuous_capacity_upper_edge_min": {z: sum(r) / 120 for z, r in hr_speed.TMAX_RANGES_S.items()},
                  "canonical_effective_load": {k: fresh_parameters()[k] for k in ("cascade", "spill_fraction", "spill_threshold_fraction")}}
    provenance = {"generation_id": envelope.get("generation_id"), "revision": envelope.get("revision"),
                  "as_of": as_of, "history_days": history_days, "quality": quality,
                  "component_history_days": component_history_days,
                  "speed_model_version": speed_by_sport[primary_sport].get("model_version") if speed_by_sport[primary_sport] else None,
                  "speed_models_by_sport": {s: {"version": v.get("model_version"), "status": v.get("status"), "active_test_keys": v.get("active_test_keys", [])} if v else None for s,v in speed_by_sport.items()},
                  "recovery_model_version": recovery_v2.VERSION, "method_catalog_version": METHODS_VERSION,
                  "speed_active_test_keys": speed_by_sport[primary_sport].get("active_test_keys", []) if speed_by_sport[primary_sport] else [],
                  "readiness_known": all_sources_known, "known_history_only_forecast": not all_sources_known,
                  "snapshot_fingerprint": _hash(snapshot), "settings_fingerprint": _hash({"bounds": settings.zone_bounds_bpm, "hrmax": settings.hrmax_bpm, "timezone": settings.timezone})}
    fingerprint = _hash({"engine": VERSION, "source": provenance, "profile": profile, "events": events,
                         "preferences": preferences, "accents": accents, "parameters": parameters,
                         "speed": speed_by_sport, "start_date": start_date.isoformat()})
    all_blocked = all(d["status"] == "REVIEW_REQUIRED" for d in result_days)
    return {"schema_version": "planning-draft-v1", "engine_version": VERSION, "fingerprint": fingerprint,
            "status": "BLOCKED" if all_blocked else "LIMITED_DRAFT" if limited or blocked else "DRAFT",
            "generated_at": now.isoformat(), "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
            "activation_eligible": activation_eligible,
            "source": provenance, "periodization": periodization, "days": result_days,
            "long_term": _long_term_outlook(profile, periodization, target_reference, accents, preferences, rows, today, limited, volume=volume, events=events, progression=progression),
            "parameters": parameters, "warnings": warnings, "catalog": catalog(profile),
            "allocation": allocation_report,
            "history_comparison": volume_evidence["weeks"],
            "component_history": load_progression.history(source, rows, today),
            "summary": {"sessions": len(planned_sessions),
                        "key_sessions": sum(s.get("is_key_session", s["zone"] in {"Z3", "Z4", "Z5"}) for s in planned_sessions),
                        "actual_sessions": len(existing_in_draft),
                        "planned_minutes": _round(sum(s["total_minutes"] for s in planned_sessions)),
                        "unused_weekly_minutes": None if automatic_time else _round(max(0., remaining)), "requires_review": True}}
