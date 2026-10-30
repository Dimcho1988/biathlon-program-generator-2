"""Reviewable seven-day physical-training drafts using the current model stack.

Versioned candidates for the active planner, with bounded individual learning.
Capacity (speed-duration / expert continuous Tmax) is explicitly separate from
historical C40 and the technical denominator used by canonical E spillover.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
import json
import math
from zoneinfo import ZoneInfo

import pandas as pd

from biathlon import hr_speed, recovery_v2, speed_duration, training_targets, planning_controls, planning_history, planning_schedule, planning_allocation, load_progression, mesocycle_focus, training_return
from biathlon.constants import COMPONENTS, DEFAULT_PARAMETERS, fresh_parameters
from biathlon import method_dosing, planning_continuity, planning_envelope, planning_q
from biathlon.method_dosing import continuous_building_fraction, continuous_maintenance_fraction, recovery_factor, threshold_allowed
from biathlon.component_load import VERSION as COMPONENT_LOAD_VERSION, DOWNWARD_FACTORS, effective_upper_bound
from biathlon.equivalence import DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM, equivalence_slope
from biathlon import speed_anchor_policy
from biathlon.periodization import build_periodization, reentry_dose_active
from biathlon.physiology import _causal_tref, effective_from_direct_vector, linear_equivalence_coefficient
from biathlon.training_methods import METHODS, EXERCISES, VERSION as METHODS_VERSION, catalog, resolved_methods, method_family
from biathlon import training_guidance, adaptive_methods, preliminary_capacity, planning_consistency, race_specific, dosing_curve, method_library, race_course, execution_ranges
from .component_load_context import context_from_speed_view, read_contexts
from .component_load_projection import project_history
from . import model_service, load_adaptation, race_duration, planning_history_estimate, training_sessions
from biathlon import method_registry, combined_methods, strength_methods, method_anchors
from .learning_methods import method_descriptor
from .response_service import ResponseStore
from .management_projection import public_learning, public_management
from .planning_diagnostics import compact_rejections
from . import plan_speed_comparison

VERSION = "training-management-v68-quality-first-weekly-doses"
PARAMETER_VERSION = "management-parameters-v58"
MIN_AEROBIC_DOSE_FRACTION = .25  # Explicit coach rule, not a physiological threshold.
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


def _metadata_sessions(day):
    sessions = list(planning_schedule.day_sessions(day))
    legacy = day.get("session")
    # JSON storage separates the legacy alias from sessions[0]. Compact both
    # local copies without assuming that different aliases are equal.
    if isinstance(legacy, dict) and all(legacy is not session for session in sessions):
        sessions.append(legacy)
    return sessions


def _compact_duplicate_dosing_cards(days):
    """Keep identical native dosing settings once in the frozen session.

    Source cards and all guidance remain complete. This is an explicit local
    reference to equal serialized data, not a reference to a changing catalog.
    """
    for day in days:
        if day.get("locked"):
            continue
        for session in _metadata_sessions(day):
            card = session.get("method_library") or {}
            dosing = (session.get("dose_evidence") or {}).get("dosing")
            if card.get("registry_kind") == "COACH_PROFILE" and dosing and card.get("dosing") == dosing:
                card.pop("dosing")
                card["dosing_ref"] = "dose_evidence.dosing"


def _compact_parent_method_cards(days):
    """Each identical parent card already lives in the frozen combined method."""
    for day in days:
        if day.get("locked"):
            continue
        for session in _metadata_sessions(day):
            components = (session.get("method_library") or {}).get("components") or []
            references = {_hash(card): index for index, card in enumerate(components)
                          if isinstance(card, dict)}
            for block in session.get("blocks", []):
                card = block.get("parent_method_card")
                index = references.get(_hash(card)) if isinstance(card, dict) else None
                if index is not None:
                    block.pop("parent_method_card")
                    block["parent_method_card_ref"] = f"method_library.components[{index}]"


def compact_plan_metadata(plan):
    """Fold equal local evidence in visible and provisional days, losslessly.

    Also apply this after a durable cache hit: an older completed calculation
    has the same prescription but can still carry repeated JSON metadata.
    Locked approved days and unequal source cards remain intact.
    """
    days = [*plan.get("days", []), *plan.get("lookahead_days", [])]
    _compact_duplicate_dosing_cards(days)
    _compact_parent_method_cards(days)
    _compact_goal_cycles(days)


def _compact_goal_cycles(days):
    """Keep identical cycle evidence once on its day, preserving the source.

    Goal dictionaries also belong to the frozen target windows. Replace the
    presentation copy only; do not strip metadata out of those shared targets.
    """
    for day in days:
        if day.get("locked") or not isinstance(day.get("cycle"), dict):
            continue
        for budget in day.get("load_budget", {}).get("components", {}).values():
            evidence = budget.get("goal_evidence")
            if isinstance(evidence, dict) and evidence.get("cycle") == day["cycle"]:
                budget["goal_evidence"] = {k: v for k, v in evidence.items() if k != "cycle"}
                budget["goal_evidence"]["cycle_ref"] = "day.cycle"


def _warning(code, message):
    return {"code": code, "message": message}


def _strength_duration(blocks):
    """Main strength exposure includes inter-set rests, never preparation.

    Active work remains separate for whole-repetition/method dosing. A rest
    inside a strength method is exposure even when it is explicitly passive.
    """
    main = [b for b in blocks if b["zone"] == "STR"
            and b["kind"] not in {"WARMUP", "COOLDOWN", "PREPARATION"}]
    work = sum(b["duration_min"] for b in main if b["kind"] == "WORK")
    pauses = sum(b["duration_min"] for b in main if b["kind"] != "WORK")
    return {"work_minutes": _round(work), "rest_minutes": _round(pauses),
            "main_minutes": _round(work+pauses), "basis": "WORK_AND_INTERSET_REST_EXCLUDING_WARMUP_COOLDOWN"}


def _race_recovery_warnings(events, days, rows, configs, *, calculation_cache=None):
    """Review the complete selected schedule; race readiness is advisory."""
    training_dates = [date.fromisoformat(d["date"]) for d in days if d.get("sessions")]
    unknown_dates = [date.fromisoformat(d["date"]) for d in days
                     if d["status"] in {"RACE", "REVIEW_REQUIRED"}]
    warnings, seen = [], set()
    for event in events:
        if event["event_type"] not in {"MAIN_RACE", "CONTROL_RACE"}:
            continue
        race_day = date.fromisoformat(str(event["start_date"]))
        if race_day in seen or not any(0 < (race_day-d).days <= 7 for d in training_dates):
            continue
        if any(d < race_day for d in unknown_dates):
            continue  # An earlier unmeasured race is not a zero-load rest day.
        seen.add(race_day)
        forecast = recovery_v2.simulate(rows, configs, target=race_day, include_details=False,
            include_deadlines=False, calculation_cache=calculation_cache)
        low = {r["zone"]: _round(r["readiness_percent"]) for r in forecast["current"]
               if r["zone"] != "STR" and r["readiness_percent"] < 90.}
        if low:
            details = ", ".join(f"{z}: {v:g}%" for z,v in low.items())
            warnings.append({**_warning("RACE_RECOVERY_WARNING",
                f"Прогнозното възстановяване преди старта на {race_day.strftime('%d.%m.%Y')} е под 90% ({details}). "
                "Предупреждение за треньорски преглед; не блокира и не намалява дозата. Прогнозата използва известния и планирания товар."),
                "race_date": race_day.isoformat(), "components": low, "blocking": False})
    return warnings


def _budget_rejection(method_id, effective, budgets, profile):
    blocked = [z for z in COMPONENTS if effective[z] > budgets[z]["deficit_effective"] + .001]
    manual = [z for z in blocked if z in profile.get("component_targets_weekly", {})]
    details = "; ".join(f"{z}: нужни {effective[z]:.1f}, остават {budgets[z]['deficit_effective']:.1f}" for z in blocked)
    index_blocked = [z for z in blocked if budgets[z].get("effective_limit_basis") == "PROSPECTIVE_CANONICAL_7_40"]
    reason = (f"Прогнозният 7/40 ограничава минималния цял вариант: {details} минути E."
              if index_blocked else f"Няма бюджет за минималния цял вариант: {details} приравнени минути.")
    if manual:
        reason += " Ръчната цел за " + ", ".join(manual) + " замества автоматичната цел от историята и 7/40."
    return {"method_id": method_id, "code": "INDEX_7_40_LIMIT" if index_blocked else "COMPONENT_BUDGET_EXHAUSTED", "reason": reason,
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


@dataclass(frozen=True)
class PlanningSourceResult:
    """Separate measured projection from temporary planning-only additions."""

    source: dict
    evidence: dict
    measured_source: dict


def planning_source(repository, alias, profile, settings, envelope, today, *, speed_by_sport=None):
    """Retain the existing two-value planning-source interface."""
    result = planning_source_details(repository, alias, profile, settings, envelope, today,
                                     speed_by_sport=speed_by_sport)
    return result.source, result.evidence


def planning_source_details(repository, alias, profile, settings, envelope, today, *, speed_by_sport=None):
    """Project measured Q once, retaining it before declared rest or estimates."""
    measured = (envelope.get("snapshot_payload") or {}).get("load_history") or {}
    calendar = planning_history_estimate.normalized_calendar(envelope.get("activities", []))
    sports = list(dict.fromkeys(s for s in [
        *(a.get("sport") for a in [*measured.get("activities", []), *calendar]),
        profile.get("actual_sport") or profile.get("sport"),
        *((profile.get("planning_controls") or {}).get("training_sports") or []),
    ] if s and s != "Активност"))
    contexts = read_contexts(repository, alias, sports, speed_by_sport=speed_by_sport)
    if measured:
        from .actual_load_projection import reconstruct_history
        measured = reconstruct_history(repository, alias, measured)
        measured = project_history(measured, contexts)
    projected_measured = measured
    measured = training_return.confirmed_source(measured, profile, today, calendar)
    quality = measured.get("quality") or {}
    estimates, speed_diagnostics = {}, None
    if not measured.get("load_selection") and (quality.get("limited_activities") or quality.get("excluded_activities")):
        from .speed_zone_history import prepare_history
        models = dict(speed_by_sport or {})
        missing_sports = {a.get("sport") for a in [*measured.get("activities", []), *calendar]
                          if a.get("quality_status") in {"limited", "excluded"}}
        for sport in sorted(s for s in missing_sports if s and s != "Активност"):
            if sport not in models:
                models[sport] = model_service.speed_view(repository, alias, sport)
        models = {sport: model for sport, model in models.items() if model and model.get("sport") == sport
                  and (model.get("source_generation_id"), model.get("source_revision")) ==
                      (envelope.get("generation_id"), envelope.get("revision"))}
        estimates, speed_diagnostics = prepare_history(repository, alias, measured, calendar, models, settings, today)
    source, evidence = planning_history_estimate.prepare(measured, calendar, profile, today, speed_estimates=estimates,
        capacity_contexts_by_sport=contexts)
    source = {**source, "component_load_model": {**(source.get("component_load_model") or {}),
        "version": COMPONENT_LOAD_VERSION, "scope": "ACTIVITY", "contexts_by_sport": contexts}}
    if speed_diagnostics is not None:
        evidence["speed_reconstruction"] = speed_diagnostics
    return PlanningSourceResult(source, evidence, projected_measured)


def _phase(periodization, day):
    key = day.isoformat()
    phase = next((p for p in periodization["phases"] if p["start_date"] <= key <= p["end_date"]), None)
    taper = any(p["start_date"] <= key <= p["end_date"] for p in periodization.get("taper_windows", []))
    return (phase["kind"] if phase else None), taper


def _taper_factor(periodization, day):
    return min([p.get("volume_factor", .7) for p in periodization.get("taper_windows", [])
                if p["start_date"] <= day.isoformat() <= p["end_date"]] or [1.])


def _with_forecast_day(rows, day, effective):
    # Existing actual-session days are excluded before this function. On a
    # covered zero-load day, replace the zero with the single proposed dose.
    key = day.isoformat()
    return [r for r in rows if r["date"] != key] + [
        {"date": key, "zone": z, "effective_load": float(effective.get(z, 0.))} for z in COMPONENTS]


def _add_forecast_session(rows, day, effective):
    key = day.isoformat()
    current = {r["zone"]: r["effective_load"] for r in rows if r["date"] == key}
    return _with_forecast_day(rows, day, {z: current.get(z, 0.) + effective.get(z, 0.) for z in COMPONENTS})


def _candidate_load(blocks, settings, rows, day, *, technical_reference=None, zone_tmax_minutes=None):
    direct, effective, technical = {z: 0. for z in COMPONENTS}, {z: 0. for z in COMPONENTS}, {}
    for slot in sorted({b.get("session_index", 1) for b in blocks}):
        d, e, technical = _canonical_load([b for b in blocks if b.get("session_index", 1) == slot], settings, rows, day, technical_reference=technical_reference, zone_tmax_minutes=zone_tmax_minutes)
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
        direct, effective, _ = _canonical_load(blocks, settings, rows, day, zone_tmax_minutes=session["dose_evidence"].get("zone_tmax_minutes"))
        evidence = deepcopy(session["dose_evidence"])
        shared_fraction = evidence.get("applied_structure_fraction")
        part_requested = evidence["requested_primary_work_minutes"]/2 if "requested_primary_work_minutes" in evidence else evidence["requested_work_minutes"]/2
        metadata = (session.get("paired_session") or {}) if slot == 2 else {"method_id": session["method_id"].split("-DOUBLE-")[0]}
        if slot == 2 and metadata:
            primary_capacity = evidence["capacity_minutes"]
            paired = evidence["paired_capacity"]
            part_requested *= paired["capacity_minutes"] / primary_capacity
            evidence = {**evidence, **evidence["paired_capacity"]}
        work = _round(sum(b["duration_min"] for b in blocks if b["kind"] == "WORK"))
        evidence.update(shared_day_work_minutes=session["main_work_minutes"], shared_day_dose=False,
                        base_fraction=evidence.get("base_fraction", 1.)/2,
                        prescribed_work_minutes=work, requested_work_minutes=_round(part_requested),
                        requested_primary_work_minutes=_round(part_requested),
                        fraction=part_requested/evidence["capacity_minutes"], applied_fraction=work/evidence["capacity_minutes"],
                        shared_day_structure_fraction=shared_fraction,
                        applied_structure_fraction=_round(_dose_usage(blocks, evidence, metadata.get("zone", session["zone"]))),
                        applied_minimum_capacity_fraction=_round(_minimum_dose_usage(blocks, evidence, metadata.get("zone", session["zone"]))))
        parts.append({**session, **metadata, "double_threshold_pair_id": sha256(f"{day}:{session['sport']}:{session['method_id']}".encode()).hexdigest()[:20], "double_threshold_part": slot, "slot": slot_index + slot, "title": f"Прагова сесия {slot}: {'дълги 6–10 мин' if slot == 1 else 'кратки 60 сек'} · {metadata.get('zone', session['zone'])}", "blocks": blocks,
                      "main_work_minutes": work, "total_minutes": _round(sum(b["duration_min"] for b in blocks)), "dose_evidence": evidence,
                      "canonical_effective_load": {z: _round(v) for z, v in effective.items()},
                      "direct_equivalent_minutes": {z: _round(v) for z, v in direct.items()}})
    return parts


def _capacity_context(speed, settings):
    from biathlon.sport_heart_rate import local_settings, reference_offset
    settings=local_settings(settings,(speed or {}).get("sport"))
    if not speed or speed.get("status") not in {"CALIBRATED", "PRELIMINARY", "INITIAL_HEURISTIC"}:
        return None, [], ["NO_INDIVIDUAL_SPEED_CURVE"]
    keys = set(speed.get("active_test_keys", []))
    tests = [e["payload"] for e in speed.get("tests", []) if e.get("entry_key") in keys]
    reasons = []
    if any(t.get("test_mode", "STRICT") != "STRICT" or t.get("maximal") is not True
           or t.get("comparable") is False or t.get("generated") or t.get("is_estimated")
           or str(t.get("source", "")).upper() in {"MODEL", "MODEL_GENERATED", "GENERATED", "ESTIMATED", "REFERENCE"}
           or str(t.get("kind", "")).upper() in {"MODEL_POINT", "ESTIMATE"} for t in tests):
        reasons.append("EXPLORATORY_OR_NONMAXIMAL_TESTS")
    if len({float(t["duration_s"]) for t in tests}) < 2:
        reasons.append("INSUFFICIENT_INDEPENDENT_TEST_DURATIONS")
    if not settings.hrmax_bpm:
        reasons.append("HRMAX_REQUIRED_FOR_HR_SPEED_MAPPING")
    if any(r != "INSUFFICIENT_INDEPENDENT_TEST_DURATIONS" for r in reasons):
        return None, tests, reasons
    prior_summary = speed.get("preliminary_capacity") or {}
    prior = preliminary_capacity.curve_from_summary(prior_summary)
    heuristic = speed.get("status") == "INITIAL_HEURISTIC"
    if not tests and prior is None and not heuristic:
        return None, tests, ["NO_INDIVIDUAL_SPEED_CURVE"]
    blend = dosing_curve.from_view(speed)
    if heuristic:
        from biathlon.initial_speed_profile import curve_from_view
        curve = curve_from_view(speed)
        if curve is None:
            return None, [], ["INVALID_INITIAL_HEURISTIC_PROFILE"]
    else:
        curve = blend or speed_duration.calibrated(tests, prior=prior)
    durations = {a["zone"]: a["duration_s"] for a in prior_summary.get("anchors", [])}
    predictor_type = dosing_curve.Predictor if blend else hr_speed.ZonalPredictor
    try:
        predictor = predictor_type(curve, settings.zone_bounds_bpm, settings.hrmax_bpm, speed.get("index_summary", {}), expert_durations=durations or None,
                                   normalization_offset_bpm=reference_offset(speed.get("sport")), sport=speed.get("sport", "Run"),
                                   profile=((speed.get("dosing_model") or {}).get("speed_zones") if blend else speed.get("speed_zones")), tests=tests)
    except ValueError:
        return None, tests, [*reasons, "INVALID_HR_SPEED_ZONE_PROFILE"]
    # Candidate methods reuse this immutable curve/HR context. Keep exact
    # speed-to-HR results on this predictor only, as the recipe resolver does.
    predictor.hr_for_speed = lru_cache(maxsize=2048)(predictor.hr_for_speed)
    capacity_view = dict(speed)
    if blend:
        capacity_view["dosing_model"] = {**(speed.get("dosing_model") or {}), "hr_model": predictor.summary()}
    else:
        capacity_view["hr_model"] = predictor.summary()
    predictor.component_load_context = context_from_speed_view(capacity_view)
    return predictor, tests, reasons


def capacity_for(method, settings, speed, context, today, allow_fallback=True, *, use_model_prior=False):
    """Prescribe a method and retain the exact canonical Q-reference capacity."""
    result = _capacity_for_impl(method, settings, speed, context, today, allow_fallback,
                                use_model_prior=use_model_prior)
    if result is None:
        return None
    if result.get("initial_heuristic") or result.get("capacity_source") == "INITIAL_HEURISTIC":
        result.update(speed_role="RAW_MOVING_SPEED", initial_heuristic=True,
                      calibration_source="INITIAL_HEURISTIC", calibration_eligible=False)
        if result.get("capacity_source") == "INITIAL_HEURISTIC":
            result["capacity_confidence"] = "INITIAL_HEURISTIC_UNVALIDATED"
        if result.get("effort_profile"):
            result["effort_profile"] = {**result["effort_profile"], "speed_basis": "RAW_MOVING_SPEED"}
    from biathlon.sport_heart_rate import local_settings
    local = local_settings(settings, method.get("actual_sport") or (speed or {}).get("sport"))
    basis = (getattr(context[0], "component_load_context", None) if context[0] is not None else None)
    basis = basis or context_from_speed_view(speed)
    predictor = context[0]
    result["dosing_speed_zones"] = ((speed or {}).get("dosing_model") or {}).get("speed_zones") or (speed or {}).get("speed_zones")
    if method.get("method_library"):
        resolved = method["method_library"]["resolution"]
        ranges = {key: deepcopy(resolved.get(key)) for key in
                  ("target_speed_range_kmh", "hr_reference_range_bpm", "hr_reference_basis", "hr_role")}
    else:
        ranges = method_dosing.target_ranges(result.get("target_speed_kmh"), result.get("target_hr_bpm"), method["zone"],
            dosing=method.get("dosing"), speed_zones=result["dosing_speed_zones"], hr_bounds=list(local.zone_bounds_bpm))
    if not ranges.get("hr_reference_range_bpm") and result.get("target_speed_kmh") and predictor:
        ranges.update(method_library._hr_reference({**ranges, "zone":method["zone"], "work_duration_seconds": (result.get("effort_profile") or {}).get("work_seconds", 300)}, predictor))
    result.update(ranges)
    result["effort_anchor"] = method_anchors.describe(method, result)
    if result.get("initial_heuristic"):
        result["effort_anchor"].update(speed_role="RAW_MOVING_SPEED", initial_heuristic=True,
                                       calibration_source="INITIAL_HEURISTIC", calibration_eligible=False)
    return {**result, "zone_tmax_minutes": basis["minutes"], "zone_tmax_sources": basis["sources"],
            "dose_settings": {"zone_bounds_bpm": list(local.zone_bounds_bpm), "hrmax_bpm": local.hrmax_bpm,
                              "sport_hr_offset_bpm": getattr(local, "sport_hr_offset_bpm", 0.)}}


def _capacity_for_impl(method, settings, speed, context, today, allow_fallback=True, *, use_model_prior=False):
    """Select exactly one capacity source, without a second TI/volume factor."""
    from biathlon.sport_heart_rate import local_settings
    settings=local_settings(settings,method.get("actual_sport") or (speed or {}).get("sport"))
    if method.get("combined_methods"):
        parts = [capacity_for({**part, "actual_sport": method.get("actual_sport")}, settings, speed, context, today, allow_fallback, use_model_prior=use_model_prior) for part in method["combined_methods"]]
        if any(part is None for part in parts):
            return None
        return {**parts[0], "method_library": None, "combined_capacities": parts, "combination_shortage": method["combination_shortage"]}
    if method.get("capacity_method"):
        return capacity_for({**method["capacity_method"], "actual_sport": method.get("actual_sport", method["sports"][0]),
                             "position": method["position"]}, settings, speed, context, today, allow_fallback, use_model_prior=use_model_prior)
    zone = method["zone"]
    if method.get("method_library"):
        from .method_capacity import library_curve_capacity
        return library_curve_capacity(method, speed, context, use_model_prior=use_model_prior)
    from .method_capacity import expert_interval_curve_capacity
    interval_profile = method.get("interval_template" if method.get("structure") == "MODEL_INTERVALS" else "interval_profile") or {}
    if (method["structure"] in {"MODEL_INTERVALS", "METABOLIC_INTERVALS"}
            and interval_profile.get("speed_time_duration_ratio") is not None and context[0] is not None):
        return expert_interval_curve_capacity(method, speed, context, use_model_prior=use_model_prior)
    if method["structure"] == "MODEL_INTERVALS":
        if method.get("race_specific") or zone == "Z5":
            from .method_capacity import model_interval_curve_capacity
            base = model_interval_curve_capacity(method, settings, speed, context, use_model_prior=use_model_prior)
        else:
            base = capacity_for({**method, "structure": "CONTINUOUS"}, settings, speed, context, today, allow_fallback, use_model_prior=use_model_prior)
        if base is None:
            return None
        p = {**method["interval_template"], "sport": method.get("actual_sport", method["sports"][0]),
             "continuous_capacity_min": base["capacity_minutes"], "target_speed_kmh": base["target_speed_kmh"],
             "speed_basis": "RAW_MOVING_SPEED" if base.get("initial_heuristic") else "FLAT_EQUIVALENT", "effort": method["instructions"]}
        if p["work_seconds"] >= base["capacity_minutes"]*60:
            return None
        return {**base, "target_hr_bpm": None, "hr_role": "OBSERVATION_ONLY", "effort_profile": p,
                "implementation_profile": method["implementation_profile"],
                "capacity_role": "MODEL_ESTIMATE_FOR_REPEATABLE_EFFORT_NOT_MEASURED_TTE"}
    if method["structure"] == "METABOLIC_INTERVALS":
        p = method["interval_profile"]
        if ((speed or {}).get("speed_role") == "RAW_MOVING_SPEED"
                and p.get("target_speed_kmh") and p.get("speed_basis") == "FLAT_EQUIVALENT"):
            # A raw-history scale cannot invert a terrain-corrected target.
            return None
        capacity = p["continuous_capacity_min"]
        source = "COACH_EFFORT_CAPACITY"
        capacity_model_version = "individual-effort-profile-v2"
        observed = []
        within_window = False
        if (p.get("target_speed_kmh") and p.get("speed_basis") == "FLAT_EQUIVALENT"
                and speed and speed.get("status") in {"CALIBRATED", "PRELIMINARY"}):
            keys = set(speed.get("active_test_keys", []))
            anchors = [e["payload"] for e in speed.get("tests", []) if e.get("entry_key") in keys]
            if all(t.get("maximal") and t.get("test_mode", "STRICT") == "STRICT" for t in anchors):
                try:
                    blend = dosing_curve.from_view(speed)
                    prior = preliminary_capacity.curve_from_summary(speed.get("preliminary_capacity"))
                    # An available individual curve can estimate capacity
                    # outside its measured window; never create a reference
                    # curve from an empty set of tests and no paired prior.
                    curve = blend
                    if curve is None and (anchors or prior):
                        curve = speed_duration.calibrated(anchors, prior=prior)
                    if curve is not None:
                        seconds = curve.inverse(p["target_speed_kmh"] / 3.6)
                        observed = [t["duration_s"] for t in anchors]
                        within_window = bool(observed) and min(observed) <= seconds <= max(observed)
                        from .method_capacity import observed_curve_support
                        if use_model_prior or observed_curve_support(anchors, seconds):
                            capacity, source = seconds / 60, ("BLENDED_DOSING_CURVE" if blend else
                                "SPEED_DURATION" if anchors else "SPEED_DURATION_PRIOR")
                            capacity_model_version = dosing_curve.VERSION if blend else getattr(curve, "model_version", speed["model_version"])
                        else:
                            # A stricter evidence policy may exclude a method,
                            # but cannot replace an available curve with Tmax
                            # from a different source.
                            return None
                except ValueError:
                    return None
        if source == "COACH_EFFORT_CAPACITY" and not 0 <= (today - date.fromisoformat(p["assessed_on"])).days <= 42:
            return None
        if p["work_seconds"] >= capacity * 60:
            return None
        return {"capacity_source": source, "capacity_minutes": capacity,
                "target_hr_bpm": None, "target_speed_kmh": p.get("target_speed_kmh"),
                "model_version": capacity_model_version, "fallback_reasons": [],
                "capacity_confidence": "COACH_30_70_ESTIMATE" if source == "BLENDED_DOSING_CURVE" else "INDIVIDUAL_CURVE_ESTIMATE" if source != "COACH_EFFORT_CAPACITY" else "INDIVIDUAL_COACH_ASSESSMENT",
                "capacity_is_estimate": True,
                "capacity_reference": "CURVE_INVERSE_AT_PRESCRIBED_SPEED" if source != "COACH_EFFORT_CAPACITY" else "INDIVIDUAL_COACH_ASSESSMENT",
                "supported_test_duration_s": [min(observed), max(observed)] if observed else None,
                "within_observed_test_window": within_window,
                "effort_profile": deepcopy(p), "hr_role": "OBSERVATION_ONLY",
                "speed_role": p.get("speed_basis", "ACTUAL"), "target_zone_working_bounds_bpm": None}
    if zone == "STR":
        return {"capacity_source": "STRENGTH_METHOD_PROFILE", "capacity_minutes": method["max_work_min"],
                "target_hr_bpm": None, "target_speed_kmh": None,
                "model_version": strength_methods.VERSION if method.get("strength_profile") else "strength-circuit-v2", "fallback_reasons": [],
                "strength_profile": deepcopy(method.get("strength_profile")),
                "capacity_role": "METHOD_WORK_LIMIT_NOT_METABOLIC_TREF"}
    low, high = _working_band(settings, zone)
    target_hr = float(low + method["position"] * (high - low))
    predictor, tests, initial_reasons = context
    reasons = list(initial_reasons)
    duration = velocity = None
    test_mapping = False
    heuristic = (speed or {}).get("status") == "INITIAL_HEURISTIC"
    if predictor is not None:
        from biathlon.zone_profile import _accepted_tests
        mapping_source = predictor.metadata(target_hr)["hr_prediction_source"]
        test_mapping = (use_model_prior and allow_fallback and bool(tests)
            and len(_accepted_tests(tests, speed.get("sport", "Run"))) == len(tests)
            and mapping_source in {"INITIAL_V50_V6_ESTIMATE", "TEST_CURVE_CONFLICT_FALLBACK"})
        recent = (speed.get("index_window") or {}).get("last_activity_date")
        index_reference_day = today
        freshness = speed.get("index_freshness") or {}
        if freshness.get("version") == speed_anchor_policy.VERSION:
            try:
                selected_on = date.fromisoformat(freshness.get("as_of") or "")
            except ValueError:
                selected_on = None
            if selected_on is not None and selected_on <= today:
                # Future prescriptions share the model selected at generation.
                # A later generation reevaluates recency before selecting it.
                index_reference_day = selected_on
        if not heuristic and not test_mapping and (not recent or (index_reference_day - date.fromisoformat(recent)).days > speed_anchor_policy.MAX_INDEX_AGE_DAYS):
            reasons.append("STALE_HR_SPEED_INDEX")
        if not heuristic and (speed.get("index_summary", {}).get(zone) or {}).get("count", 0) < 3:
            reasons.append("INSUFFICIENT_COMPARABLE_INDEX_OBSERVATIONS")
        try:
            velocity = predictor.speed_for_hr(target_hr)
            duration = predictor.duration(target_hr)
            if predictor.metadata(target_hr)["hr_prediction_source"] not in {"INDEX", "COACH_INDEX_TEST_BLEND"}:
                reasons.append("HR_MAPPING_USES_EXPERT_ANCHOR")
            # The broad reference curve is not an athlete-supported domain.
            if not tests or not min(t["duration_s"] for t in tests) <= duration <= max(t["duration_s"] for t in tests):
                reasons.append("OUTSIDE_OBSERVED_TEST_DURATION_SUPPORT")
        except ValueError:
            reasons.append("BOUNDARY_OUTSIDE_CURVE_DOMAIN" if velocity is not None else "OUTSIDE_HR_SPEED_PREDICTION_RANGE")
    prior_reasons = {"INSUFFICIENT_INDEPENDENT_TEST_DURATIONS", "OUTSIDE_OBSERVED_TEST_DURATION_SUPPORT"}
    mapping_priors = {"HR_MAPPING_USES_EXPERT_ANCHOR", "INSUFFICIENT_COMPARABLE_INDEX_OBSERVATIONS"}
    prior_allowed = use_model_prior and allow_fallback and reasons and set(reasons) <= prior_reasons | mapping_priors
    # The separate 30/70 curve is an explicit coaching estimate, including its
    # extrapolated part. Existing fallback settings still control whether such
    # estimates may prescribe a dose; stale/invalid data remain disqualifying.
    from .method_capacity import observed_curve_support
    blend_allowed = (isinstance(predictor, dosing_curve.Predictor)
        and (use_model_prior or observed_curve_support(tests, duration))
        and (not reasons or allow_fallback
             and set(reasons) <= prior_reasons | mapping_priors))
    if (not reasons or prior_allowed or blend_allowed) and duration is not None:
        source = ("INITIAL_HEURISTIC" if heuristic else "BLENDED_DOSING_CURVE" if blend_allowed
                  else "TEST_CURVE_INITIAL_HR_ESTIMATE" if test_mapping else "SPEED_DURATION_PRIOR" if prior_allowed else "SPEED_DURATION")
        minutes = duration / 60.
        model_version = dosing_curve.VERSION if blend_allowed else speed["model_version"]
    else:
        outside_curve = (predictor is not None and velocity is not None and use_model_prior
                         and set(reasons) <= prior_reasons | mapping_priors | {"BOUNDARY_OUTSIDE_CURVE_DOMAIN"})
        if not allow_fallback or ((predictor is not None or (speed or {}).get("status") in {"CALIBRATED", "PRELIMINARY", "INITIAL_HEURISTIC"}) and not outside_curve):
            return None
        # Expert continuous capacity at the upper zone edge; explicitly NOT
        # the canonical load history's bounded 7*E40 variable also named Tref.
        prior_anchor = next((a for a in (speed or {}).get("preliminary_capacity", {}).get("anchors", [])
                             if a.get("zone") == zone and a.get("kind") == "ESTIMATE"), None)
        upper_capacity = (prior_anchor["duration_s"]/60 if prior_anchor else sum(hr_speed.TMAX_RANGES_S[zone]) / 120.)
        from biathlon.speed_equivalence import relative_coefficient
        coefficient = relative_coefficient(target_hr, low, high, zone) or .1
        minutes = upper_capacity / max(.1, coefficient)
        source = "EXPERT_CONTINUOUS_TREF"
        if not outside_curve:
            velocity = None
        model_version = "expert-continuous-capacity-v1"
    return {"capacity_source": source, "capacity_minutes": _round(minutes),
            "capacity_reference": "EXPERT_CONTINUOUS_TMAX" if source == "EXPERT_CONTINUOUS_TREF" else "CURVE_AT_PRESCRIBED_EFFORT",
            "expert_capacity_position": prior_anchor.get("duration_position") if source == "EXPERT_CONTINUOUS_TREF" and prior_anchor else None,
            "target_hr_bpm": _round(target_hr), "target_speed_kmh": _round(velocity) if velocity else None,
            "model_version": model_version, "fallback_reasons": reasons,
            "capacity_confidence": "COACH_30_70_ESTIMATE" if source == "BLENDED_DOSING_CURVE" else "TEST_CURVE_WITH_ESTIMATED_HR_MAPPING" if source == "TEST_CURVE_INITIAL_HR_ESTIMATE" else "INDIVIDUAL_TESTS_AND_RECENT_INDEX" if source == "SPEED_DURATION" else "INDIVIDUALLY_SCALED_EXPERT_SHAPE" if source == "SPEED_DURATION_PRIOR" else "EXPERT_REFERENCE",
            "target_zone_working_bounds_bpm": [low, high],
            "supported_test_duration_s": [min(t["duration_s"] for t in tests), max(t["duration_s"] for t in tests)] if tests else None,
            "speed_role": "RAW_MOVING_SPEED" if heuristic and velocity is not None else "FLAT_EQUIVALENT_REFERENCE_NOT_TERRAIN_PACE",
            # A bounded expert duration can accompany an initial raw-speed
            # target. Keep each origin explicit; the duration fallback cannot
            # change the coordinate frame or make the speed measured evidence.
            **({"initial_heuristic": True, "calibration_source": "INITIAL_HEURISTIC",
                "calibration_eligible": False, "target_speed_source": "INITIAL_HEURISTIC",
                "target_speed_model_version": speed["model_version"]}
               if heuristic and velocity is not None else {})}


def _block(kind, label, zone, minutes, hr, instructions, *, speed=None, repetition=None):
    seconds = round(float(minutes) * 60., 3)
    return {"kind": kind, "label": label, "zone": zone, "duration_min": seconds / 60., "duration_s": seconds,
            "target_hr_bpm": _round(hr) if hr is not None else None,
            "target_speed_kmh": _round(speed) if speed is not None else None,
            "repetition": repetition, "instructions": instructions}


def _blocks(method, work, evidence, settings):
    blocks = _blocks_impl(method, work, evidence, settings)
    from biathlon.sport_heart_rate import local_settings
    local = local_settings(settings, method.get("actual_sport"))
    for block in blocks:
        block.setdefault("load_sport", method.get("actual_sport") or (method.get("sports") or ["Run"])[0])
        if not method.get("combined_methods") and evidence.get("initial_heuristic") and block.get("target_speed_kmh") is not None:
            block.update(speed_basis="RAW_MOVING_SPEED", speed_role="RAW_MOVING_SPEED",
                         initial_heuristic=True, calibration_source="INITIAL_HEURISTIC", calibration_eligible=False)
        if block["zone"] == "STR" or block.get("passive_rest") or block.get("method_library") or block.get("primary_control") == "HRMAX":
            continue
        same = block.get("target_speed_kmh") is not None and evidence.get("target_speed_kmh") is not None and abs(block["target_speed_kmh"]-evidence["target_speed_kmh"]) < .002
        block.update(method_dosing.target_ranges(block.get("target_speed_kmh"), block.get("target_hr_bpm"), block["zone"],
            dosing=method.get("dosing") or evidence.get("dosing"),
            speed_range=block.get("target_speed_range_kmh") or (evidence.get("target_speed_range_kmh") if same else None),
            hr_range=block.get("hr_reference_range_bpm") or (evidence.get("hr_reference_range_bpm") if same else None),
            speed_zones=evidence.get("dosing_speed_zones"), hr_bounds=list(local.zone_bounds_bpm)))
    return blocks


def _blocks_impl(method, work, evidence, settings):
    from biathlon.sport_heart_rate import local_settings, reference_offset
    settings=local_settings(settings,method.get("actual_sport"))
    if method.get("combined_methods"):
        budgets = evidence.get("combined_budgets")
        if not budgets:
            return []
        primary_budget = budgets[0]["requested_work_minutes"]
        scale = work / primary_budget if primary_budget else 0.
        low, high = _working_band(settings, "Z1")
        easy = low + .35*(high-low)
        result = ([_block("WARMUP", "Обща загрявка", "Z1", method["warmup_min"], easy,
                          "Подготви се за двата метода с плавно леко движение.")] if method["warmup_min"] else [])
        for index, (part, cap, budget) in enumerate(zip(method["combined_methods"], evidence["combined_capacities"], budgets)):
            ceiling = min(part["max_work_min"], budget["whole_role_work_minutes"],
                          budget.get("objective_limit_work_minutes", float("inf")))
            # Rounding the first part upward must not enlarge the second
            # component's independent target and force the whole pair down.
            requested = budget["requested_work_minutes"]*(min(1., scale) if index else scale)
            amount = _whole_work_at_most(part, requested, ceiling)
            if amount < part["min_work_min"] - 1e-9:
                return []
            blocks = _blocks({**part, "actual_sport": method.get("actual_sport", part["sports"][0])}, amount, cap, settings)
            if not blocks:
                return []
            if index:
                result.append(_block("RECOVERY", "Преход между двата метода", "Z1", 3., low+.35*(high-low), "Три минути спокойно движение; започни втората част със запазена техника."))
            # Each part owns one independent card, shared by its repetitions.
            # Copying its entire source/resolution for every block dominates
            # candidate trials without changing their load or instructions.
            card = deepcopy(part.get("method_library") or part.get("method_card"))
            result.extend({**b, "combination_part": index+1, "parent_method_id": part["id"], "parent_method_card": card} for b in blocks if b["kind"] not in {"WARMUP", "COOLDOWN"})
        if method["cooldown_min"]:
            result.append(_block("COOLDOWN", "Общо разпускане", "Z1", method["cooldown_min"], easy,
                                 "Плавно намали усилието след двете основни части."))
        return result
    if method.get("double_threshold"):
        single = {**method, "double_threshold": False, "min_work_min": method["min_work_min"] / 2}
        half = _blocks(single, work / 2, evidence, settings)
        if not half:
            return []
        if method.get("paired_method"):
            other = method["paired_method"]
            paired = evidence["paired_capacity"]
            primary_capacity = evidence["capacity_minutes"]
            paired_capacity = paired["capacity_minutes"]
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
        prep = method.get("source_preparation") or {}
        warmup_fraction = prep.get("warmup_hrmax_fraction")
        warmup_hr = sum(warmup_fraction) / 2 * settings.hrmax_bpm if warmup_fraction else easy
        warmup_zone = (next((f"Z{i+1}" for i in range(5) if settings.zone_bounds_bpm[i] <= warmup_hr < settings.zone_bounds_bpm[i+1]), "Z5")
                       if warmup_fraction else "Z1")
        blocks.append(_block("WARMUP", "Загрявка", warmup_zone, method["warmup_min"], warmup_hr,
            "Леко движение; през последните 1–2 мин плавно повиши ритъма в Z1." if method.get("mixed_component") else "Плавно леко движение в Z1."))
        if warmup_fraction:
            blocks[-1].update(primary_control="HRMAX", hr_reference_range_bpm=[v*settings.hrmax_bpm for v in warmup_fraction],
                              hr_reference_basis="PUBLISHED_PREPARATION_PERCENT_HRMAX", hr_role="PRIMARY_SOURCE_PRESCRIPTION",
                              instructions="Загрявка при описаната в източника интензивност.")
        if method.get("strength_profile"):
            blocks[-1]["instructions"] = "Леко общо разгряване и подготвителни серии с постепенно увеличаване на съпротивлението. Подготвителните серии не са работни и не влизат в силовата доза."
    if method.get("mixed_component"):
        blocks.append(_block("PREPARATION", "Подготвителни упражнения", "Z1", 3., easy, "Познати мобилизационни и координационни упражнения; запази леко усилие."))
    structure = method["structure"]
    if structure in {"THRESHOLD_LONG", "THRESHOLD_SHORT", "MIXED_AEROBIC"}:
        if structure == "THRESHOLD_LONG":
            reps = max(2, math.ceil(work/10))
            duration = work/reps
            if duration < 6:
                return []
            rest = 1.
        elif structure == "THRESHOLD_SHORT":
            reps, duration, rest = int(work+1e-6), 1., .5
            if reps < 6:
                return []
        elif method["mixed_variant"] == "STEADY":
            reps, duration, rest = 1, work, 0.
        else:
            reps = max(2, math.ceil(work/3))
            duration, rest = work/reps, 1.
        for rep in range(reps):
            hr = None if structure == "THRESHOLD_SHORT" else evidence["target_hr_bpm"]
            b = _block("WORK", f"{'Кратка' if duration <= 1 else 'Работна'} отсечка {rep+1}/{reps}", method["zone"], duration,
                       hr, method["instructions"], speed=evidence.get("target_speed_kmh"), repetition=rep+1)
            if hr is None:
                b.update(primary_control="EFFORT_AND_QUALITY", load_estimate="ZONE_REFERENCE_NOT_MEASURED_HR",
                         load_reference_hr_bpm=evidence.get("target_hr_bpm"))
            blocks.append(b)
            if rep < reps-1:
                blocks.append(_block("RECOVERY", "Активна почивка", "Z1", rest, easy, "Използвай цялата почивка с леко движение."))
    elif structure == "STRENGTH_SETS":
        main = strength_methods.work_blocks(method, work, evidence, _block)
        if not main:
            return []
        blocks.extend(main)
    elif structure in {"STRENGTH_CIRCUIT", "AEROBIC_STRENGTH"}:
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
    elif structure in {"LIBRARY_INTERVALS", "LIBRARY_CONTINUOUS"}:
        library = evidence["method_library"]
        r = library["resolution"]
        if r["structure"] in {"SEQUENCE", "SCALED_SEQUENCE", "PROGRESSIVE_CONTINUOUS"}:
            r = deepcopy(r)
            selected_work = _quantized_work(method, min(work, r["maximum_work_seconds"]/60)) * 60
            if selected_work + 1e-6 < r["minimum_work_seconds"]:
                return []
            if r["structure"] == "SEQUENCE":
                tail = r["tail_segment"]
                n = round((selected_work-r["cycle_work_seconds"]) / tail["work_duration_seconds"])
                r["segments"].extend(deepcopy(tail) for _ in range(n))
            else:
                scale = selected_work / r["cycle_work_seconds"]
                for segment in r["segments"]:
                    # Preserve every phase and its speed; shorten work together.
                    # Recovery duration stays prescribed, not Recovery-scaled.
                    if segment["kind"] == "WORK":
                        for key in ("work_duration_seconds", "work_distance_m"):
                            segment[key] *= scale
                        segment["work_duration_range_seconds"] = [segment["work_duration_seconds"]]*2
                        if segment.get("work_unit") == "meters":
                            segment["work_distance_range_m"] = [segment["work_distance_m"]]*2
            r.update(cycle_work_seconds=selected_work, repetitions=[1, 1])
        if structure == "LIBRARY_CONTINUOUS":
            if work * 60 + 1e-6 < r["minimum_work_seconds"]:
                return []
            segment = r["segments"][0]
            b = _block("WORK", "Основна работа", segment["zone"], min(work, r["maximum_work_seconds"]/60),
                       segment.get("target_hr_bpm"), method["instructions"], speed=segment["target_speed_kmh"])
            b.update(primary_control=segment.get("execution_control", "SPEED_AND_QUALITY"), method_library=deepcopy(library),
                     hr_reference_range_bpm=segment.get("hr_reference_range_bpm"), hr_reference_basis=segment.get("hr_reference_basis"), hr_role=segment.get("hr_role"),
                     target_speed_range_kmh=segment["target_speed_range_kmh"],
                     duration_range_seconds=segment["work_duration_range_seconds"])
            blocks.append(b)
        else:
            selected_work = _quantized_work(method, work)
            reps = min(r["repetitions"][1], int((selected_work*60+1e-6)//(r["cycle_work_seconds"]*r["sets"])))
            if reps < r["repetitions"][0]:
                return []
            for series in range(r["sets"]):
                for rep in range(reps):
                    for i, segment in enumerate(r["segments"]):
                        if segment["kind"] == "RECOVERY" and i == len(r["segments"])-1 and rep == reps-1 and series == r["sets"]-1:
                            continue
                        b = _block(segment["kind"], f"Серия {series+1} · отсечка {rep+1}/{reps} · част {i+1}",
                                   segment["zone"], segment["work_duration_seconds"]/60, segment.get("target_hr_bpm"),
                                   method["instructions"], speed=segment["target_speed_kmh"], repetition=rep+1)
                        # Keep the resolver's duration so millisecond display
                        # rounding cannot make a complete recipe exceed itself.
                        b.update(duration_s=segment["work_duration_seconds"], duration_min=segment["work_duration_seconds"]/60)
                        b.update(primary_control=segment.get("execution_control", "SPEED_AND_QUALITY"), speed_basis="FLAT_EQUIVALENT",
                                 load_estimate="ZONE_UPPER_REFERENCE_NOT_MEASURED_HR",
                                 method_library={"id": library["id"], "resolution": deepcopy(segment)},
                                 target_speed_range_kmh=segment["target_speed_range_kmh"],
                                 duration_range_seconds=segment["work_duration_range_seconds"],
                                 hr_reference_range_bpm=segment.get("hr_reference_range_bpm"), hr_reference_basis=segment.get("hr_reference_basis"), hr_role=segment.get("hr_role"),
                                 target_distance_m=segment["work_distance_m"], target_distance_range_m=segment.get("work_distance_range_m"), series=series+1)
                        if segment.get("target_hr_bpm") is not None:
                            b.update(load_estimate="PRESCRIBED_HR_NOT_MEASURED_HR", primary_control="HRMAX")
                        blocks.append(b)
                        if i < len(r["segments"])-1 and segment["recovery_after_seconds"] > 0:
                            rest_type = segment["recovery_after_type"]
                            passive = rest_type == "PASSIVE"
                            label = {"PASSIVE":"Пасивна почивка", "WALK":"Ходене между частите", "WALK_OR_JOG":"Ходене или леко бягане"}.get(rest_type, "Почивка между частите")
                            recovery_block = _block("RECOVERY", label, "Z1", segment["recovery_after_seconds"]/60,
                                                 None if passive else easy, segment.get("recovery_after_instructions") or (library.get("guidance") or {}).get("recovery") or "Следвай предписаната почивка.")
                            recovery_block.update(recovery_type=rest_type, passive_rest=passive)
                            blocks.append(recovery_block)
                    rest = r["recovery_seconds"] if rep < reps-1 else r["set_recovery_seconds"] if series < r["sets"]-1 else 0
                    if rest > 0:
                        between_sets = rep == reps-1 and series < r["sets"]-1
                        rest_type = r["set_recovery_type"] if between_sets else r["recovery_type"]
                        passive = rest_type == "PASSIVE"
                        recovery_resolution = None if between_sets else r.get("recovery_resolution")
                        b = _block("RECOVERY", "Пасивна почивка" if passive else "Почивка между повторенията",
                                   recovery_resolution["zone"] if recovery_resolution else "Z1", rest/60,
                                   None if passive else recovery_resolution.get("target_hr_bpm") if recovery_resolution else easy,
                                   (library.get("guidance") or {}).get("recovery") or "Следвай предписаната почивка.",
                                   speed=recovery_resolution["target_speed_kmh"] if recovery_resolution else None)
                        if recovery_resolution and recovery_resolution.get("primary_control") == "HRMAX":
                            b.update(primary_control="HRMAX", hr_reference_range_bpm=recovery_resolution["hr_reference_range_bpm"],
                                     hr_reference_basis=recovery_resolution["hr_reference_basis"], hr_role=recovery_resolution["hr_role"])
                        rest_mode = rest_type if between_sets else r.get("recovery_mode_hint") or rest_type
                        if rest_mode in {"WALK", "WALK_OR_JOG"}:
                            b["label"] = "Ходене" if rest_mode == "WALK" else "Ходене или леко бягане"
                        b.update(recovery_type=rest_type, recovery_mode_hint=rest_mode, passive_rest=passive)
                        blocks.append(b)
    elif structure in {"METABOLIC_INTERVALS", "MODEL_INTERVALS", "THRESHOLD_HIGH"}:
        p = ((evidence.get("secondary_capacity") or {}).get("effort_profile") or method["interval_profile"]
             if structure == "THRESHOLD_HIGH" else evidence.get("effort_profile") or method.get("interval_profile"))
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
            if method.get("race_specific"):
                b.update(primary_control="RACE_SPEED_AND_QUALITY", race_specific=deepcopy(method["race_specific"]))
                if method["interval_template"].get("work_seconds_range"):
                    b["duration_range_seconds"] = list(method["interval_template"]["work_seconds_range"])
                if method["race_specific"].get("speed_range_kmh"):
                    b["target_speed_range_kmh"] = list(method["race_specific"]["speed_range_kmh"])
            blocks.append(b)
            if rep < reps - 1:
                blocks.append(_block("RECOVERY", "Леко движение между отсечките", "Z1", p["recovery_seconds"] / 60,
                                     easy, "Пълна активна почивка между отсечките; без допълнителна след последната."))
    elif structure == "AEROBIC_SUPPORT":
        secondary = evidence["secondary_capacity"]
        blocks.append(_block("WORK", "Лека аеробна част", "Z1", 2*work,
                             secondary["target_hr_bpm"], method["instructions"], speed=secondary["target_speed_kmh"]))
        blocks.append(_block("WORK", "Поддържаща част в Z3", "Z3", work,
                             evidence["target_hr_bpm"], method["instructions"], speed=evidence["target_speed_kmh"]))
    elif structure == "THRESHOLD_REPETITIONS":
        reps = max(2, math.ceil(work/12))
        duration = math.floor(work/reps*2)/2
        if duration < 6:
            return []
        for rep in range(reps):
            blocks.append(_block("WORK", f"Работна част {rep+1}/{reps}", "Z3", duration,
                                 evidence["target_hr_bpm"], method["instructions"], speed=evidence["target_speed_kmh"], repetition=rep+1))
            if rep < reps-1:
                blocks.append(_block("RECOVERY", "Активна почивка", "Z1", 2, easy, "Две минути леко движение."))
    elif structure == "CRUISE_ALTERNATING":
        cycles = int(work//15)
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
    if method.get("mixed_component"):
        primary_work = sum(b["duration_min"] for b in blocks if b["kind"] == "WORK")
        secondary = evidence["secondary_capacity"]
        easy_work = primary_work*evidence["easy_to_primary_ratio"]
        if evidence.get("easy_work_cap_minutes") is not None:
            easy_work = min(easy_work, max(0., evidence["easy_work_cap_minutes"]))
        if easy_work > 0:
            blocks.append(_block("WORK", "Довършване на леката аеробна работа", "Z1", easy_work,
                                 secondary["target_hr_bpm"], "Продължи спокойно в Z1 според планираното време. Не наваксвай пропуснати отсечки.", speed=secondary.get("target_speed_kmh")))
    if method["cooldown_min"]:
        blocks.append(_block("COOLDOWN", "Разпускане", "Z1", method["cooldown_min"], easy, "Постепенно намали усилието."))
    if method.get("neuromuscular_profile"):
        preparation = _block("PREPARATION", "Подготвителни упражнения", "Z1", 3., easy,
                             "Мобилизация и няколко познати координационни упражнения. Подготви техниката за ускоренията.")
        blocks[1:1] = [preparation, *training_guidance.neuromuscular_blocks(method["neuromuscular_profile"], easy)]
    offset=reference_offset(method.get("actual_sport"))
    if offset:
        for block in blocks:
            block["sport_hr_offset_bpm"]=offset
    return blocks


def _direct_q(blocks, settings, *, details=False):
    """One Q calculation for candidate checks and the frozen explanation.

    Ledger rows retain unrounded arithmetic. Presentation rounding happens
    after all blocks are summed, as it does for the selected session totals.
    """
    direct = {z: 0. for z in COMPONENTS}
    ledger = []
    for block in blocks:
        z = block["zone"]
        if z == "STR":
            if block.get("kind") not in {"WARMUP", "COOLDOWN", "PREPARATION"}:
                direct[z] += block["duration_min"]
                if details:
                    ledger.append({"kind": block["kind"], "zone": z, "duration_min": block["duration_min"],
                                   "coefficient": 1., "equivalent_min": block["duration_min"], "source": "STRENGTH_EXPOSURE"})
            continue
        if block.get("passive_rest"):
            continue
        if z == "NMS":
            # Unknown metabolic/mechanical contribution is disclosed separately.
            # Never translate sprint seconds into Z5, STR, or zero measured load.
            continue
        idx = int(z[1:]) - 1
        low, high = settings.zone_bounds_bpm[idx:idx + 2]
        offset=max(0.,block.get("sport_hr_offset_bpm",0.)-getattr(settings,"sport_hr_offset_bpm",0.))
        low,high=low-offset,high-offset
        if z == "Z5":
            high = settings.hrmax_bpm-offset if settings.hrmax_bpm else high
        # Effort-led intervals carry a planning estimate, never a fabricated
        # measured HR or an instruction to chase the HR target.
        load_hr = block["target_hr_bpm"] if block["target_hr_bpm"] is not None else (block.get("load_reference_hr_bpm") or high)
        from biathlon import speed_equivalence
        contexts = getattr(settings, "load_contexts_by_sport", {})
        sport = block.get("load_sport") or getattr(settings, "load_default_sport", "Run")
        zone_parameters = (contexts.get(sport) or {}).get("comparison_parameters")
        speed = block.get("target_speed_kmh")
        use_speed = (speed is not None and zone_parameters
                     and (getattr(settings, "leading_indicator", "HEART_RATE") == "SPEED"
                          or block.get("target_hr_bpm") is None))
        speed_estimate = speed_equivalence.evaluate(zone_parameters, speed, zone=z) if use_speed else None
        estimate = speed_estimate
        if estimate is None or estimate.get("status") == "UNAVAILABLE":
            local_offset = getattr(settings, "sport_hr_offset_bpm", 0.)
            common_bounds = tuple(v+local_offset for v in settings.zone_bounds_bpm)
            total_offset = block.get("sport_hr_offset_bpm", local_offset)
            estimate = speed_equivalence.evaluate_hr(zone_parameters, load_hr, bounds=common_bounds, offset_bpm=total_offset, zone=z)
        if estimate.get("status") == "AVAILABLE":
            amount = block["duration_min"] * estimate["coefficient"]
            direct[estimate["zone"]] += amount
            if details:
                ledger.append({"kind": block["kind"], "zone": estimate["zone"], "duration_min": block["duration_min"],
                               "coefficient": estimate["coefficient"], "equivalent_min": amount,
                               "source": "SPEED" if use_speed and estimate is speed_estimate else "HEART_RATE"})
    return direct, ledger


def _canonical_load(blocks, settings, rows, day, *, technical_reference=None, zone_tmax_minutes=None):
    parameters = DEFAULT_PARAMETERS  # Read-only constants; no candidate mutates them.
    direct, _ = _direct_q(blocks, settings)
    lower = (day - timedelta(days=40)).isoformat()
    upper = day.isoformat()
    technical = technical_reference if technical_reference is not None else {z: _causal_tref(z, pd.Series([r["effective_load"] for r in rows
                    if r["zone"] == z and lower <= r["date"] < upper], dtype=float), parameters["base_loads"])
                 for z in COMPONENTS}
    vector = effective_from_direct_vector(direct, technical, parameters, zone_tmax_minutes=zone_tmax_minutes)
    return direct, dict(zip(COMPONENTS, map(float, vector))), technical


def _q_accounting(blocks, settings):
    """Disclose any unmapped prescription time instead of calling it zero Q."""
    _, ledger = _direct_q(blocks, settings, details=True)
    expected = sum(b["duration_min"] for b in blocks if
        b["zone"] == "STR" and b["kind"] not in {"WARMUP", "COOLDOWN", "PREPARATION"}
        or b["zone"] in COMPONENTS[:5] and not b.get("passive_rest"))
    missing = max(0., expected-sum(row["duration_min"] for row in ledger))
    return {"basis": "PLANNED_ESTIMATE_NOT_MEASURED_LOAD", "rows": ledger,
            "status": "INCOMPLETE" if missing > 1e-7 else "COMPLETE",
            "unavailable_minutes": missing,
            "unknown_metabolic_minutes": sum(b["duration_min"] for b in blocks if b["zone"] == "NMS")}


def _budgets(rows, day, taper=False, mesocycle_factor=1., *, actual_rows=None, targets=None):
    end = (day - timedelta(days=1)).isoformat()
    first40 = (day - timedelta(days=40)).isoformat()
    first7 = (day - timedelta(days=7)).isoformat()
    first50 = (day - timedelta(days=50)).isoformat()
    prospective_first = (day - timedelta(days=6)).isoformat()
    current_day = day.isoformat()
    parameters = DEFAULT_PARAMETERS
    result = {}
    for z in COMPONENTS:
        previous = [r for r in (actual_rows if actual_rows is not None else rows) if r["zone"] == z and first40 <= r["date"] <= end]
        recent = [r for r in previous if r["date"] >= first7]
        mean40 = sum(r["effective_load"] for r in previous) / len(previous) if previous else 0.
        mean7 = sum(r["effective_load"] for r in recent) / len(recent) if recent else 0.
        previous50 = [r for r in rows if r["zone"] == z and
                      first50 <= r["date"] <= end]
        mean50 = sum(r["effective_load"] for r in previous50) / len(previous50) if previous50 else 0.
        base = max(parameters["base_loads"][z], .5 * mean50)
        target = targets[z]["target"] if targets is not None else 7 * mean40 * mesocycle_factor * (.7 if taper else 1.)
        # Candidate day's prospective seven-day window: the oldest day from
        # the completed 7-day window has rolled out before adding today's dose.
        actual = sum(r["effective_load"] for r in rows if r["zone"] == z and
                     prospective_first <= r["date"] <= current_day)
        result[z] = {"e7_daily": _round(mean7), "e40_daily": _round(mean40),
                     "index_7_40": _round((base + mean7) / (base + mean40)),
                     "target_weekly_effective": _round(target), "rolling_7d_effective": _round(actual),
                     "deficit_effective": _round(max(0., target - actual)),
                     "target_basis": targets[z]["basis"] if targets is not None else "SEVENTY_PERCENT_C40_TAPER_CEILING" if taper else "C40_MAINTENANCE_REFERENCE",
                     "goal_evidence": targets[z] if targets is not None else None,
                     "prospective_window_start": (day - timedelta(days=6)).isoformat(),
                     "prospective_window_end": day.isoformat(),
                     "is_required_catchup": False}
    return result


def _prescription_budgets(rows, day, values, segment, reserve=None):
    """One Q allowance per component, with an independent actual 7/40 gate."""
    components = segment["components"]
    bounds = {z: planning_allocation.prescription_headroom(row) or 0.
              for z, row in components.items()}
    possible = {z: planning_allocation.prescription_headroom(row,
        maintenance_reserve=max((reserve or {}).get("q", {}).get(z, 0.),
            (reserve or {}).get("quality_preparation_q", {}).get(z, 0.))) or 0.
        for z, row in components.items()}
    envelope = planning_envelope.headroom(rows, day, effective_upper_bound(possible))
    q = {}
    for z, objective in components.items():
        if objective["basis"] == "DIRECT_Q":
            values[z].update(envelope[z])
            q[z] = bounds[z]
        else:
            values[z]["deficit_effective"] = _round(min(values[z]["deficit_effective"],
                                                       objective["remaining_effective"]))
            values[z]["effective_limit_basis"] = "LEGACY_E_TARGET"
        values[z].update(rolling_deficit_effective=values[z]["deficit_effective"],
            prescription_basis="LONG_TERM_MICROCYCLE_Q" if objective["basis"] == "DIRECT_Q" else "LONG_TERM_MICROCYCLE_REMAINDER",
            long_term_window_start=segment["window_start"], long_term_window_end=segment["window_end"],
            target_period_effective=_round(objective["target_effective"]),
            target_period_q=_round(objective["target"]) if objective["basis"] == "DIRECT_Q" else None,
            remaining_target_q=objective["remaining"] if objective["basis"] == "DIRECT_Q" else None,
            desired_index_7_40=(values[z].get("goal_evidence") or {}).get("target_index"))
    return values, q, segment


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
    state = planning_controls.resolve(profile, day, period, automatic, periodization=periodization, taper=taper)
    return_points = [training_return.point(progression,z,day) for z in COMPONENTS[:5]]
    if state and not state["explicit"] and any(p and p["returning"] for p in return_points) and training_return.supported(progression):
        state.update(kind="RE_ENTRY", name="Линейно връщане след пауза", wave_factor=1., volume_factor=1.,
                     automatic_shock=False, training_return=True)
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
    goals = planning_consistency.reconcile(goals, profile, state, actual_reference, limited=limited, taper=taper)
    return goals, focus, state


def progression_context(repository, alias, profile, source, rows, today, periodization=None, *, envelope=None, measured_source=None):
    config = load_progression.settings(profile)
    if not config or not profile.get("planning_controls"):
        return None
    entries = ResponseStore(repository).entries(alias) if config["feedback_enabled"] else []
    control = (profile.get("individual_learning") or {}).get("mode", "SHADOW") == "CONTROL"
    learning_source = measured_source if measured_source is not None else source
    learning_rows = _daily_rows(learning_source, today) if measured_source is not None else rows
    from biathlon.learning_provenance import eligible_load_rows
    learning_rows = eligible_load_rows(learning_source, learning_rows)
    if config["feedback_enabled"]:
        from .learning_service import compatible_load_observations
        entries = compatible_load_observations(entries, learning_source)
    adaptation = (load_adaptation.symptom_context(entries, today) if control else
                  load_adaptation.assess(entries, today, rows=learning_rows)) if config["feedback_enabled"] else None
    from .management_store import ManagementStore
    retained = ManagementStore(repository).progression_reference(alias)
    settings = repository.athlete_settings(alias)
    physiology = {"bounds": list(settings.zone_bounds_bpm), "hrmax": settings.hrmax_bpm} if settings else None
    result = load_progression.context(profile, source, rows, today, adaptation, retained=retained,
                                      physiology=physiology, periodization=periodization)
    if result is not None and config["feedback_enabled"]:
        from . import learning_service
        result["individual_learning"] = learning_service.context(
            repository, alias, profile, learning_source, learning_rows, today, periodization=periodization, envelope=envelope, entries=entries)
    return result


def _learned_method(method, profile, progression, day, period, state, *, taper=False, limited=False):
    """Change a single HR target before recalculating its complete capacity.

    Composite and effort-led methods keep their approved target structure.
    The passed method is already an isolated candidate copy.
    """
    adjustment = load_progression.individual_adjustment(progression, profile, method["zone"], day, period,
                                                       state, taper=taper, limited=limited)
    delta = adjustment["intensity_delta"]
    if (not delta or method["zone"] == "STR" or method.get("double_threshold")
            or method.get("paired_method") or method["structure"] not in
            {"CONTINUOUS", "TWO_REPETITIONS", "THRESHOLD_REPETITIONS"}):
        return None
    old = method["position"]
    method["position"] = min(1., max(0., old+delta))
    return {**adjustment, "baseline_position": old, "applied_position": method["position"],
            "applied_intensity_delta": method["position"]-old,
            "capacity_recalculated": True}


def _learned_preference(method, sport, profile, progression, day, period, state, *, purpose, duration,
                        original_method=None, target_speed=None, taper=False, limited=False):
    adjustment = load_progression.individual_adjustment(progression, profile, method["zone"], day, period,
                                                       state, taper=taper, limited=limited)
    if (not adjustment["eligible"] or taper or period not in {"GENERAL_PREPARATION", "SPECIAL_PREPARATION"}
            or state.get("kind") != "BUILD"):
        return None
    preferences = ((progression or {}).get("individual_learning") or {}).get("method_preferences") or []
    from .learning_methods import method_descriptor
    current_version = method_descriptor(original_method or method, sport)["version"]
    match = next((p for p in preferences if p.get("method_id") == method["id"]
                  and p.get("version") == current_version
                  and p.get("sport") == sport and p.get("component") == method["zone"]
                  and p.get("purpose") == purpose), None)
    if not match or not isinstance(match.get("score_delta"), (int, float)) or not math.isfinite(match["score_delta"]):
        return None
    if (match.get("version") != current_version
            or not isinstance(match.get("duration_min"), (int, float))
            or not isinstance(match.get("duration_max"), (int, float))
            or not match["duration_min"] <= duration <= match["duration_max"]):
        return None
    observed_speed = match.get("planned_speed_kmh")
    if observed_speed is not None and (not isinstance(observed_speed, (int, float)) or observed_speed <= 0
            or not isinstance(target_speed, (int, float)) or not .95 <= target_speed/observed_speed <= 1.05):
        return None
    return {**match, "applied_score_delta": min(.25, max(-.25, match["score_delta"]))}


def _dose_usage(blocks, evidence, zone):
    """Largest direct-work fraction; plan Q/E checks combine received effects.

    Different components do not share an arbitrary sum-of-fractions ceiling.
    Efforts within the same component still accumulate into its one budget.
    """
    if evidence.get("strength_profile"):
        return sum(b["duration_min"] for b in blocks if b["zone"] == "STR" and b["kind"] == "WORK") / evidence["capacity_minutes"]
    if evidence.get("combined_budgets"):
        primary_fraction = evidence["combined_budgets"][0]["fraction"]
        usages = []
        for index, (cap, budget) in enumerate(zip(evidence["combined_capacities"], evidence["combined_budgets"])):
            work = sum(b["duration_min"] for b in blocks if b["kind"] == "WORK" and b.get("combination_part") == index+1)
            denominator = budget.get("whole_role_work_minutes", cap["capacity_minutes"]*budget["fraction"]*budget.get("readiness_factor", 1.))
            usages.append(work / max(1e-9, denominator) * primary_fraction * evidence["combined_budgets"][0].get("readiness_factor", 1.))
        return max(usages, default=0.)
    if any(b.get("session_index") for b in blocks):
        usages = []
        for slot in sorted({b.get("session_index", 1) for b in blocks}):
            cap = _part_capacity(evidence, slot)
            part = [{k: v for k, v in b.items() if k != "session_index"} for b in blocks if b.get("session_index", 1) == slot]
            usages.append(_dose_usage(part, cap, cap.get("zone", zone)))
        return max(usages, default=0.)
    if evidence.get("method_library"):
        # Published work owns its selected standard denominator. Segment loads
        # still enter Q/E and Recovery in their actual zones, exactly once.
        ceiling = evidence["library_work_ceiling_minutes"]
        return sum(b["duration_min"] for b in blocks if b["kind"] == "WORK") / ceiling
    capacities = {zone: evidence["capacity_minutes"] * (evidence.get("method_work_capacity_ratio", 1.) if evidence.get("dose_capacity_basis") != "INDEPENDENT_CONTINUOUS_TMAX" else 1.)}
    if evidence.get("method_library"):
        return sum(b["duration_min"] for b in blocks if b["kind"] == "WORK") / evidence["capacity_minutes"]
    if evidence.get("effort_profile") and "method_work_capacity_ratio" not in evidence and evidence.get("dose_capacity_basis") != "INDEPENDENT_CONTINUOUS_TMAX":
        capacities[zone] *= evidence["effort_profile"]["total_capacity_ratio"]
    secondary = evidence.get("secondary_capacity")
    if secondary:
        secondary_zone = secondary.get("zone", "Z1")
        capacities[secondary_zone] = secondary["capacity_minutes"] * secondary.get("effort_profile", {}).get("total_capacity_ratio", 1.)
    by_effort = evidence.get("capacity_by_effort", {})
    usages = {}
    for b in blocks:
        if b["kind"] != "WORK" or b["zone"] == "STR" or b["zone"] not in capacities:
            continue
        effort = f"{b['zone']}:{b['target_hr_bpm']:.3f}" if b.get("target_hr_bpm") is not None else None
        effort_capacity = capacities[b["zone"]]
        if effort in by_effort:
            ratio = 1. if evidence.get("dose_capacity_basis") == "INDEPENDENT_CONTINUOUS_TMAX" else evidence.get("method_work_capacity_ratio", 1.)
            effort_capacity = by_effort[effort] * ratio
        usages[b["zone"]] = usages.get(b["zone"], 0.) + b["duration_min"]/effort_capacity
    if secondary and evidence.get("secondary_max_fraction") is not None and not evidence.get("minimum_combined_fraction"):
        return usages.get(zone, 0.)
    return sum(usages.values()) if evidence.get("minimum_combined_fraction") else max(usages.values(), default=0.)


def _dose_fits(blocks, evidence, zone, maximum, *, tolerance=.001):
    if not evidence.get("combined_budgets"):
        maximum *= evidence.get("whole_dose_rounding_factor", 1.)
    if _dose_usage(blocks, evidence, zone) > maximum + tolerance:
        return False
    # An easy part keeps its own maintenance budget even when an interval
    # primary uses the selected developmental fraction of its method budget.
    secondary = evidence.get("secondary_capacity")
    limit = evidence.get("secondary_max_fraction")
    if secondary and limit is not None:
        secondary_zone = secondary.get("zone", "Z1")
        work = sum(b["duration_min"] for b in blocks if b["kind"] == "WORK" and b["zone"] == secondary_zone)
        capacity = secondary["capacity_minutes"] * secondary.get("effort_profile", {}).get("total_capacity_ratio", 1.)
        if work > capacity * limit + tolerance:
            return False
    # Method budgets constrain direct work only. Received E is accounted for
    # once by the plan's shared rolling-load and forecast Recovery checks.
    return True


def _part_capacity(evidence, slot):
    return {**evidence, **(evidence.get("paired_capacity", {}) if slot == 2 else {})}


def _minimum_dose_usage(blocks, evidence, zone, *, primary_only=False):
    """Continuous-Tmax fraction for the lower dose gate, per complete part."""
    if any(block.get("session_index") for block in blocks):
        usages = []
        for slot in sorted({block.get("session_index", 1) for block in blocks}):
            capacity = _part_capacity(evidence, slot)
            part = [{key: value for key, value in block.items() if key != "session_index"}
                    for block in blocks if block.get("session_index", 1) == slot]
            usages.append(_minimum_dose_usage(part, capacity, capacity.get("zone", zone),
                                              primary_only=primary_only))
        return min(usages, default=0.)
    if evidence.get("combined_capacities"):
        return min((sum(b["duration_min"] for b in blocks if b["kind"] == "WORK" and b.get("combination_part") == i+1) /
                    (cap["library_work_ceiling_minutes"] if (cap.get("method_library") or {}).get("resolution", {}).get("standard_work_seconds")
                     else cap["capacity_minutes"]) for i, cap in enumerate(evidence["combined_capacities"])), default=0.)
    if evidence.get("method_library"):
        if evidence["method_library"]["resolution"].get("standard_work_seconds"):
            return sum(b["duration_min"] for b in blocks if b["kind"] == "WORK") / evidence["library_work_ceiling_minutes"]
        if evidence["method_library"]["resolution"]["structure"] == "CONTINUOUS":
            return sum(b["duration_min"] for b in blocks if b["kind"] == "WORK") / evidence["capacity_minutes"]
        return sum(b["duration_min"] / (b["method_library"]["resolution"]["continuous_capacity_seconds"]/60)
                   for b in blocks if b["kind"] == "WORK")
    if primary_only:
        return sum(block["duration_min"] for block in blocks
                   if block["kind"] == "WORK" and block["zone"] == zone)/evidence["capacity_minutes"]
    capacity = {**evidence, "dose_capacity_basis": "INDEPENDENT_CONTINUOUS_TMAX", "minimum_combined_fraction": True}
    secondary = capacity.get("secondary_capacity")
    if secondary and secondary.get("effort_profile"):
        capacity["secondary_capacity"] = {**secondary, "effort_profile": {
            **secondary["effort_profile"], "total_capacity_ratio": 1.}}
    return _dose_usage(blocks, capacity, zone)


def _primary_work_share(method):
    return {"ALTERNATING": .5, "CRUISE_ALTERNATING": 2/3}.get(method["structure"], 1.)


def _interval_budget_profile(method):
    if method["structure"] in {"MODEL_INTERVALS", "LIBRARY_INTERVALS", "LIBRARY_CONTINUOUS"}:
        return method["interval_template"]
    if method["structure"] == "METABOLIC_INTERVALS":
        return method["interval_profile"]
    return None


def _nominal_fraction(method, purpose, profile, period, evidence, controls):
    fraction = _nominal_fraction_impl(method, purpose, profile, period, evidence, controls)
    evidence.pop("whole_dose_rounding_factor", None)
    evidence.pop("whole_role_work_minutes", None)
    if (evidence.get("capacity_minutes") and method["zone"] in {"Z3", "Z4", "Z5"} and evidence.get("resolved_role") == "BUILDING" and not any(method.get(k) for k in
        ("combined_methods", "mixed_component", "double_threshold", "strength_profile", "neuromuscular_profile"))):
        nominal = evidence["capacity_minutes"]*fraction*evidence.get("readiness_dose_factor", 1.)
        if nominal > 0:
            rounded = _whole_work_at_most(method, nominal,
                min(method.get("max_work_min", nominal), nominal*(1+planning_allocation.COVERAGE_TOLERANCE)))
            evidence["whole_role_work_minutes"] = max(nominal, rounded)
            evidence["whole_dose_rounding_factor"] = max(1., rounded/nominal)
    return fraction


def _nominal_fraction_impl(method, purpose, profile, period, evidence, controls):
    """Method/role dose before exactly one proportional Recovery adjustment."""
    method_dosing.prepare_native_budget(method, evidence, profile)
    purpose = adaptive_methods.purpose_for(purpose, evidence.get("readiness_policy", {}))
    evidence["resolved_role"] = "MAINTENANCE" if period == "RE_ENTRY" else purpose
    policy = evidence.get("readiness_policy", {})
    policy["dose_factor"] = recovery_factor(policy, evidence["resolved_role"])
    evidence["readiness_dose_factor"] = policy["dose_factor"]
    if method.get("strength_profile"):
        evidence["strength_period"] = period
        fraction = strength_methods.reduced_fraction(method["strength_profile"]) if evidence["resolved_role"] in strength_methods.REDUCED_ROLES else 1.
        evidence.update(method_budget_role_fraction=fraction,
                        nominal_capacity_basis="DEVELOPMENTAL_STRENGTH_REPETITIONS_PER_SESSION",
                        dose_capacity_basis="STRENGTH_REPETITIONS_NOT_TMAX",
                        minimum_dose_capacity_basis="COMPLETE_SPORT_EXERCISE_BANK")
        return fraction
    if method.get("combined_methods"):
        budgets = []
        for part, cap in zip(method["combined_methods"], evidence["combined_capacities"]):
            cap["readiness_policy"] = deepcopy(evidence.get("readiness_policy", {}))
            role = part["purpose"] if purpose == "BUILDING" else "MAINTENANCE"
            if part["zone"] not in method.get("combined_accents", [part["zone"]]) and part["zone"] not in {"Z1", "Z2"}:
                role = "MAINTENANCE"
            own = _nominal_fraction(part, role, profile, period, cap, controls)
            maximum = min(part["max_work_min"], cap["capacity_minutes"]*own*cap["readiness_dose_factor"])
            requested = min(maximum, method.get("combined_work_targets", {}).get(part["zone"], maximum))
            budgets.append({"method_id": part["id"], "zone": part["zone"], "role": role,
                            "full_role_fraction": own, "fraction": own,
                            "allocation": requested/maximum if maximum else 0.,
                            "readiness_factor": cap["readiness_dose_factor"],
                            "whole_role_work_minutes": cap.get("whole_role_work_minutes", maximum),
                            "nominal_work_minutes": cap["capacity_minutes"]*own,
                            "objective_limit_work_minutes": method.get("combined_work_limits", {}).get(part["zone"], cap.get("whole_role_work_minutes", maximum)),
                            "requested_work_minutes": requested})
        evidence.update(combined_budgets=budgets, combination_allocation="COMPONENT_TARGETS_WITHIN_OWN_ROLE_BUDGETS", nominal_capacity_basis="INDEPENDENT_METHOD_ROLE_BUDGETS")
        return budgets[0]["fraction"]
    maintenance = purpose in {"MAINTENANCE", "RECOVERY", "SUPPORTING", "ACTIVATION"} or period == "RE_ENTRY"
    if method.get("method_library"):
        r = method["method_library"]["resolution"]
        standard = r["standard_work_seconds"] / 60
        role_fraction = .6 if maintenance else 1.
        factor = policy["dose_factor"]
        evidence.update(standard_method_work_minutes=standard,
                        method_budget_role_fraction=role_fraction,
                        nominal_capacity_basis="SELECTED_SOURCE_STANDARD_WORK",
                        dose_capacity_basis="SELECTED_SOURCE_STANDARD_WORK",
                        minimum_dose_capacity_basis="DECLARED_COMPLETE_METHOD_VARIANT",
                        nominal_dose_policy="SOURCE_STANDARD_ROLE_RECOVERY_ONCE_V1",
                        method_dose_factors=[{"stage": "STANDARD_METHOD", "work_minutes": standard},
                                             {"stage": "ROLE", "factor": role_fraction},
                                             {"stage": "RECOVERY", "factor": factor}],
                        unrounded_requested_work_minutes=standard * role_fraction * factor)
        if maintenance:
            evidence["maintenance_policy"] = "SIXTY_PERCENT_SELECTED_STANDARD_NO_HIDDEN_TMAX_MULTIPLIER"
        return standard * role_fraction / evidence["capacity_minutes"]
    fraction = continuous_maintenance_fraction(profile) if maintenance else continuous_building_fraction(profile)
    if method["structure"] == "CONTINUOUS" and method["zone"] in {"Z1", "Z2"}:
        fraction = .65 * (.6 if maintenance else 1.)
        evidence["fallback_dose_policy"] = "UNDOSED_CONTINUOUS_Z1_Z2_65_PERCENT_TMAX"
    if period == "RE_ENTRY":
        evidence["reentry_dose_policy"] = "MAINTENANCE_ROLE_BUDGET"
    interval = _interval_budget_profile(method)
    if interval:
        role = fraction
        fraction = interval["total_capacity_ratio"] * role
        evidence.update(approved_interval_work_capacity_ratio=interval["total_capacity_ratio"],
                        method_budget_role_fraction=role,
                        dose_capacity_basis="WHOLE_STRUCTURE_WORK_CAPACITY",
                        nominal_capacity_basis="EXPERT_METHOD_WORK_BUDGET_TIMES_CONTINUOUS_TMAX")
        if maintenance:
            evidence["maintenance_policy"] = "SIXTY_PERCENT_METHOD_BUDGET_NO_RECOVERY_SCALE"
    elif method["zone"] == "STR":
        fraction = .6 if maintenance else 1.
    elif not method.get("double_threshold"):
        evidence["method_budget_role_fraction"] = fraction
        fraction *= evidence.get("method_work_capacity_ratio", 1.)
        if maintenance:
            evidence["maintenance_policy"] = "SIXTY_PERCENT_METHOD_BUDGET_NO_RECOVERY_SCALE"
    if method.get("double_threshold"):
        fraction = 2*(controls or {}).get("double_threshold_fraction", .5) * fraction * evidence.get("method_work_capacity_ratio", 1.)
    if method["zone"] != "STR":
        evidence.setdefault("nominal_capacity_basis", "CONTINUOUS_TMAX_AT_PRESCRIBED_EFFORT")
        evidence["nominal_dose_policy"] = "INDIVIDUAL_TMAX_METHOD_ROLE_HALF_DEFICIT_V5"
        evidence["minimum_dose_capacity_basis"] = "INDEPENDENT_CONTINUOUS_TMAX"
    return fraction


def _role_work_limit(evidence, nominal):
    if evidence.get("combined_budgets"):
        return evidence["combined_budgets"][0]["whole_role_work_minutes"]
    return evidence.get("whole_role_work_minutes", nominal)


def _dose_ceiling(method, purpose, profile, period, evidence, controls, generic_ceiling=.8, *, reentry=False):
    """Express a role's ceiling in the denominator used by _dose_usage."""
    if reentry:
        period = "RE_ENTRY"
    if method.get("strength_profile"):
        return _nominal_fraction(method, purpose, profile, period, evidence, controls)
    method_dosing.prepare_native_budget(method, evidence, profile)
    purpose = adaptive_methods.purpose_for(purpose, evidence.get("readiness_policy", {}))
    evidence["resolved_role"] = "MAINTENANCE" if period == "RE_ENTRY" else purpose
    if method.get("combined_methods"):
        _nominal_fraction(method, purpose, profile, period, evidence, controls)
        return evidence["combined_budgets"][0]["fraction"]
    if method.get("method_library"):
        _nominal_fraction(method, purpose, profile, period, evidence, controls)
        return evidence["method_budget_role_fraction"]
    if method.get("double_threshold"):
        return (controls or {}).get("double_threshold_fraction", .5) * (continuous_maintenance_fraction(profile) if purpose != "BUILDING" or period == "RE_ENTRY" else continuous_building_fraction(profile))
    interval = _interval_budget_profile(method)
    if interval:
        maximum = continuous_maintenance_fraction(profile) if purpose in {"MAINTENANCE", "RECOVERY", "SUPPORTING", "ACTIVATION"} or period == "RE_ENTRY" or reentry else continuous_building_fraction(profile)
    else:
        nominal = _nominal_fraction(method, purpose, profile, period, evidence, controls)
        maximum = min(generic_ceiling, nominal / evidence.get("method_work_capacity_ratio", 1.)) if method["zone"] != "STR" else generic_ceiling
        if reentry:
            maintenance_limit = .39 if method["structure"] == "CONTINUOUS" and method["zone"] in {"Z1", "Z2"} else continuous_maintenance_fraction(profile)
            maximum = min(maximum, maintenance_limit)
    if evidence.get("secondary_capacity"):
        evidence["secondary_max_fraction"] = (.6 if evidence["secondary_capacity"].get("effort_profile")
                                               else continuous_maintenance_fraction(profile))
        evidence["combination_allocation"] = "COMPONENT_METHOD_BUDGETS_WITH_SHARED_CANONICAL_Q_E"
        evidence["session_dose_policy"] = "DIRECT_WORK_WITHIN_METHOD_ROLE_BUDGETS_E_FOR_PLAN_CONSTRAINTS"
    return maximum


def _prepare_threshold_high_budget(method, evidence, profile, settings, speed, context, today,
                                   *, primary_fraction, readiness_factor):
    """Resolve both parts of a mixed method for reservation and prescription.

    The caller supplies the already-halved primary role fraction. The high
    component owns half its explicit interval budget; Recovery scales each
    component once, using the same factor for the complete combination.
    """
    p = method["interval_profile"]
    controls = profile.get("planning_controls") or {}
    high_method = {**method, "zone": p["zone"], "structure": "METABOLIC_INTERVALS"}
    high_capacity = capacity_for(high_method,
        settings, speed, context, today, profile.get("allow_expert_fallback", True),
        use_model_prior=controls.get("capacity_policy") == "MODEL_WITH_PRIOR")
    if high_capacity is None:
        return False
    method_dosing.prepare_native_budget(high_method, high_capacity, profile)
    high_fraction = p["total_capacity_ratio"] * continuous_building_fraction(profile) * (.30 if evidence.get("resolved_role") in {"MAINTENANCE", "SUPPORTING", "RECOVERY"} else .5) * readiness_factor
    evidence.update(primary_requested_work=evidence["capacity_minutes"] * primary_fraction * readiness_factor,
                    combination_high_work_cap=high_capacity["capacity_minutes"] * high_fraction,
                    secondary_capacity={**high_capacity, "zone": p["zone"], "fraction": high_fraction})
    return True


def _locked_dose_fits(session, profile, readiness):
    policy = adaptive_methods.readiness_policy(session, profile, readiness)
    factor = recovery_factor(policy, session.get("purpose"))
    evidence = session["dose_evidence"]
    if (factor <= 0 or "base_fraction" not in evidence
        or adaptive_methods.purpose_for(session.get("purpose"), policy) != session.get("purpose")):
        return False
    primary = sum(b["duration_min"] for b in session["blocks"] if b["kind"] == "WORK" and b["zone"] == session["zone"])
    base_max = evidence.get("base_max_dose_fraction", .8)
    rounding = evidence.get("whole_dose_rounding_factor", 1.)
    if evidence.get("combined_budgets"):
        first = evidence["combined_budgets"][0]
        nominal = evidence["capacity_minutes"]*first["fraction"]*first.get("readiness_factor", 1.)
        rounding = max(1., first.get("whole_role_work_minutes", nominal)/max(1e-9, nominal))
    return (primary <= evidence["capacity_minutes"]*evidence["base_fraction"]*factor*rounding + .005
            and _dose_fits(session["blocks"], {**evidence, "readiness_dose_factor": factor}, session["zone"], base_max*factor, tolerance=.005))


def _quantized_work(method, work):
    """Keep source distance cycles exact; half minutes belong to legacy methods."""
    if method.get("combined_methods"):
        return _quantized_work(method["combined_methods"][0], work)
    if method.get("strength_profile"):
        return strength_methods.quantize(method, work)
    if method.get("structure") == "STRENGTH_CIRCUIT":
        return math.floor((work+1e-9)/3)*3.
    if method.get("structure") in {"MODEL_INTERVALS", "METABOLIC_INTERVALS"}:
        interval = method.get("interval_template") or method.get("interval_profile")
        if interval and interval.get("work_seconds"):
            step = interval["work_seconds"]/60
            return math.floor((work+1e-9)/step)*step
    if method.get("method_library"):
        r = method["method_library"]["resolution"]
        if r["structure"] in {"CONTINUOUS", "SCALED_SEQUENCE", "PROGRESSIVE_CONTINUOUS"}:
            return work
        if r.get("work_options_seconds"):
            return max((s/60 for s in r["work_options_seconds"] if s/60 <= work+1e-9), default=0.)
        cycle = r["cycle_work_seconds"] * r["sets"] / 60
        return math.floor((work + 1e-9) / cycle) * cycle
    return math.floor(work * 2) / 2


def _previous_work(method, work):
    if method.get("combined_methods"):
        return _previous_work(method["combined_methods"][0], work)
    if method.get("strength_profile"):
        return strength_methods.previous(method, work)
    if method.get("structure") == "STRENGTH_CIRCUIT":
        return _quantized_work(method, work)-3.
    if method.get("method_library") and method["method_library"]["resolution"]["structure"] != "CONTINUOUS":
        r = method["method_library"]["resolution"]
        if r["structure"] in {"SCALED_SEQUENCE", "PROGRESSIVE_CONTINUOUS"}:
            return work - .5
        if r.get("work_options_seconds"):
            return max((s/60 for s in r["work_options_seconds"] if s/60 < work-1e-9), default=0.)
        return _quantized_work(method, work) - r["cycle_work_seconds"] * r["sets"] / 60
    return work - .5


def _whole_work_at_most(method, requested, ceiling):
    """Prefer the next whole dose, without crossing a method/Recovery ceiling."""
    lower = _quantized_work(method, min(requested, ceiling))
    if lower >= requested-1e-9:
        return lower
    # Discrete source recipes can have nonuniform or distance-based steps.
    lo, hi = requested, ceiling
    if _quantized_work(method, hi) <= lower+1e-9:
        return lower
    for _ in range(24):
        mid = (lo+hi)/2
        if _quantized_work(method, mid) > lower+1e-9:
            hi = mid
        else:
            lo = mid
    return _quantized_work(method, min(ceiling, hi+1e-7))


def _absolute_minimum(method):
    # Existing easy maintenance templates own their declared minimum. Their
    # upper dose still comes from capacity/readiness; building methods retain
    # the relative minimum. Library/combined methods keep their own contracts.
    return (method.get("purpose") == "RECOVERY" or
            method.get("purpose") == "MAINTENANCE" and method.get("zone") in {"Z1", "Z2"}
            and method.get("structure") == "CONTINUOUS" and not method.get("planned_long_session") and not any(method.get(k) for k in
                ("method_library", "mixed_component", "combined_methods", "developmental_variant")))


def _minimum_work(method, evidence, settings):
    """Smallest complete structure meeting the relative minimum on a .5-min grid.

    Relative minima use each effort's continuous Tmax. Approved interval
    ratios define the method work budget independently of this lower gate.
    Each threshold session meets its own minimum; supplements use a smaller one.
    Strength keeps its separate circuit/profile minimum, without aerobic Tmax.
    """
    if method.get("strength_profile"):
        return strength_methods.minimum_work(method, evidence)
    if method['zone'] == 'STR' or _absolute_minimum(method):
        return method['min_work_min']
    if method.get("combined_methods"):
        budgets = evidence.get("combined_budgets") or []
        if len(budgets) != len(method["combined_methods"]):
            return None
        primary_budget = budgets[0]["requested_work_minutes"]
        required = 0.
        part_minima = []
        for part, cap, budget in zip(method["combined_methods"], evidence["combined_capacities"], budgets):
            minimum = _minimum_work(part, cap, settings)
            own_budget = budget["requested_work_minutes"]
            if minimum is None or own_budget <= 0 or primary_budget <= 0:
                return None
            part_minima.append(minimum)
            required = max(required, minimum * primary_budget / own_budget)
        primary = method["combined_methods"][0]
        resolution = (primary.get("method_library") or {}).get("resolution") or {}
        options = ([v/60 for v in resolution["work_options_seconds"]] if resolution.get("work_options_seconds")
                   else [required] if resolution.get("structure") in {"CONTINUOUS", "SCALED_SEQUENCE", "PROGRESSIVE_CONTINUOUS"}
                   else [math.ceil(required * 2 - 1e-9) / 2])
        for amount in options:
            if amount + 1e-9 >= required and amount <= method["max_work_min"] + 1e-9:
                blocks = _blocks(method, amount, evidence, settings)
                if blocks and all(sum(b["duration_min"] for b in blocks if b["kind"] == "WORK" and b.get("combination_part") == i+1) >= minimum-1e-9
                                  for i, minimum in enumerate(part_minima)):
                    evidence.update(combined_minimum_work_minutes=part_minima, minimum_dose_scope="EACH_COMBINATION_PART")
                    return amount
        return None
    if method.get("method_library"):
        r = method["method_library"]["resolution"]
        # The complete declared variant is the method's minimum. Applying a
        # second generic 25%-of-Tmax floor would silently redose the source.
        return r["minimum_work_seconds"] / 60
    primary_only = bool(method.get("mixed_component") or method.get("developmental_variant"))
    minimum = method.get("minimum_fraction", MIN_AEROBIC_DOSE_FRACTION) * evidence.get("readiness_dose_factor", 1.)
    for half_minutes in range(math.ceil(method['min_work_min']*2), math.floor(method['max_work_min']*2)+1):
        work = half_minutes / 2
        blocks = _blocks(method, work, evidence, settings)
        if blocks and _minimum_dose_usage(blocks, evidence, method["zone"], primary_only=primary_only) + 1e-9 >= minimum:
            return work
    return None


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
        state = planning_controls.resolve(profile, day, phase, list(PRIORITIES.get(phase, ("Z1",))), periodization=periodization)
        week = max(0, (day-anchor).days//7) % length
        factor = state["volume_factor"] if state else training_targets.cycle_factor(
            week, length, profile.get("progression_percent", 5),
            development=not limited and phase in {"GENERAL_PREPARATION", "SPECIAL_PREPARATION"})
        weighted += available[day.weekday()]*factor*_taper_factor(periodization, day)
    return min(available_window, baseline*weighted/max(1., sum(available)))


def _copy_method_budget(method):
    """Separate a trial's mutable budget from its read-only recipe metadata.

    Native budget preparation replaces interval/dosing dictionaries. Combined
    budgets also prepare their child methods, so those need independent
    dictionaries. Source recipes, guidance and curve resolutions are read-only
    throughout a trial; published method cards still receive a full copy.
    """
    method = dict(method)
    if method.get("combined_methods"):
        method["combined_methods"] = [_copy_method_budget(part) for part in method["combined_methods"]]
    return method


def _copy_capacity_budget(capacity):
    """Keep speculative role/Recovery edits out of the source capacity."""
    capacity = dict(capacity)
    if capacity.get("combined_capacities"):
        capacity["combined_capacities"] = [_copy_capacity_budget(cap) for cap in capacity["combined_capacities"]]
    if capacity.get("readiness_policy") is not None:
        capacity["readiness_policy"] = dict(capacity["readiness_policy"])
    return capacity


def _volume_estimate(profile, components, baseline, actual_base, days):
    """Display-only duration equivalent at the observed load/time mix.

    Zone targets govern dosing. This approximation is not a second time quota
    and cannot convert an individual component's effective load into raw time.
    """
    zones = [z for z in COMPONENTS if z != "STR" or profile.get("strength_enabled")]
    observed = sum(actual_base[z]["c40"]*7 for z in zones)
    target = sum(components[z]["target_weekly_effective"] or 0. for z in zones)
    return _round(baseline*target/observed*days/7) if observed > 0 else None


def _long_term_outlook(profile, periodization, reference, accents, preferences, rows, today, limited, *, volume=None, events=None, progression=None, planning_evidence=None):
    """Read-only coach targets before the daily planner's eligibility gates.

    No synthetic sessions or future recovery. Ratios use the current actual
    C40 and B50, frozen explicitly; they are targets, not forecast 7/40 values.
    Camps are calendar context, never an automatic permission to add load.
    """
    start = max(today, date.fromisoformat(profile["program_start"]))
    planning_evidence = planning_evidence or (progression or {}).get("planning_history") or {}
    estimated = planning_evidence.get("estimated", False)
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
        desired_q_targets = {z: [] for z in COMPONENTS}
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
                desired_q_targets[z].append(goals[z].get("desired_weekly_q",goals[z].get("target_weekly_q")))
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
                             "desired_weekly_q": _round(sum(desired_q_targets[z])/len(desired_q_targets[z])) if all(v is not None for v in desired_q_targets[z]) else None,
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
    return {"schema_version": "training-outlook-v1", "as_of": today.isoformat(),
            "progression": public_management(load_progression.public_context(progression)),
            "individual_learning": public_learning((progression or {}).get("individual_learning")),
            "basis": "ESTIMATED_PLANNING_REFERENCE_FROZEN" if estimated else "CURRENT_ACTUAL_REFERENCE_FROZEN", "targets_version": training_targets.VERSION,
            "planning_history": planning_evidence,
            "reference_cutoff": reference["cutoff"], "limited": limited,
            "goal_role": "COACH_TARGETS_BEFORE_DAILY_GATES",
            "readiness_forecast": False, "automatic_camp_load_increase": False,
            "baseline": {z: {**actual_base[z], "estimated": estimated,
                             "actual_index_7_40": baseline[z]["index_7_40"] if actual_base[z]["known"] and not estimated else None,
                             "estimated_index_7_40": baseline[z]["index_7_40"] if actual_base[z]["known"] and estimated else None} for z in COMPONENTS},
            "weeks": weeks}


def generate_plan(repository, alias: str, profile: dict, *, start_date: date, now: datetime | None = None,
                  decisions: dict | None = None, locked_day: dict | None = None,
                  window_end: date | None = None) -> dict:
    from .management_schemas import normalize_building_profile
    profile = normalize_building_profile(profile)
    # Candidate trials share immutable historical calculations only within
    # this plan. Keys include doses, dates and configuration; no cross-user cache.
    recovery_calculation_cache = {}
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
    if window_end is not None:
        if not start_date <= window_end <= start_date + timedelta(days=6):
            raise ValueError("The approved week end must bound the remaining seven-day window")
        end_date = min(end_date, window_end)
    horizon = (end_date - start_date).days + 1
    decisions = decisions or {}
    calendar_settings = _read_optional(repository, "athlete_planning_calendar", alias) or {"events": []}
    events = calendar_settings.get("events", [])
    original_event_profile = profile
    profile = race_course.apply_event(profile, events, start_date)
    events = [*events, *[{"event_type": "UNAVAILABLE", "start_date": d, "end_date": d,
                         "event_id": f"management-{d}", "name": choice["action"]}
                        for d, choice in decisions.items() if choice["action"] in {"SKIP", "REST"}]]
    preferences = _read_optional(repository, "athlete_planning_profile", alias) or {}
    controls = profile.get("planning_controls")
    reservation_methods = (profile.get("_allocation_policy") or {}).get("reservation_methods", {})
    def reservation_preference(method_id, method_sport, dates):
        return any(r["method_id"] == method_id and r["sport"] == method_sport
                   for d in dates for r in reservation_methods.get(d.isoformat(), []))
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
    speed_by_sport = {s: model_service.speed_view(repository, alias, s) if envelope else None for s in dict.fromkeys([sport, *training_sports])}
    prepared_source = planning_source_details(repository, alias, profile, settings, envelope, today, speed_by_sport=speed_by_sport)
    source, planning_evidence = prepared_source.source, prepared_source.evidence
    recorded_sessions = training_sessions.group_recordings(source.get("activities", []),
        planning_history_estimate.normalized_calendar(envelope.get("activities", [])), settings.timezone)
    component_contexts = (source.get("component_load_model") or {}).get("contexts_by_sport") or {}
    measured_source = prepared_source.measured_source
    snapshot = {**snapshot, "load_history": measured_source}
    envelope = {**envelope, "snapshot_payload": snapshot}
    profile = {**profile, "_component_load_context": component_contexts[primary_sport]}
    from types import SimpleNamespace
    settings = SimpleNamespace(**{name: getattr(settings, name) for name in dir(settings)
        if not name.startswith("_") and not callable(getattr(settings, name))},
        load_contexts_by_sport=component_contexts, load_default_sport=primary_sport)
    rows = _daily_rows(source, today)
    volume_evidence = planning_controls.volume_history(source, today, 0, gap_days=(controls or {}).get("history_gap_days", planning_history.DEFAULT_GAP_DAYS))
    history_policy = volume_evidence["history_policy"]
    recorded_exposures = planning_history.component_exposure_dates(measured_source,
        (today-timedelta(days=28)).isoformat(), today.isoformat())
    exposure_history_dates = set(history_policy["complete_dates"])
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
    missing_days = planning_history.completed_day_lag(source, today)
    limited = not history_policy["usable"] or min(component_history_days.values()) < planning_history.MINIMUM_DAYS or not planning_evidence["supported"]
    equivalence_changed = not load_progression.history_matches(source, {"bounds": list(settings.zone_bounds_bpm), "hrmax": settings.hrmax_bpm})
    if equivalence_changed:
        limited = True
        warnings.append(_warning("EQUIVALENCE_REANALYSIS_REQUIRED", "Нужен е нов анализ на активностите по текущите пулсови граници и приравняване (Z5: 5% за удар). До тогава новите дози са задържани."))
    if limited:
        warnings.append(_warning("LIMITED_LOAD_HISTORY", "Историята е кратка или съдържа непълни активности. Само ограничени леки предложения; липсващата умора не се приема за нулева."))
    if planning_evidence["estimated"]:
        estimate_basis = ("по валидни скоростни сегменти и експертни Q опори за непокрития остатък"
                          if planning_evidence.get("speed_estimated_activity_count") else "по експертните Q опори")
        warnings.append(_warning("ESTIMATED_LOAD_HISTORY", "Общата продължителност е записана. Непокритият товар е оценен "
            + estimate_basis + "; разпределението по зони, 7/40 и Recovery за плана са приблизителни, а не измерени."))
    methods = resolved_methods(profile)
    warnings.append(_warning("VERSIONED_COACHING_RULES", "Целите и прогресията използват видими начални треньорски правила. Нисък стрес сам по себе си не увеличава товара."))
    if (controls and controls.get("double_threshold_days") and "Z4" in controls.get("double_threshold_components", [])
            and not any(p.get("zone") == "Z4" and p.get("goal") == "THRESHOLD" for p in profile.get("interval_profiles", []))):
        warnings.append(_warning("DOUBLE_THRESHOLD_PROFILE_REQUIRED", "За прагова част в Z4 е нужен индивидуален прагoв интервален профил. Профил за аеробна мощност не го замества."))
    for missing in catalog(profile)["disabled"]:
        warnings.append(_warning("METHOD_PROFILE_" + missing["component"], missing["reason"]))
    blocked = equivalence_changed or (missing_days is not None and missing_days > 0)
    if missing_days:
        limited = True
        warnings.append(_warning("STALE_LOAD_SNAPSHOT", "Има непокрити дни след последния анализ. Обновете активностите; липсата на запис не доказва почивка."))
    if start_date > today + timedelta(days=1):
        limited = True
        warnings.append(_warning("UNKNOWN_INTERVENING_LOAD", "Началото е след повече от един ден. Междинните тренировки още не са известни; проектът е ограничен и трябва да се преизчисли преди изпълнение."))
    speed = speed_by_sport[sport]
    if speed and (speed.get("source_generation_id") != envelope.get("generation_id") or
                  speed.get("source_revision") != envelope.get("revision")):
        blocked = True
        warnings.append(_warning("INPUT_GENERATION_CHANGED", "Анализът се е обновил по време на генерирането. Генерирайте отново с една съгласувана версия."))
    context = _capacity_context(speed, settings)
    context_by_sport = {sport: context}
    for other in training_sports:
        if other == sport:
            continue
        other_speed = speed_by_sport[other]
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
    if race_speed and event_duration["source"] in {"SPEED_DURATION", "COURSE_EXPECTATION"} and (
            race_speed.get("source_generation_id") != envelope.get("generation_id") or
            race_speed.get("source_revision") != envelope.get("revision")):
        blocked = True
        warnings.append(_warning("INPUT_GENERATION_CHANGED", "Моделът за състезателната продължителност се е обновил. Генерирайте отново."))
    profile = race_duration.applied(profile, event_duration)
    event_reference = race_specific.reference(profile, race_speed, event_duration, today)
    event_duration["specific_reference"] = event_reference
    from . import method_progression_service
    profile = {**profile, "_method_progression": method_progression_service.context(repository, alias, measured_source, today)}
    recorded_sessions = training_sessions.with_confirmed_methods(recorded_sessions,
        profile["_method_progression"]["observations"])
    methods = method_registry.executable_methods(profile, speed_by_sport, legacy_methods=methods, race_reference=event_reference)
    method_days = {}
    def methods_for_day(day):
        event_profile = race_course.apply_event(original_event_profile, events, day)
        target_id = (event_profile.get("_race_event") or {}).get("event_id")
        initial_id = (profile.get("_race_event") or {}).get("event_id")
        if target_id == initial_id:
            return methods
        if target_id not in method_days:
            current_duration = race_duration.resolve(event_profile, race_speed)
            current_reference = race_specific.reference(event_profile, race_speed, current_duration, today)
            generic = [m for m in methods if not m.get("race_specific")]
            race_methods = race_specific.methods(event_profile, current_reference, resolved_methods(event_profile))
            method_days[target_id] = generic + method_registry.executable_methods(event_profile, {}, legacy_methods=race_methods)
        return method_days[target_id]
    # Resolve the same race component before constructing the growth calendar
    # in both the weekly generator and the read-only outlook.
    progression = progression_context(repository, alias, profile, source, rows, today, periodization, envelope=envelope, measured_source=measured_source)
    return_supported = (training_return.supported(progression) and not equivalence_changed
        and missing_days == 0 and planning_evidence["supported"]
        and min(component_history_days.values()) >= planning_history.MINIMUM_DAYS)
    if (progression or {}).get("training_return", {}).get("status") == "CONFLICT":
        blocked = True
        warnings.append(_warning("TRAINING_RETURN_CONFLICT", "Посочената пълна пауза съдържа записано натоварване. Коригирайте датите или отбележете, че подготовката е продължила."))
    if (progression or {}).get("training_return", {}).get("status") == "PAUSED":
        blocked = True
        warnings.append(_warning("TRAINING_RETURN_NOT_STARTED", "Потвърдената пауза още не е приключила. Генерирайте актуална програма при подновяване на подготовката."))
    if return_supported and start_date <= today+timedelta(days=1):
        limited = False
        warnings = [w for w in warnings if w["code"] != "LIMITED_LOAD_HISTORY"]
        warnings.append(_warning("LINEAR_TRAINING_RETURN", "Линейното връщане използва потвърдената пауза и запазеното Q. Срокът е условен; непокритите и неизпълнените седмици не натрупват прираст."))
    adaptation = (progression or {}).get("adaptation") or {}
    if adaptation.get("hold_for_reported_illness_or_pain"):
        blocked = True
        warnings.append(_warning("REPORTED_ILLNESS_OR_PAIN", load_adaptation.symptom_message(adaptation)))
    individual = (progression or {}).get("individual_learning") or {}
    if individual.get("mode") == "CONTROL" and (individual.get("current") or {}).get("lab_review"):
        blocked = True
        warnings.append(_warning("LEARNING_REVIEW", "Нужен е преглед на текущите наблюдения. Самообучението не променя програмата автоматично; прегледайте „Стрес и възстановяване“."))
    if not profile.get("race_duration_min"):
        warnings.append(_warning("RACE_DURATION_MISSING", "Няма индивидуална оценка за тази дистанция. Въведете приблизителната продължителност на основната дисциплина в профила."))
    by_sport_minutes = volume_evidence["reference_by_sport_weekly_minutes"]
    volume = planning_controls.volume_basis(profile, volume_evidence)
    if return_supported:
        episode = progression["training_return"]
        by_sport_minutes = {**episode["by_sport_weekly_minutes"], **by_sport_minutes}
        return_sports = set(episode["known_sports"])
        # Explicit restart without retained Q uses only the selected sport's
        # expert start; capacity and per-method checks are still mandatory.
        if all(c["source"] == "EXPERT_START" for c in episode["components"].values()):
            return_sports.add(primary_sport)
        volume = {**volume, "missing_history": False, "reported_history_only": False,
                  "weekly_volume_source": "CONFIRMED_RETURN_COMPONENT_Q",
                  "return_exposure_sports": sorted(return_sports)}
    else:
        return_sports = set()
    weekly_minutes = volume["baseline_weekly_minutes"]
    volume_source = volume["weekly_volume_source"]
    historical_weekly_minutes = volume["historical_selected_weekly_minutes"]
    if volume["reported_history_only"]:
        limited = True
        warnings.append(_warning("AGGREGATE_HISTORY_ONLY", "Въведеният седмичен обем ограничава проекта, но не се превръща в измислени дневни товари или известна готовност."))
    if volume["missing_history"]:
        blocked = True
        warnings.append(_warning("WEEKLY_VOLUME_REQUIRED", "Нужна е достатъчна реална история или обемите от последните четири седмици."))
    if not any(by_sport_minutes.get(s, 0.) or s in return_sports for s in training_sports):
        limited = True
        warnings.append(_warning("NO_ACTUAL_MODE_EXPOSURE", "Няма скорошен обем за избраните средства. Общият бюджет не замества опита и капацитета за конкретното средство; предложенията са ограничени."))
    available = planning_history.availability(profile)
    availability_mode = planning_history.availability_mode(profile)
    for weekday in preferences.get("rest_days", []):
        available[weekday] = 0
    component_governed = bool(controls) and not limited and not volume["reported_history_only"]
    display_end = end_date
    meso_anchor = date.fromisoformat(str(preferences.get("mesocycle_anchor_date", profile["program_start"])))
    allocation_start = start_date
    if component_governed:
        allocation_start, end_date = planning_schedule.microcycle_bounds(
            start_date, display_end, meso_anchor, program_start, program_end)
        horizon = (end_date-start_date).days+1
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
    if not component_governed:
        key_limit = max(0, key_limit - len(reserved_race_days))
    meso_length = preferences.get("mesocycle_length_weeks", 4)
    def meso_at(day):
        week = max(0, (day - meso_anchor).days // 7) % meso_length
        period, _ = _phase(periodization, day)
        development = not limited and period in {"GENERAL_PREPARATION", "SPECIAL_PREPARATION"}
        state = planning_controls.resolve(profile, day, period, list(PRIORITIES.get(period, ("Z1",))), periodization=periodization)
        return week, state["volume_factor"] if state else training_targets.cycle_factor(week, meso_length, profile.get("progression_percent", 5), development=development)
    target_reference = training_targets.development_reference(rows, today, meso_anchor, meso_length)
    meso_week, meso_factor = meso_at(start_date)
    def observed_session_ceiling(sport, day, overhead, *, quality=False):
        observed = volume_evidence["max_session_minutes_by_sport"].get(sport, 0.)
        # A measured maximum is an exposure reference, not an explicit time
        # prohibition. Complete building structures may use the same 10%
        # prescription tolerance; explicit daily/weekly time caps still apply.
        tolerance = 1+planning_allocation.COVERAGE_TOLERANCE if quality and planning_history_supported else 1.
        return observed*max(1., meso_at(day)[1])*tolerance if observed else profile.get("recovery_session_cap_min", 30.)+overhead
    # Measured automatic history uses component 7/40 budgets, including spill.
    # Incomplete indicator coverage also retains an initial recorded-time
    # envelope, independent of the provisional component distribution.
    # This limits this draft, not capacity or the unchanged component targets.
    available_window = sum(available[(start_date+timedelta(days=i)).weekday()] for i in range((end_date-start_date).days+1))
    weekly_ceiling = available_window if component_governed else _volume_ceiling(
        profile, periodization, events, available, weekly_minutes, start_date, end_date, preferences, limited)
    if controls and controls.get("weekly_target_hours") is not None:
        weekly_ceiling = min(weekly_ceiling, controls["weekly_target_hours"]*60*((end_date-start_date).days+1)/7)
    estimated_initial_time = (planning_evidence["estimated"] and not (progression or {}).get("history_usable")
                              and (controls or {}).get("weekly_target_hours") is None)
    if estimated_initial_time:
        weekly_ceiling = min(weekly_ceiling, weekly_minutes*((end_date-start_date).days+1)/7)
        warnings.append(_warning("ESTIMATED_HISTORY_TIME_ENVELOPE", "Началният обем за планирания период следва записаното средно седмично време. Изпълнените тренировки преди този период не изчерпват този ориентир. Зададен от вас седмичен лимит може да го замести при спазени Recovery и правила за дозата."))
    automatic_time = component_governed and availability_mode == "AUTO_HISTORY" and controls.get("weekly_target_hours") is None and not estimated_initial_time
    # An inferred mean only bounds this prospective draft. Reusing it as a
    # rolling cap would turn a recent above-average week into compulsory rest
    # even when the coach set no time limit. Actual prior load is governed by
    # the component 7/40 and Recovery checks; explicit time limits remain rolling.
    rolling_time_limit = (controls["weekly_target_hours"]*60 if controls and controls.get("weekly_target_hours") is not None
                          else None)
    remaining = weekly_ceiling
    sport_spent = {s: sum(float(a.get("duration_min") or 0.) for a in source.get("activities", [])
                            if a.get("sport") == s and a["date"] == today.isoformat()) if start_date == today else 0.
                   for s in training_sports}
    def strength_activity(activity):
        return (activity.get("sport") == "WeightTraining" or activity.get("zone") == "STR"
                or any(z.get("zone") == "STR" and z.get("raw_time_min", 0) > 0 for z in activity.get("zones", []))
                or any(b.get("zone") == "STR" and b.get("kind") == "WORK" for b in activity.get("blocks", [])))
    strength_actual = {a.get("activity_ref") or ("unidentified", index): a
        for index, a in enumerate(source.get("activities", [])) if strength_activity(a)
        and min(allocation_start, start_date-timedelta(days=6)).isoformat() <= a["date"] <= end_date.isoformat()}
    strength_recorded_sessions = [s for s in recorded_sessions if any(strength_activity(a) for a in s.activities)
        and min(allocation_start, start_date-timedelta(days=6)).isoformat() <= s.date <= end_date.isoformat()]
    def recorded_strength_building(session):
        return any(strength_activity(a) and a.get("purpose") not in {"MAINTENANCE", "ACTIVATION"}
                   for a in session.activities)
    def strength_week(day):
        first = meso_anchor + timedelta(days=7*((day-meso_anchor).days//7))
        return first.isoformat(), (first+timedelta(days=6)).isoformat()
    def strength_counts(day, extra=()):
        first, last = strength_week(day)
        actual = [s for s in strength_recorded_sessions if first <= s.date <= last]
        proposed = [session for d in result_days if first <= d["date"] <= last
                    for session in planning_schedule.day_sessions(d) if strength_activity(session)]
        # A locked day is represented exactly once when it reaches result_days.
        if locked_day and first <= locked_day["date"] <= last and not any(d.get("locked") for d in result_days):
            proposed += [v for v in locked_sessions if strength_activity(v)
                         and v.get("activity_ref") not in strength_actual]
        window_start = (day-timedelta(days=6)).isoformat() if component_governed else start_date.isoformat()
        window_end = day.isoformat() if component_governed else end_date.isoformat()
        window_actual = [s for s in strength_recorded_sessions if window_start <= s.date <= window_end]
        window = [v for d in result_days if window_start <= d["date"] <= window_end
                   for v in planning_schedule.day_sessions(d) if strength_activity(v)]
        if (locked_day and window_start <= locked_day["date"] <= window_end
            and not any(d.get("locked") for d in result_days)):
            window += [v for v in locked_sessions if strength_activity(v)
                       and v.get("activity_ref") not in strength_actual]
        cycle_extra = [building for d, building in extra if first <= d.isoformat() <= last]
        window_extra = [building for d, building in extra if window_start <= d.isoformat() <= window_end]
        return (max(len(actual)+len(proposed)+len(cycle_extra), len(window_actual)+len(window)+len(window_extra)),
                max(sum(recorded_strength_building(s) for s in actual)
                    + sum(v.get("purpose") not in {"MAINTENANCE", "ACTIVATION"} for v in proposed)+sum(cycle_extra),
                    sum(recorded_strength_building(s) for s in window_actual)
                    + sum(v.get("purpose") not in {"MAINTENANCE", "ACTIVATION"} for v in window)+sum(window_extra)))
    strength_sessions = len(strength_recorded_sessions)
    # Unknown execution roles cannot be used to claim the required maintenance session.
    strength_building = sum(recorded_strength_building(s) for s in strength_recorded_sessions)
    strength_limit = strength_methods.session_limit(controls,
        accent="STR" in _accents(_phase(periodization, start_date)[0], preferences))
    if controls:
        strength_accents = planning_controls.resolve(profile, start_date, _phase(periodization, start_date)[0],
            list(PRIORITIES.get(_phase(periodization, start_date)[0], ("Z1",))), periodization=periodization)
        strength_limit = strength_methods.session_limit(controls,
            accent=bool(strength_accents and "STR" in strength_accents["accents"]))
    last_strength_day = max([date.fromisoformat(a["date"]) for a in source.get("activities", [])
                             if a.get("sport") == "WeightTraining" and a["date"] <= today.isoformat()] or [None])
    actual_window_minutes = sum(float(a.get("duration_min") or 0.) for a in source.get("activities", [])
                                if start_date.isoformat() <= a["date"] <= end_date.isoformat())
    remaining = max(0., remaining - actual_window_minutes)
    planning_history_supported = not limited and missing_days == 0 and planning_evidence["supported"]
    all_sources_known = planning_history_supported and not planning_evidence["estimated"]
    forecast_known = planning_history_supported
    result_days = []
    # Historical execution can precede the visible draft but still belong to
    # its anchored microcycle. Slot occupancy below remains draft-local.
    executed_dates = {date.fromisoformat(a["date"]) for a in source.get("activities", [])}
    existing_in_draft = [a for a in source.get("activities", []) if start_date.isoformat() <= a["date"] <= end_date.isoformat()]
    actual_by_day = {d: [a for a in existing_in_draft if a["date"] == d.isoformat()] for d in slot_counts}
    recorded_sessions_by_day = {d: [s for s in recorded_sessions if s.date == d.isoformat()] for d in slot_counts}
    recorded_in_draft = [s for s in recorded_sessions if start_date.isoformat() <= s.date <= end_date.isoformat()]
    from .double_threshold_continuation import reconcile as reconcile_double
    if locked_day:
        locked_day = reconcile_double(locked_day, actual_by_day.get(date.fromisoformat(locked_day["date"]), []), profile["_method_progression"]["observations"])
    def actual_slots_known(d):
        # An imported session consumes its own slot, not the entire day.
        # Ambiguous metadata and old locked bundles retain the old guard.
        # A reviewed remaining bundle records the imports already present;
        # new imports close it rather than guessing their workout identity.
        actual = actual_by_day[d]
        catalog = [a for a in actual_activities if a.get("local_date") == d.isoformat()]
        return bool(component_governed and actual and len(actual) == len(catalog)
            and {a.get("activity_ref") for a in actual} == {a.get("activity_ref") for a in catalog}
            and len({a.get("activity_ref") for a in actual}) == len(actual)
            and all(a.get("activity_ref") and (a.get("duration_min") or 0) > 0
                and planning_history.recorded_zone_minutes(a, unified=bool(source.get("load_selection")),
                    require_complete=True) is not None for a in actual)
            and any(r["date"] == d.isoformat() and r["effective_load"] > 0 for r in rows)
            and (not locked_day or locked_day["date"] != d.isoformat()
                or set(locked_day.get("activity_refs", [])) == {a["activity_ref"] for a in actual}))
    def first_plannable_slot(d):
        return (len(recorded_sessions_by_day[d]) if actual_slots_known(d) else slot_counts[d]) if actual_by_day[d] else 0

    def unexecuted_slot_count(d):
        return max(0, slot_counts[d]-first_plannable_slot(d))

    sessions = len(recorded_in_draft)
    key_sessions = sum(s.is_key for s in recorded_in_draft)
    forecast_rows = deepcopy(rows)
    recent_key_dates = [date.fromisoformat(s.date) for s in recorded_sessions
                        if s.date <= today.isoformat() and s.is_key]
    last_key_day = max(recent_key_dates) if recent_key_dates else None
    # Imported execution may already close the approved day's slots. Keep the
    # lock for identity/continuation checks, but count only tasks still pending.
    # Otherwise the actual workout and its obsolete approved task consume two
    # strength/key quotas and block valid sessions later in the draft.
    locked_date = date.fromisoformat(locked_day["date"]) if locked_day else None
    locked_sessions = (planning_schedule.day_sessions(locked_day)
        if locked_date in slot_counts and unexecuted_slot_count(locked_date) > 0 else [])
    if any(v.get("is_key_session", v["zone"] in {"Z3", "Z4", "Z5"}) for v in locked_sessions):
        last_key_day = date.fromisoformat(locked_day["date"])
        key_sessions += sum(v.get("is_key_session", v["zone"] in {"Z3", "Z4", "Z5"}) for v in locked_sessions)
    if any(v["zone"] == "STR" for v in locked_sessions):
        last_strength_day = date.fromisoformat(locked_day["date"])
        strength_sessions += sum(v["zone"] == "STR" for v in locked_sessions)
        strength_building += sum(v["zone"] == "STR" and v.get("purpose") not in {"MAINTENANCE", "ACTIVATION"} for v in locked_sessions)
    activation_eligible = planning_history_supported and not blocked
    def session_counts(at_day, extra=()):
        """Real and proposed sessions in the same rolling seven-day window."""
        left, right = (at_day-timedelta(days=6)).isoformat(), at_day.isoformat()
        actual = [s for s in recorded_sessions if left <= s.date <= right]
        planned = [s for d in result_days if left <= d["date"] <= right
                   for s in planning_schedule.day_sessions(d)]
        if (locked_day and left <= locked_day["date"] < right
            and not any(d.get("locked") for d in result_days)):
            planned += locked_sessions
        count = len(actual)+len(planned)
        keys = sum(s.is_key for s in actual)
        keys += sum(s.get("is_key_session", s["zone"] in {"Z3", "Z4", "Z5"}) for s in planned)
        keys += len({str(e["start_date"]) for e in events
                     if e["event_type"] in {"MAIN_RACE", "CONTROL_RACE", "TEST"}
                     and left <= str(e["start_date"]) <= right
                     and not any(s.date == str(e["start_date"]) for s in actual)})
        for d, n, is_key in extra:
            if left <= d.isoformat() <= right:
                count += n
                keys += n if is_key else 0
        return count, keys

    def time_headroom(at_day, at_days):
        if estimated_initial_time:
            # Whole-microcycle allocation may include provisional days after
            # the visible draft. Keep its initial recorded-time envelope
            # intact without charging completed work before the draft starts.
            left = max(start_date, at_day-timedelta(days=6)).isoformat()
            right = at_day.isoformat()
            limit = weekly_minutes
            if at_day <= display_end:
                limit *= ((display_end-start_date).days+1)/7
        elif rolling_time_limit is not None:
            left, right = (at_day-timedelta(days=6)).isoformat(), at_day.isoformat()
            limit = rolling_time_limit
        else:
            return float("inf")
        actual = sum(float(a.get("duration_min") or 0.) for a in source.get("activities", [])
                     if left <= a["date"] <= right)
        planned = sum(s["total_minutes"] for d in at_days if left <= d["date"] <= right
                      for s in planning_schedule.day_sessions(d))
        return max(0., limit-actual-planned)
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
        if not unexecuted_slot_count(d):
            continue
        previous_key = key_slots[-1] if key_slots else last_key_day
        if previous_key and (d - previous_key).days < 2:
            continue
        needed = 2 if d.weekday() in double_days and unexecuted_slot_count(d) >= 2 else 1
        keys_used = session_counts(d, [(v, 2 if v.weekday() in double_days and unexecuted_slot_count(v) >= 2 else 1, True)
                                      for v in key_slots])[1] if component_governed else key_sessions+reserved_key_count
        if needed <= max(0, key_limit - keys_used):
            key_slots.append(d)
            reserved_key_count += needed
    covered_slots = {}
    # Establish the entire week's component intent before choosing methods.
    # Calendar changes inside the week retain separate rolling targets.
    goal_windows, day_goals, segment_keys = {}, {}, {}
    actual_target_reference = planning_controls.reference(rows, today)
    for offset in range((end_date-allocation_start).days+1):
        d = allocation_start + timedelta(days=offset)
        period, taper = _phase(periodization, d)
        week, _ = meso_at(d)
        day_goals[d] = _goals(profile, d, period, taper, target_reference, accents,
                             week, meso_length, rows, today, limited, _taper_factor(periodization, d), progression, periodization=periodization,
                             support_limited=limited, actual_reference=actual_target_reference)
        segment_keys[d] = (d-meso_anchor).days // 7
        goal_windows[d] = day_goals[d][0]
    opportunities = {z: [] for z in COMPONENTS}
    for d, slot, count in schedule:
        if not count or any(e["event_type"] in {"MAIN_RACE", "CONTROL_RACE", "TEST", "UNAVAILABLE"}
                            and str(e["start_date"]) <= d.isoformat() <= str(e["end_date"]) for e in events):
            continue
        if actual_by_day[d] and (not actual_slots_known(d) or slot < len(recorded_sessions_by_day[d])):
            continue
        for z in COMPONENTS:
            if z in {"Z3", "Z4", "Z5"} and d not in key_slots and not (progression and z == "Z3" and key_slots and d > key_slots[-1]):
                continue
            if z in {"Z3", "Z4", "Z5"} and slot >= first_plannable_slot(d)+(2 if d.weekday() in double_days and unexecuted_slot_count(d) >= 2 else 1):
                continue
            if z == "STR" and not profile.get("strength_enabled"):
                continue
            opportunities[z].append((d, slot))
    def component_budgets(at_rows, at_days, d):
        """Distribute Q; replay actual E through 7/40 and Recovery separately."""
        values = _budgets(at_rows, d, actual_rows=rows, targets=goal_windows[d])
        if not component_governed:
            return values, load_progression.remaining_q(source, at_days, d, goal_windows[d]), None
        segment = planning_allocation.segment_headroom(goal_windows, rows, at_rows, source, at_days, d,
                                                       segment_keys=segment_keys)
        return _prescription_budgets(at_rows, d, values, segment, continuity_reserve(segment))

    continuity_references = {}
    manual_components = set(profile.get("component_targets_weekly") or {})

    def continuity_reserve(segment):
        """Freeze reference doses per anchored cycle, independently of forecasts."""
        if segment is None:
            return {"q": {}, "e": {}, "references": {}}
        identity = segment["window_start"]
        eligible = {z for z, row in segment["components"].items()
                    if z not in manual_components and (planning_continuity.eligible(row) or
                        z in {"Z1", "Z2"} and row["basis"] == "DIRECT_Q" and row.get("actual_q") is not None and row["target"] > 0)}
        if identity not in continuity_references:
            references = {}
            reference_day = date.fromisoformat(identity)
            reference_period = _phase(periodization, reference_day)[0]
            for original in methods_for_day(reference_day) if eligible else []:
                z = original["zone"]
                if (z not in eligible or original.get("mixed_component") or original.get("combined_methods")
                    or original.get("double_threshold") or original.get("neuromuscular_profile")
                    or original["purpose"] == "SUPPORTING" or reference_period not in original["periods"]
                    or original["structure"] not in {"CONTINUOUS", "TWO_REPETITIONS", "THRESHOLD_REPETITIONS",
                        "THRESHOLD_LONG", "THRESHOLD_SHORT", "MODEL_INTERVALS", "METABOLIC_INTERVALS",
                        "LIBRARY_CONTINUOUS", "LIBRARY_INTERVALS", "STRENGTH_CIRCUIT", "STRENGTH_SETS"}):
                    continue
                for reference_sport in training_sports:
                    if (reference_sport not in original["sports"] or not by_sport_minutes.get(reference_sport, 0.)
                        or z == "STR" and reference_sport != primary_sport):
                        continue
                    method = _copy_method_budget(original)
                    method["actual_sport"] = reference_sport
                    cap = capacity_for(method, settings, speed_by_sport[reference_sport], context_by_sport[reference_sport],
                        today, profile.get("allow_expert_fallback", True),
                        use_model_prior=(controls or {}).get("capacity_policy") == "MODEL_WITH_PRIOR")
                    if cap is None:
                        continue
                    cap["readiness_policy"] = adaptive_methods.readiness_policy(method, profile, {c:100. for c in COMPONENTS})
                    role = "RECOVERY" if method["purpose"] == "RECOVERY" else "MAINTENANCE"
                    nominal = _nominal_fraction(method, role, profile, reference_period, cap, controls)
                    minimum = _minimum_work(method, cap, settings)
                    maximum = min(method["max_work_min"], cap["capacity_minutes"]*nominal)
                    if role == "RECOVERY" or z in {"Z1", "Z2"}:
                        maximum = min(maximum, profile.get("recovery_session_cap_min", 30.))
                    work = _quantized_work(method, maximum)
                    ceiling = _dose_ceiling(method, role, profile, reference_period, cap, controls)
                    blocks = _blocks(method, work, cap, settings) if minimum is not None and work >= minimum else []
                    while blocks and not _dose_fits(blocks, cap, z, ceiling, tolerance=1e-6):
                        work = _previous_work(method, work)
                        blocks = _blocks(method, work, cap, settings) if work >= minimum else []
                    if not blocks:
                        continue
                    q, e, _ = _candidate_load(blocks, settings, rows, reference_day, zone_tmax_minutes=cap["zone_tmax_minutes"])
                    if q[z] <= .005:
                        continue
                    reference = {"method_id":method["id"], "sport":reference_sport, "q":q, "e":e,
                                 "work_minutes":work, "purpose":role}
                    # Pick the least costly complete nominal maintenance dose,
                    # never the largest method in a growing catalogue.
                    if z not in references or (q[z], q["Z1"], method["id"]) < (
                            references[z]["q"][z], references[z]["q"]["Z1"], references[z]["method_id"]):
                        references[z] = reference
            continuity_references[identity] = references
        return planning_continuity.reserves(segment["components"], continuity_references[identity], manual_components)

    def reservation_fits(d, method, blocks, direct, effective, budgets, remaining_q, segment):
        """Future tasks spend the same dated ledger and reserve as today's task."""
        if segment is None:
            return (all(effective[z] <= budgets[z]["deficit_effective"]+.001 for z in COMPONENTS)
                    and all(direct[z] <= v+.001 for z, v in remaining_q.items()))
        period, taper = _phase(periodization, d)
        state = day_goals[d][2]
        allowed = (planning_history_supported and not taper and not reentry_dose_active(periodization, d)
                   and period != "TRANSITION" and (not state or state["kind"] != "RECOVERY"))
        reserve = continuity_reserve(segment) if allowed else {"q": {}, "e": {}, "references": {}}
        z, components = method["zone"], segment["components"]
        simple = not any(method.get(k) for k in ("mixed_component", "combined_methods", "double_threshold", "neuromuscular_profile"))
        maintenance = (allowed and simple and z in reserve["references"]
            and planning_continuity.eligible(components[z], planned_easy=z in {"Z1", "Z2"})
            and (z not in {"Z1", "Z2"} or _absolute_minimum(method)))
        headroom = planning_continuity.limits(components, budgets, remaining_q, reserve, zone=z,
            maintenance=maintenance, preparation=bool(allowed and "Z1" not in manual_components
                and components["Z1"]["basis"] == "DIRECT_Q" and z != "Z1"
                and not any(b["kind"] == "WORK" and b["zone"] == "Z1" for b in blocks)))
        return planning_continuity.fits(direct, effective, headroom, components)

    def candidate_catalog(d, slot=0):
        candidates, shortage = cached_candidate_catalog(d, slot, len(result_days), tuple(key_slots))
        return list(candidates), shortage

    @lru_cache(maxsize=128)
    def cached_candidate_catalog(d, slot, history_revision, key_dates):
        """Use the same complete methods for reservations and actual selection.

        A slot shortage is evaluated for the dated microcycle. Combinations
        retain their own component role doses and pass the ordinary Q/E, time and
        Recovery checks in either caller.
        """
        candidates = [(sport, m) for sport in training_sports for m in methods_for_day(d)
                      if sport in m["sports"] and (m["zone"] != "STR" or sport == primary_sport)]
        period, taper = _phase(periodization, d)
        if not controls or not controls.get("mixed_sessions_enabled", True) or limited or taper:
            return candidates, combined_methods.shortage({}, {}, 0)
        _, remaining_q, segment = component_budgets(forecast_rows, result_days, d)
        deficits = ({z: row["remaining"] or 0. for z, row in segment["components"].items()}
                    if segment else remaining_q)
        free = [(fd, fs) for fd, fs, count in schedule if count and fs >= first_plannable_slot(fd)
                and available[fd.weekday()] > 0 and not any(ev["event_type"] in
                    {"UNAVAILABLE", "MAIN_RACE", "CONTROL_RACE", "TEST"}
                    and str(ev["start_date"]) <= fd.isoformat() <= str(ev["end_date"]) for ev in events)]
        separate = min(max(0, session_limit-session_counts(d)[0]), planning_allocation.remaining_slots(
            free, d, slot, segment_keys=segment_keys if segment else None))
        key_count = sum(2 if kd.weekday() in double_days and unexecuted_slot_count(kd) >= 2 else 1
            for kd in key_slots if kd >= d and (not segment or segment_keys[kd] == segment_keys[d])
            and (kd > d or slot == first_plannable_slot(d)))
        nominal, vectors, key_costs, work_per_q = {}, [], [], {}
        state, accents = day_goals[d][2], day_goals[d][1]
        building_zones = set(accents)
        if state and state.get("background_development"):
            building_zones.update(z for z in COMPONENTS if mesocycle_focus.growth_weight(state, z) > 0)
        for sport, original in candidates:
            if (original.get("mixed_component") or original["structure"] in method_registry.COMBINED_STRUCTURES
                or period not in original["periods"] or original["purpose"] not in {"BUILDING", "MAINTENANCE"}):
                continue
            parent = _copy_method_budget(original)
            parent["actual_sport"] = sport
            cap = capacity_for(parent, settings, speed_by_sport[sport], context_by_sport[sport], today,
                profile.get("allow_expert_fallback", True), use_model_prior=controls.get("capacity_policy") == "MODEL_WITH_PRIOR")
            if cap is None:
                continue
            policy = adaptive_methods.readiness_policy(parent, profile, {z:100. for z in COMPONENTS})
            cap["readiness_policy"] = policy
            role = parent["purpose"] if parent["zone"] in building_zones or parent["zone"] in {"Z1", "Z2"} else "MAINTENANCE"
            fraction = _nominal_fraction(parent, role, profile, period, cap, controls)
            work = _quantized_work(parent, min(parent["max_work_min"], cap["capacity_minutes"]*fraction*policy["dose_factor"]))
            core = _blocks(parent, work, cap, settings) if work >= parent["min_work_min"] else []
            if core:
                q, e, _ = _candidate_load(core, settings, forecast_rows, d, zone_tmax_minutes=cap.get("zone_tmax_minutes"))
                vector = {z: q[z] if segment and segment["components"][z]["basis"] == "DIRECT_Q" else e[z] for z in COMPONENTS}
                vectors.append(vector)
                key_costs.append(int(training_sessions.role_is_key(parent["zone"], role)))
                nominal[parent["zone"]] = max(nominal.get(parent["zone"], 0.), vector[parent["zone"]])
                primary_work = sum(b["duration_min"] for b in core if b["kind"] == "WORK" and b["zone"] == parent["zone"])
                if q[parent["zone"]] > 0 and primary_work > 0:
                    work_per_q[(sport, parent["id"])] = primary_work/q[parent["zone"]]
        shortage = combined_methods.shortage(deficits, nominal, separate, vectors=vectors,
            key_costs=key_costs, available_key_slots=key_count)
        combos = combined_methods.candidates([(s, m) for s, m in candidates if period in m["periods"]], shortage)
        for sport, combo in combos:
            combo["combined_accents"] = sorted(building_zones)
            combo["combined_work_targets"] = {p["zone"]: deficits[p["zone"]]*work_per_q[(sport, p["id"])]
                for p in combo["combined_methods"] if (sport, p["id"]) in work_per_q
                and segment and segment["components"][p["zone"]]["basis"] == "DIRECT_Q"}
            combo["combined_work_limits"] = {p["zone"]: (planning_allocation.prescription_headroom(segment["components"][p["zone"]]) or 0.)*work_per_q[(sport, p["id"])]
                for p in combo["combined_methods"] if p["zone"] in combo["combined_work_targets"]}
            combo["method_card"] = method_registry.coach_card(combo)
            combo["method_card"]["components"] = [deepcopy(p.get("method_library") or p.get("method_card")) for p in combo["combined_methods"]]
        return candidates + combos, shortage

    def executable_key(d, forecast_ready, future_budget, future_q, future_segment):
        """A calendar reservation requires a complete executable method."""
        for sport in training_sports:
            if controls and not by_sport_minutes.get(sport, 0.):
                continue
            for candidate_sport, original in candidate_catalog(d, first_plannable_slot(d))[0]:
                if candidate_sport != sport:
                    continue
                if (original["zone"] not in {"Z3", "Z4", "Z5"} or original["purpose"] in {"RECOVERY", "SUPPORTING"}
                    or sport not in original["sports"] or _phase(periodization, d)[0] not in original["periods"]):
                    continue
                fm = _copy_method_budget(original)
                future_phase = _phase(periodization, d)[0]
                future_state = day_goals[d][2]
                race_zones = (([future_state["race_component"]] if future_phase == "COMPETITION" else future_state["mesocycle_accents"])
                              if future_state and future_state.get("race_component") else [])
                if (future_state and controls and controls["accent_mode"] == "AUTO" and not future_state["explicit"]
                    and future_phase in {"PRECOMPETITION", "COMPETITION"} and fm["purpose"] == "BUILDING"
                    and fm["zone"] not in race_zones):
                    continue
                if fm["structure"] == "MODEL_INTERVALS" and not fm.get("developmental_variant"):
                    exposures = recorded_exposures.get((sport, fm["zone"]), set()) & exposure_history_dates
                    if len(exposures) < 2:
                        fm = adaptive_methods.short_variant(fm)
                fm["actual_sport"] = sport
                threshold_choice = (controls or {}).get("threshold_method", "AUTO")
                if not threshold_allowed(fm, threshold_choice):
                    continue
                cap = capacity_for(fm, settings, speed_by_sport[sport], context_by_sport[sport], today,
                    profile.get("allow_expert_fallback", True), use_model_prior=(controls or {}).get("capacity_policy") == "MODEL_WITH_PRIOR")
                if cap is None:
                    continue
                cap["readiness_policy"] = adaptive_methods.readiness_policy(fm, profile, forecast_ready)
                factor = cap["readiness_policy"]["dose_factor"]
                if factor <= 0:
                    continue
                cap["readiness_dose_factor"] = factor
                if fm.get("developmental_variant"):
                    cap["dose_capacity_basis"] = "INDEPENDENT_CONTINUOUS_TMAX"
                purpose = fm["purpose"]
                background = bool(future_state and future_state.get("background_development")
                                  and mesocycle_focus.growth_weight(future_state, fm["zone"]) > 0)
                if purpose == "BUILDING" and (reentry_dose_active(periodization, d)
                    or fm["zone"] not in day_goals[d][1] and not background
                    or future_state and future_state["kind"] == "RECOVERY"):
                    purpose = "MAINTENANCE"
                purpose = adaptive_methods.purpose_for(purpose, cap["readiness_policy"])
                if purpose != "BUILDING":
                    continue
                nominal = _nominal_fraction(fm, purpose, profile, "RE_ENTRY" if reentry_dose_active(periodization, d) else _phase(periodization, d)[0], cap, controls)
                factor = cap["readiness_dose_factor"]
                if fm["structure"] == "THRESHOLD_HIGH":
                    nominal *= .5
                    if not _prepare_threshold_high_budget(fm, cap, profile, settings,
                            speed_by_sport[sport], context_by_sport[sport], today,
                            primary_fraction=nominal, readiness_factor=factor):
                        continue
                minimum = _minimum_work(fm, cap, settings)
                if minimum is None or factor <= 0 or minimum > _role_work_limit(cap, cap["capacity_minutes"]*nominal*factor) + .001:
                    continue
                blocks = _blocks(fm, minimum, cap, settings)
                ceiling = _dose_ceiling(fm, purpose, profile, future_phase, cap, controls,
                    (progression or {}).get("config", {}).get("max_dose_fraction", .8),
                    reentry=reentry_dose_active(periodization, d))
                if not blocks or not _dose_fits(blocks, cap, fm["zone"], ceiling*factor):
                    continue
                if sum(b["duration_min"] for b in blocks) > available[d.weekday()]-sum(a.get("duration_min") or 0. for a in actual_by_day[d]) + .001:
                    continue
                fq, fe, _ = _candidate_load(blocks, settings, forecast_rows, d, zone_tmax_minutes=cap.get("zone_tmax_minutes"))
                if reservation_fits(d, fm, blocks, fq, fe, future_budget, future_q, future_segment):
                    return True
        return False

    key_schedule_changes = []
    def key_day_due(d, previous):
        if not key_days:
            return True
        first = max(start_date, previous + timedelta(days=1)) if previous else start_date
        if any((first + timedelta(days=i)).weekday() in key_days
               for i in range(max(0, (d-first).days+1))):
            return True
        # A preferred weekday in the next microcycle cannot postpone this
        # segment's executable remaining work. Capacity, whole-dose Q/E,
        # readiness and spacing are still checked before reserving the day.
        if component_governed:
            # This is catch-up for an unserved segment, not another key after
            # the segment's planned preferred task has already been reserved.
            if (previous in segment_keys and segment_keys[previous] == segment_keys[d]
                and previous not in executed_dates) or any(
                    segment_keys[date.fromisoformat(item["date"])] == segment_keys[d]
                    and any(s.get("is_key_session") for s in planning_schedule.day_sessions(item))
                    for item in result_days):
                return False
            segment_end = min(program_end, meso_anchor + timedelta(days=7*segment_keys[d]+6))
            if segment_end > end_date:
                return False
            return not any(later >= d and segment_keys[later] == segment_keys[d]
                and later.weekday() in key_days and slot_counts[later]
                and available[later.weekday()] >= 25
                and not any(e['event_type'] in {'MAIN_RACE','CONTROL_RACE','TEST','UNAVAILABLE'}
                    and str(e['start_date']) <= later.isoformat() <= str(e['end_date']) for e in events)
                and unexecuted_slot_count(later)
                for later in slot_counts)
        return False

    def reserve_remaining_keys(after):
        """Preferred days establish a rhythm; missed work moves forward."""
        if limited or blocked:
            return
        previous = last_key_day
        retained_dates = [d for d in key_slots if d <= after]
        future, reserved = [], 0
        for offset in range(horizon):
            d = start_date+timedelta(days=offset)
            if d <= after:
                continue
            period, taper = _phase(periodization,d)
            if period not in {"GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"} or taper:
                continue
            if (available[d.weekday()] < 25 or not slot_counts[d] or not key_day_due(d, previous)
                    or previous and (d-previous).days < 2):
                continue
            if any(e["event_type"] in {"MAIN_RACE","CONTROL_RACE","TEST","UNAVAILABLE"}
                   and str(e["start_date"]) <= d.isoformat() <= str(e["end_date"]) for e in events):
                continue
            if not unexecuted_slot_count(d):
                continue
            forecast = {r["zone"]:r["readiness_percent"] for r in recovery_v2.simulate(forecast_rows,configs["zones"],target=d,include_details=False, include_deadlines=False, calculation_cache=recovery_calculation_cache)["current"]}
            future_budget, future_q, future_segment = component_budgets(forecast_rows, result_days, d)
            if forecast["Z1"] <= 0 or future_budget["Z1"].get("rolling_deficit_effective", future_budget["Z1"]["deficit_effective"]) <= 0:
                continue
            if not executable_key(d, forecast, future_budget, future_q, future_segment):
                continue
            needed = 2 if d.weekday() in double_days and unexecuted_slot_count(d) >= 2 else 1
            keys_used = session_counts(d, [(v, 2 if v.weekday() in double_days and unexecuted_slot_count(v) >= 2 else 1, True)
                                          for v in future])[1] if component_governed else key_sessions+reserved
            needed = min(needed, max(0, key_limit-keys_used))
            if not needed:
                continue
            future.append(d)
            reserved += needed
            previous = d
        old = [d for d in key_slots if d > after]
        if future != old:
            key_schedule_changes.append({"after":after.isoformat(),"from":[d.isoformat() for d in old],
                "to":[d.isoformat() for d in future],"reason":"PREFERRED_RHYTHM_WITH_ADAPTIVE_CATCHUP"})
        key_slots[:] = retained_dates+future
        for z in ("Z3","Z4","Z5"):
            opportunities[z] = [(d,s) for d,s,count in schedule if count
                and (d in key_slots or progression and z == "Z3" and key_slots and d > key_slots[-1])
                and first_plannable_slot(d) <= s < first_plannable_slot(d)+(2 if d.weekday() in double_days and unexecuted_slot_count(d) >= 2 else 1)
                and not any(e["event_type"] in {"MAIN_RACE","CONTROL_RACE","TEST","UNAVAILABLE"}
                    and str(e["start_date"]) <= d.isoformat() <= str(e["end_date"]) for e in events)
                and unexecuted_slot_count(d)]
    reserve_remaining_keys(start_date-timedelta(days=1))
    visible_state = None
    for day, slot_index, slots_today in schedule:
        if day > display_end and visible_state is None:
            visible_state = (blocked, forecast_known, activation_eligible)
        if component_governed:
            sessions, key_sessions = session_counts(day)
        strength_sessions, strength_building = strength_counts(day)
        day_state = day_goals[day][2]
        strength_limit = strength_methods.session_limit(controls, accent="STR" in day_goals[day][1])
        if slot_index < covered_slots.get(day, 0):
            continue
        prior_today = [d for d in result_days if d["date"] == day.isoformat()]
        if prior_today and any(d["status"] not in {"TRAINING", "REST", "EXISTING_ACTIVITY"} or d.get("locked")
            or d["status"] == "EXISTING_ACTIVITY" and not actual_slots_known(day) for d in prior_today):
            continue
        day_spent = sum(v["total_minutes"] for d in prior_today for v in planning_schedule.day_sessions(d))
        actual_day_minutes = sum(a.get("duration_min") or 0. for a in actual_by_day[day])
        day_available = min(max(0., available[day.weekday()] - day_spent - actual_day_minutes),
                            time_headroom(day, result_days))
        key = day.isoformat()
        period, taper = _phase(periodization, day)
        taper_day_headroom = (max(0., weekly_minutes / max(1, sum(v > 0 for v in available))
            * _taper_factor(periodization, day) - day_spent - actual_day_minutes)
            if taper and (weekly_minutes > 0 or not return_supported) else float("inf"))
        day_dose_available = min(day_available, taper_day_headroom)
        # Calendar-day Recovery cannot infer intraday recovery. Forecast the
        # cumulative load, but assess the bundle against day-start readiness.
        day_start_rows = [r for r in forecast_rows if r["date"] != key] + [r for r in rows if r["date"] == key]
        # Dose trials on this day use the same causal spill reference. Compute
        # it once; it is not a dose capacity and never reads future proposals.
        technical_reference = _canonical_load([], settings, day_start_rows, day)[2]
        def candidate_load(blocks, *, capacity):
            # Freeze the actual means' denominator at every call site; the
            # last candidate in a multi-sport loop must never own this load.
            return _candidate_load(blocks, settings, day_start_rows, day, technical_reference=technical_reference,
                zone_tmax_minutes=capacity["zone_tmax_minutes"])
        recovery_policy_rows = ([r for r in day_start_rows if r["date"] != key]
                                if locked_day and locked_day.get("double_threshold_continuation") and locked_day["date"] == key else day_start_rows)
        recovery_before = recovery_v2.simulate(recovery_policy_rows, configs["zones"], target=day, include_details=False, include_deadlines=False, calculation_cache=recovery_calculation_cache)
        ready = {r["zone"]: r["readiness_percent"] for r in recovery_before["current"]}
        day_meso_week, day_meso_factor = meso_at(day)
        goals, selected_accents, cycle_state = day_goals[day]
        budgets, q_remaining, active_segment = component_budgets(forecast_rows, result_days, day)
        for z, remaining_q in q_remaining.items():
            budgets[z].update(target_weekly_q=goals[z].get("target_weekly_q", goals[z]["target"] if z == "STR" else None), remaining_q=remaining_q)
        period_objectives = active_segment["components"] if active_segment else None
        continuity_allowed = bool(period_objectives and planning_history_supported and not taper
            and not reentry_dose_active(periodization, day) and period != "TRANSITION"
            and (not cycle_state or cycle_state["kind"] != "RECOVERY"))
        taper_continuity_allowed = bool(period_objectives and planning_history_supported and taper
            and not reentry_dose_active(periodization, day) and period != "TRANSITION"
            and (not cycle_state or cycle_state["kind"] != "RE_ENTRY"))
        maintenance_reserve = continuity_reserve(active_segment) if continuity_allowed else {"q": {}, "e": {}, "references": {}}
        allocation = planning_allocation.quota(goal_windows, opportunities, forecast_rows, day, slot_index,
            objectives=period_objectives) if component_governed else None
        def objective_load(direct, effective):
            return planning_allocation.objective_load(direct, effective, period_objectives) if period_objectives else effective
        visible_ready = {z: _round(ready[z]) if forecast_known else None for z in COMPONENTS}
        item = {"date": key, "status": "REST", "period": period, "taper": taper, "cycle": cycle_state,
                "session": None, "slot": slot_index + 1, "readiness_scope": "DAY_START_BUNDLE_NOT_INTRADAY_FORECAST", "readiness_before": visible_ready, "readiness_after": dict(visible_ready),
                "load_budget": {"remaining_weekly_minutes": None if automatic_time else _round(max(0., remaining)), "components": budgets,
                                "mesocycle_week_index": day_meso_week, "mesocycle_factor": day_meso_factor},
                "rejected_alternatives": [], "explanation": "Почивка; не е необходимо да се запълва всяка свободна минута."}
        if actual_day_minutes:
            item["actual_minutes"] = _round(actual_day_minutes)
            item["actual_sessions"] = len(recorded_sessions_by_day[day])
            item["actual_activity_records"] = len(actual_by_day[day])
        if allocation is not None:
            item["load_budget"]["component_allocation"] = {z: _round(v) for z, v in allocation.items()}
        events_today = [e for e in events if str(e["start_date"]) <= key <= str(e["end_date"])]
        existing = [a for a in actual_activities if a.get("local_date") == key]
        if not existing:
            existing = [a for a in source.get("activities", []) if a["date"] == key]
        known_actual_load = any(r["date"] == key and r["effective_load"] > 0 for r in rows)
        if existing and (not actual_slots_known(day) or slot_index < len(recorded_sessions_by_day[day])):
            item.update(status="EXISTING_ACTIVITY", explanation="Има реално изпълнена активност. Не се добавя втора планирана доза за същия ден.",
                        activity_refs=[a.get("activity_ref") for a in existing])
            if actual_slots_known(day) and len(recorded_sessions_by_day[day]) < slots_today:
                item["explanation"] = "Изпълнените занимания заемат собствените си сесии. Близките записи се броят заедно; целият им товар се отчита при проверката на останалите места."
        elif known_actual_load and not existing:
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
        elif any(e["event_type"] == "UNAVAILABLE" for e in events_today) or (
            day_available <= 0 and time_headroom(day, result_days) > .001):
            item.update(status="UNAVAILABLE", explanation="Почивен или недостъпен ден според календара и предпочитанията.")
            if key in decisions:
                item.update(status="SKIPPED" if decisions[key]["action"] == "SKIP" else "REST",
                            explanation="Отбелязано пропускане без наваксване." if decisions[key]["action"] == "SKIP" else "Почивка по индивидуално решение.")
        elif locked_day and locked_day["date"] == key and locked_sessions:
            preserved = deepcopy(locked_sessions)
            effective = {z: 0. for z in COMPONENTS}
            preserved_q = {z: 0. for z in COMPONENTS}
            for session in preserved:
                direct_load, load, _ = _canonical_load(session["blocks"], settings, day_start_rows, day, zone_tmax_minutes=session.get("dose_evidence", {}).get("zone_tmax_minutes"))
                session["direct_equivalent_minutes"] = {z: _round(v) for z, v in direct_load.items()}
                session["canonical_effective_load"] = {z: _round(v) for z, v in load.items()}
                session["total_minutes"] = _round(sum(b["duration_min"] for b in session["blocks"]))
                for z in COMPONENTS:
                    effective[z] += load[z]
                    preserved_q[z] += direct_load[z]
            total = sum(v["total_minutes"] for v in preserved)
            preserved_budget_ok = (all(effective[z] <= budgets[z]["deficit_effective"] + .001 for z in COMPONENTS)
                and all(preserved_q[z] <= value + .001 for z, value in q_remaining.items()))
            if not preserved_budget_ok and (continuity_allowed or taper_continuity_allowed):
                # Revalidate an approved exception against today's real ledger.
                # Saved metadata is explanatory, never authority to refill it.
                shadow_rows, shadow_days = forecast_rows, list(result_days)
                preserved_budget_ok = True
                for session in preserved:
                    cb, cq, cs = component_budgets(shadow_rows, shadow_days, day)
                    reserve = continuity_reserve(cs) if continuity_allowed else {"q": {}, "e": {}, "references": {}}
                    z = session["zone"]
                    maintenance = (session["purpose"] in {"MAINTENANCE", "RECOVERY"}
                        and z in reserve["references"]
                        and planning_continuity.eligible(cs["components"][z], planned_easy=z in {"Z1", "Z2"})
                        and not session.get("mixed_component")
                        and not session.get("double_threshold"))
                    allowance = planning_continuity.limits(cs["components"], cb, cq, reserve, zone=z,
                        maintenance=maintenance, preparation=bool("Z1" not in manual_components
                            and cs["components"]["Z1"]["basis"] == "DIRECT_Q" and z != "Z1"
                            and not any(b["kind"] == "WORK" and b["zone"] == "Z1" for b in session["blocks"])))
                    if taper_continuity_allowed:
                        simple = (not session.get("mixed_component") and not session.get("double_threshold")
                            and not session["dose_evidence"].get("combination_allocation")
                            and session["dose_evidence"].get("structure_work_scope") == "PRIMARY_WORK"
                            and not session.get("neuromuscular_exposure"))
                        allowance = planning_continuity.taper_limits(cs["components"], cb, cq, zone=z,
                            light=simple and session["purpose"] in {"MAINTENANCE", "RECOVERY"},
                            preparation=simple and not any(b["kind"] == "WORK" and b["zone"] == "Z1" for b in session["blocks"]),
                            manual=manual_components)
                    direct, effective_part = session["direct_equivalent_minutes"], session["canonical_effective_load"]
                    if not planning_continuity.fits(direct, effective_part, allowance, cs["components"]):
                        preserved_budget_ok = False
                        break
                    current_exception = planning_continuity.evidence(direct, effective_part, allowance, cs)
                    if current_exception:
                        current_exception["history_basis"] = "MEASURED" if all_sources_known else "SUPPORTED_WITH_ESTIMATES"
                        session["dose_evidence"]["continuity"] = current_exception
                    else:
                        session["dose_evidence"].pop("continuity", None)
                    shadow_rows = _add_forecast_session(shadow_rows, day, effective_part)
                    shadow_days.append({"date":key, "sessions":[session]})
            below_minimum = any(s["zone"] != "STR" and
                _minimum_dose_usage(s["blocks"], s["dose_evidence"], s["zone"], primary_only=bool(s.get("mixed_component") or s["dose_evidence"].get("developmental_variant"))) + 1e-9 < (s["dose_evidence"].get("min_dose_fraction", MIN_AEROBIC_DOSE_FRACTION) or 0.)
                for s in preserved)
            if below_minimum:
                item.update(status="REVIEW_REQUIRED", explanation="Днешната утвърдена задача е под минималната относителна доза. Нужен е преглед; не се запазва автоматично кратката сесия.")
                item["rejected_alternatives"].append({"method_id": "LOCKED_SESSION", "code": "MINIMUM_CAPACITY_DOSE", "reason": item["explanation"]})
                activation_eligible = False
            elif len(preserved)+len(recorded_sessions_by_day[day]) > slots_today or sessions + len(preserved) > session_limit or any(
                not _locked_dose_fits(s, profile, ready) for s in preserved) or not preserved_budget_ok or total > min(remaining, day_dose_available) + .001:
                item.update(status="REVIEW_REQUIRED", explanation="Новите данни изискват преглед на днешните утвърдени задачи.")
                activation_eligible = False
            else:
                forecast_rows = (_add_forecast_session(forecast_rows, day, effective) if actual_by_day[day]
                                 else _with_forecast_day(forecast_rows, day, effective))
                after = recovery_v2.simulate(forecast_rows, configs["zones"], target=day, include_details=False, include_deadlines=False, calculation_cache=recovery_calculation_cache)
                item.update(session=preserved[0], sessions=preserved, status="TRAINING", locked=True,
                            readiness_after={r["zone"]: _round(r["readiness_percent"]) for r in after["current"]},
                            explanation="Втората част от утвърдения двоен праг е проверена след потвърдената сутрешна сесия. Реалният и оставащият товар се отчитат отделно." if locked_day.get("double_threshold_continuation") else "Днешните утвърдени задачи са запазени; адаптират се следващите дни.")
                remaining = max(0., remaining - total)
                for session in preserved:
                    if session["zone"] != "STR":
                        sport_spent[session["sport"]] = sport_spent.get(session["sport"], 0.) + session["total_minutes"]
                sessions += len(preserved)
        elif not automatic_time and (remaining <= .001 or time_headroom(day, result_days) <= .001):
            item.update(status="UNAVAILABLE", time_limit_exhausted=True,
                        explanation="Лимитът за общо време в този период е изчерпан от изпълнените и вече планираните сесии. "
                        "Това е ограничение за време, а не предписание за възстановяване. Провери „Максимум общо време за 7 дни“ и наличните минути по дни в профила.")
            item["rejected_alternatives"].append({"method_id": "TIME_BUDGET", "code": "WEEKLY_TIME_LIMIT_EXHAUSTED",
                                                   "reason": item["explanation"]})
        elif sessions >= session_limit:
            item["explanation"] = f"Достигнат е максимумът от {session_limit} тренировъчни занимания за периода; отчетени са {sessions} изпълнени и планирани сесии."
        else:
            # Protect complete future quality doses before optional work uses
            # their preparation/cascade budget. These are local reservations,
            # never fictitious executed history or additional component goals.
            future_options = []
            future_strength_dates = sorted({
                d for d, _, count in schedule if d > day and count
                and profile.get("strength_enabled")
                and strength_counts(d)[0] < strength_methods.session_limit(controls, accent="STR" in day_goals[d][1])
                and (d not in key_slots or count >= 2)
                and (not last_strength_day or (d-last_strength_day).days >= 2)})
            future_tasks = sorted([(d, "KEY") for d in key_slots if d > day] +
                [(d, "STRENGTH") for d in future_strength_dates] +
                [(d, "LONG") for d in slot_counts if d > day and slot_counts[d]
                 and d.weekday() == preferences.get("long_session_day")])
            for future_day, future_kind in future_tasks:
                future_period, future_taper = _phase(periodization, future_day)
                if (future_taper or any(a.get("date") == future_day.isoformat() for a in existing_in_draft)
                    or any(e["event_type"] in {"MAIN_RACE", "CONTROL_RACE", "TEST", "UNAVAILABLE"}
                    and str(e["start_date"]) <= future_day.isoformat() <= str(e["end_date"]) for e in events)):
                    continue
                forecast_ready = {r["zone"]: r["readiness_percent"] for r in recovery_v2.simulate(
                    forecast_rows, configs["zones"], target=future_day, include_details=False, include_deadlines=False, calculation_cache=recovery_calculation_cache)["current"]}
                options = []
                preferred_future = reservation_methods.get(future_day.isoformat())
                if preferred_future is not None:
                    preferred_future = [r for r in preferred_future if
                        (r.get("is_key_session") if future_kind == "KEY" else
                         r["zone"] == "STR" if future_kind == "STRENGTH" else r["zone"] == "Z1")]
                    if not preferred_future:
                        # A replay with no strength session is evidence of a
                        # missed task, not permission to erase its preparation
                        # reservation while its cycle objective remains open.
                        if future_kind == "STRENGTH":
                            preferred_future = None
                        else:
                            continue
                for future_sport in training_sports:
                    future_candidates = [(s, m) for s, m in candidate_catalog(future_day, first_plannable_slot(future_day))[0] if s == future_sport]
                    future_state = day_goals[future_day][2]
                    if (future_kind == "KEY" and future_day.weekday() in double_days and slot_counts[future_day] >= 2
                        and (not future_state or future_state["kind"] != "RECOVERY")
                        and key_limit-key_sessions >= 2
                        and (profile.get("age_years") or 0) >= 18 and (profile.get("training_experience_years") or 0) >= 1):
                        future_candidates = adaptive_methods.threshold_pairs(future_candidates, (controls or {}).get("double_threshold_components", ["Z3"])) + future_candidates
                    for _, future_method in future_candidates:
                        selected_future = next((r for r in preferred_future or []
                            if r["method_id"] == future_method["id"] and r["sport"] == future_sport), None)
                        if preferred_future is not None and selected_future is None:
                            continue
                        fz = future_method["zone"]
                        if (fz not in ({"Z3", "Z4", "Z5"} if future_kind == "KEY" else {"Z1"} if future_kind == "LONG" else {"STR"})
                            or future_kind == "LONG" and future_method["structure"] not in {"CONTINUOUS", "TWO_REPETITIONS"}
                            or fz == "STR" and future_sport != primary_sport
                            or future_method["purpose"] in {"RECOVERY", "SUPPORTING"}
                            or future_sport not in future_method["sports"] or future_period not in future_method["periods"]
                            or future_method["structure"] not in {"CONTINUOUS", "TWO_REPETITIONS", "THRESHOLD_REPETITIONS", "THRESHOLD_LONG", "THRESHOLD_SHORT", "MODEL_INTERVALS", "METABOLIC_INTERVALS", "LIBRARY_CONTINUOUS", "LIBRARY_INTERVALS", "STRENGTH_CIRCUIT", "STRENGTH_SETS", "COMBINED_METHODS"}):
                            continue
                        fm = _copy_method_budget(future_method)
                        fm["planned_long_session"] = future_kind == "LONG"
                        if fm["structure"] == "MODEL_INTERVALS" and not fm.get("developmental_variant"):
                            exposures = recorded_exposures.get((future_sport, fz), set()) & exposure_history_dates
                            if len(exposures) < 2:
                                fm = adaptive_methods.short_variant(fm)
                        fm["actual_sport"] = future_sport
                        threshold_choice = (controls or {}).get("threshold_method", "AUTO")
                        if not threshold_allowed(fm, threshold_choice):
                            continue
                        if controls and fz != "STR" and not by_sport_minutes.get(future_sport, 0.):
                            continue
                        cap = capacity_for(fm, settings, speed_by_sport[future_sport], context_by_sport[future_sport], today,
                            profile.get("allow_expert_fallback", True), use_model_prior=(controls or {}).get("capacity_policy") == "MODEL_WITH_PRIOR")
                        if cap is None:
                            continue
                        if fm.get("developmental_variant"):
                            cap["dose_capacity_basis"] = "INDEPENDENT_CONTINUOUS_TMAX"
                        if fm.get("paired_method"):
                            other = fm["paired_method"]
                            paired = capacity_for(other, settings, speed_by_sport[future_sport], context_by_sport[future_sport], today,
                                profile.get("allow_expert_fallback", True), use_model_prior=(controls or {}).get("capacity_policy") == "MODEL_WITH_PRIOR")
                            if paired is None or future_period not in other["periods"]:
                                continue
                            cap["dose_capacity_basis"] = "INDEPENDENT_CONTINUOUS_TMAX"
                            cap["paired_capacity"] = {**paired, "zone": other["zone"], "dose_capacity_basis": "INDEPENDENT_CONTINUOUS_TMAX"}
                            fm["min_work_min"] = math.ceil(4*max(fm["single_min_work_min"], other["min_work_min"]*cap["capacity_minutes"]/paired["capacity_minutes"]))/2
                        future_readiness = adaptive_methods.readiness_policy(fm, profile, forecast_ready)
                        cap["readiness_policy"] = future_readiness
                        cap["readiness_dose_factor"] = future_readiness["dose_factor"]
                        if cap["readiness_dose_factor"] <= 0:
                            continue
                        future_state = day_goals[future_day][2]
                        future_purpose = fm["purpose"]
                        background = bool(future_state and future_state.get("background_development")
                                          and mesocycle_focus.growth_weight(future_state, fz) > 0)
                        if future_purpose == "BUILDING" and (reentry_dose_active(periodization, future_day)
                            or fz not in day_goals[future_day][1] and not background
                            or future_state and future_state["kind"] == "RECOVERY"):
                            future_purpose = "MAINTENANCE"
                        if fm.get("double_threshold"):
                            future_purpose = "BUILDING"
                        if selected_future and selected_future.get("purpose"):
                            # The completed replay may promote endurance to
                            # building from remaining need, independently of
                            # the accent list. Reserve that actual role.
                            future_purpose = selected_future["purpose"]
                        future_purpose = adaptive_methods.purpose_for(future_purpose, future_readiness)
                        if future_kind == "KEY" and future_purpose != "BUILDING":
                            continue
                        nominal = _nominal_fraction(fm, future_purpose, profile, "RE_ENTRY" if reentry_dose_active(periodization, future_day) else future_period, cap, controls)
                        minimum = _minimum_work(fm, cap, settings)
                        if minimum is None:
                            continue
                        if minimum > _role_work_limit(cap, cap["capacity_minutes"]*nominal*cap["readiness_dose_factor"]) + .001:
                            continue
                        ceiling = _dose_ceiling(fm, future_purpose, profile, future_period, cap, controls,
                            (progression or {}).get("config", {}).get("max_dose_fraction", .8),
                            reentry=reentry_dose_active(periodization, future_day))
                        maximum = min(fm["max_work_min"], _role_work_limit(cap, cap["capacity_minutes"]*nominal*cap["readiness_dose_factor"]))
                        requested = cap["combined_budgets"][0]["requested_work_minutes"] if fm.get("combined_methods") else maximum
                        full_blocks = _blocks(fm, maximum, cap, settings)
                        full_q, _, _ = _candidate_load(full_blocks, settings, forecast_rows, future_day, zone_tmax_minutes=cap.get("zone_tmax_minutes"))
                        future_segment = component_budgets(forecast_rows, result_days, future_day)[2]
                        if not fm.get("combined_methods") and future_segment and future_segment["components"][fz]["basis"] == "DIRECT_Q" and full_q[fz] > 0:
                            requested = min(maximum, maximum*(future_segment["components"][fz]["remaining"] or 0.)/full_q[fz])
                        # Reserve a dose approaching the remaining objective,
                        # not just the smallest legal exposure. Keep a complete
                        # minimum alternative when the larger task cannot fit.
                        amounts = {minimum}
                        if segment_keys[future_day] == segment_keys[day]:
                            amounts.add(_quantized_work(fm, min(maximum, max(minimum, requested))))
                            amounts.add(_whole_work_at_most(fm, max(minimum, requested), maximum))
                        for future_work in sorted(amounts, reverse=True):
                            future_blocks = _blocks(fm, future_work, cap, settings)
                            if (future_work < minimum or not future_blocks
                                or not _dose_fits(future_blocks, cap, fz, ceiling*cap["readiness_dose_factor"])
                                or sum(b["duration_min"] for b in future_blocks) > available[future_day.weekday()]):
                                continue
                            if controls and fz != "STR":
                                cap_time = observed_session_ceiling(future_sport, future_day,
                                    fm["warmup_min"]+fm["cooldown_min"]+fm.get("recovery_min", 0.),
                                    quality=future_purpose == "BUILDING" and fz in {"Z3", "Z4", "Z5"})
                                if any(sum(b["duration_min"] for b in future_blocks if b.get("session_index", 1) == slot) > cap_time+.001
                                       for slot in {b.get("session_index", 1) for b in future_blocks}):
                                    continue
                            fq, fe, _ = _candidate_load(future_blocks, settings, forecast_rows, future_day, zone_tmax_minutes=cap.get("zone_tmax_minutes"))
                            options.append({"q": fq, "e": fe, "zone": fz, "double": bool(fm.get("double_threshold")),
                                "method_id": fm["id"], "sport": future_sport,
                                "minutes": sum(b["duration_min"] for b in future_blocks),
                                "work": future_work, "nominal_work": cap["capacity_minutes"]*nominal,
                                "dose_usage": _dose_usage(future_blocks, cap, fz), "dose_ceiling": ceiling,
                                "required_components": list(future_readiness["required_components"]),
                                "method":fm, "capacity":cap, "blocks":future_blocks, "purpose":future_purpose,
                                "period":future_period, "reentry":reentry_dose_active(periodization, future_day)})
                if options:
                    # Reserve an executable preferred pair as one bundle. If
                    # its real budgets cannot fit, single sessions stay valid.
                    baseline_budget, baseline_q, baseline_segment = component_budgets(forecast_rows, result_days, future_day)
                    pairs = [option for option in options if option["double"]
                        and reservation_fits(future_day, option["method"], option["blocks"], option["q"], option["e"],
                            baseline_budget, baseline_q, baseline_segment)]
                    future_options.append((future_day, future_kind, sorted(options,
                        key=lambda option: (not any(r["method_id"] == option["method_id"] and r["sport"] == option["sport"]
                                                    for r in preferred_future or []),
                                            not (option in pairs), option["e"]["Z1"], sum(option["e"].values())))))

            def executable_reservation(option, ready):
                method, cap = option["method"], deepcopy(option["capacity"])
                policy = adaptive_methods.readiness_policy(method, profile, ready)
                cap.update(readiness_policy=policy, readiness_dose_factor=policy["dose_factor"])
                stage = ((method.get("method_library") or {}).get("resolution") or {}).get("progression") or {}
                if not policy["allowed"] or stage.get("requires_fresh_building") and policy["maximum_role"] != "BUILDING":
                    return False
                nominal = _nominal_fraction(method, option["purpose"], profile,
                    "RE_ENTRY" if option["reentry"] else option["period"], cap, controls)
                ceiling = _dose_ceiling(method, option["purpose"], profile, option["period"], cap, controls,
                    (progression or {}).get("config", {}).get("max_dose_fraction", .8), reentry=option["reentry"])
                return (option["work"] <= _role_work_limit(cap, cap["capacity_minutes"]*nominal*policy["dose_factor"]) + .001
                    and _dose_fits(option["blocks"], cap, option["zone"], ceiling*policy["dose_factor"]))

            reservation_contract = {}
            @lru_cache(maxsize=256)
            def replay_reserved_future_work(candidate_q, candidate_e, required_dates, candidate_primary, candidate_count, candidate_minutes, return_key_sessions):
                candidate_q = dict(candidate_q) if candidate_q is not None else None
                candidate_e = dict(candidate_e) if candidate_e is not None else None
                reserved_rows = forecast_rows
                reserved_days = list(result_days)
                objective_spent = {z: 0. for z in COMPONENTS}
                if candidate_e is not None:
                    reserved_rows = _add_forecast_session(reserved_rows, day, candidate_e)
                    reserved_days.append({"date": key, "status": "TRAINING", "sessions": [{"direct_equivalent_minutes": candidate_q, "total_minutes":candidate_minutes}]})
                    objective_spent.update(objective_load(candidate_q, candidate_e))
                current_key = candidate_primary in {"Z3", "Z4", "Z5"}
                current_strength = candidate_primary == "STR"
                keys_left = max(0, key_limit-key_sessions-(candidate_count if current_key else 0))
                strengths_left = max(0, strength_limit-strength_sessions-int(current_strength))
                sessions_left = max(0, session_limit-sessions-(candidate_count if candidate_e is not None else 0))
                previous_key = day if current_key else last_key_day
                previous_strength = day if current_strength else last_strength_day
                date_slots = {}
                date_minutes = {}
                minutes_left = float("inf") if automatic_time else max(0., remaining-candidate_minutes)
                selected = set()
                selected_key_sessions = []
                extra_sessions = [(day, candidate_count, current_key)] if candidate_e is not None else []
                extra_strength = [(day, True)] if current_strength else []
                for future_day, future_kind, options in future_options:
                    task = (future_day, future_kind)
                    if required_dates is not None and task not in required_dates:
                        continue
                    if component_governed:
                        future_count, future_keys = session_counts(future_day, extra_sessions)
                        sessions_left, keys_left = max(0, session_limit-future_count), max(0, key_limit-future_keys)
                    # A past session may have left the rolling window by the
                    # reservation date. Apply that date's cycle and rolling
                    # quota, including every hypothetical session once.
                    future_strength_limit = strength_methods.session_limit(controls, accent="STR" in day_goals[future_day][1])
                    strengths_left = max(0, future_strength_limit-strength_counts(future_day, extra_strength)[0])
                    remaining_slots = min(sessions_left, slot_counts[future_day]-date_slots.get(future_day, 0))
                    kind_left = keys_left if future_kind == "KEY" else sessions_left if future_kind == "LONG" else strengths_left
                    previous = previous_key if future_kind == "KEY" else None if future_kind == "LONG" else previous_strength
                    if remaining_slots <= 0 or kind_left <= 0 or previous and (future_day-previous).days < 2:
                        continue
                    options = [option for option in options if (2 if option["double"] else 1) <= min(remaining_slots, kind_left)]
                    # Protect the chosen whole task, not only an occupied date.
                    # Otherwise every added easy dose can silently replace a
                    # Z4+Z5 reservation with a cheaper Z3 task on the same date.
                    contracted = reservation_contract.get(task) if required_dates is not None else None
                    if contracted:
                        options = [o for o in options if (o["method_id"], o["sport"], o["work"]) == contracted]
                    if not options:
                        continue
                    # Slots are opportunities, not a requirement to add another
                    # dose after the current quality has covered that objective.
                    primary_options = [option for option in options if option["zone"] == candidate_primary]
                    if primary_options and future_kind != "LONG" and segment_keys[future_day] == segment_keys[day]:
                        residual = ((period_objectives[candidate_primary]["remaining"] or 0.) - objective_spent[candidate_primary]
                            if period_objectives else budgets[candidate_primary]["deficit_effective"] - objective_spent[candidate_primary])
                        if residual < min(objective_load(option["q"], option["e"])[candidate_primary] for option in primary_options) - .005:
                            options = [o for o in options if o["zone"] != candidate_primary or any(
                                o["q"][z] > .005 for z in {"Z3", "Z4", "Z5", "STR"}-{candidate_primary})]
                            if not options:
                                continue
                    future_budget, future_q, future_segment = component_budgets(reserved_rows, reserved_days, future_day)
                    future_ready = {r["zone"]:r["readiness_percent"] for r in recovery_v2.simulate(
                        reserved_rows, configs["zones"], target=future_day, include_details=False, include_deadlines=False, calculation_cache=recovery_calculation_cache)["current"]}
                    feasible = [option for option in options
                        if (2 if option["double"] else 1) <= min(remaining_slots, kind_left)
                        and option["minutes"] <= min(minutes_left, available[future_day.weekday()]-date_minutes.get(future_day, 0.),
                                                     time_headroom(future_day, reserved_days)) + .001
                        and executable_reservation(option, future_ready)
                        and reservation_fits(future_day, option["method"], option["blocks"], option["q"], option["e"],
                            future_budget, future_q, future_segment)]
                    def future_priority(option):
                        projected = [(z, {**row, "planned": row["planned"] +
                            (option["q"][z] if row["basis"] == "DIRECT_Q" else option["e"][z])})
                            for z, row in future_segment["components"].items()] if future_segment else []
                        quality = [(z, row) for z, row in projected if z in {"Z3", "Z4", "Z5", "STR"}]
                        return (*(-v for v in planning_allocation.gap_score(quality)),
                                *(-v for v in planning_allocation.gap_score(projected)),
                                option["q"]["Z1"], option["minutes"], option["method_id"])
                    chosen = min(feasible, key=future_priority) if feasible else None
                    if chosen is None:
                        if required_dates is not None:
                            return None
                        continue
                    selected.add(task)
                    cost = 2 if chosen["double"] else 1
                    extra_sessions.append((future_day, cost, future_kind == "KEY"))
                    if future_kind in {"KEY", "LONG"}:
                        selected_key_sessions.append({**chosen, "date":future_day,
                            "slots":list(range(date_slots.get(future_day, 0), date_slots.get(future_day, 0)+cost))})
                    sessions_left -= cost
                    date_slots[future_day] = date_slots.get(future_day, 0)+cost
                    date_minutes[future_day] = date_minutes.get(future_day, 0.)+chosen["minutes"]
                    minutes_left -= chosen["minutes"]
                    if future_kind == "KEY":
                        keys_left -= cost
                        previous_key = future_day
                    elif future_kind == "STRENGTH":
                        strengths_left -= 1
                        extra_strength.append((future_day, chosen["purpose"] not in {"MAINTENANCE", "ACTIVATION"}))
                        previous_strength = future_day
                    reserved_rows = _add_forecast_session(reserved_rows, future_day, chosen["e"])
                    reserved_days.append({"date": future_day.isoformat(), "status": "TRAINING",
                        "sessions": [{"direct_equivalent_minutes": chosen["q"], "total_minutes":chosen["minutes"]}]})
                    for z, value in objective_load(chosen["q"], chosen["e"]).items():
                        objective_spent[z] += value
                return selected_key_sessions if return_key_sessions else selected

            def reserved_future_work(candidate_q=None, candidate_e=None, required_dates=None, candidate_primary=None, candidate_count=1, candidate_minutes=0., *, return_key_sessions=False):
                # All causal history, slots, quotas and future options are
                # fixed for this slot. Whole-dose trials often revisit the
                # exact same Q/E bundle; reuse only that identical replay.
                return replay_reserved_future_work(
                    tuple(sorted(candidate_q.items())) if candidate_q is not None else None,
                    tuple(sorted(candidate_e.items())) if candidate_e is not None else None,
                    frozenset(required_dates) if required_dates is not None else None,
                    candidate_primary, candidate_count, candidate_minutes, return_key_sessions)

            reservable_dates = reserved_future_work() if not limited else set()
            reserved_keys = reserved_future_work(return_key_sessions=True) if not limited else []
            reservation_contract.update({(r["date"], "LONG" if r["zone"] == "Z1" else "KEY"):
                (r["method_id"], r["sport"], r["work"]) for r in reserved_keys})
            item["quality_reservations"] = [{"date": r["date"].isoformat(), "method_id": r["method_id"],
                "sport": r["sport"], "purpose": r["purpose"], "minutes": _round(r["minutes"]),
                "direct_q": {z:_round(v) for z,v in r["q"].items()},
                "basis": "OBJECTIVE_DOSE_AND_PREPARATION"} for r in reserved_keys]
            reserved_key_slots = {(r["date"], s) for r in reserved_keys for s in r["slots"]}
            choices = []
            choice_methods = {}
            choice_checks = {}
            choice_headroom = {}
            candidates, shortage = candidate_catalog(day, slot_index)
            allocation_segments = segment_keys if period_objectives is not None else None
            free_slots = [(d, slot) for d, slot, count in schedule if count > 0 and slot >= first_plannable_slot(d)
                and available[d.weekday()] > 0 and not any(ev["event_type"] in {"UNAVAILABLE", "MAIN_RACE", "CONTROL_RACE", "TEST"}
                    and str(ev["start_date"]) <= d.isoformat() <= str(ev["end_date"]) for ev in events)]
            separate_slots = min(max(0, session_limit-sessions), planning_allocation.remaining_slots(
                free_slots, day, slot_index, segment_keys=allocation_segments))
            if (day.weekday() in double_days and slot_index == first_plannable_slot(day) and unexecuted_slot_count(day) >= 2 and not limited and not taper
                    and (not cycle_state or cycle_state["kind"] != "RECOVERY")
                    and sessions + 2 <= session_limit and key_sessions + 2 <= key_limit
                    and (profile.get("age_years") or 0) >= 18 and (profile.get("training_experience_years") or 0) >= 1):
                candidates = adaptive_methods.threshold_pairs(candidates, controls.get("double_threshold_components", ["Z3"])) + candidates
            # Complete independent methods are checked before their optional
            # supplements. This avoids replaying supplements that have a real
            # admissible standalone alternative in this same slot.
            candidates.sort(key=lambda candidate: bool(candidate[1].get("mixed_component")))
            standalone_zones = set()
            slot_allocation = allocation
            reserved_support = {z:sum(r["q"][z] for r in reserved_keys
                if segment_keys[r["date"]] == segment_keys[day]
                and not any(b["kind"] == "WORK" and b["zone"] == z for b in r["blocks"]))
                for z in ("Z1", "Z2")}
            for sport, method in candidates:
                allocation = slot_allocation
                speed, context = speed_by_sport[sport], context_by_sport[sport]
                if method["structure"] == "MODEL_INTERVALS" and not method.get("developmental_variant"):
                    exposures = recorded_exposures.get((sport, method["zone"]), set()) & exposure_history_dates
                    if len(exposures) < 2:
                        method = adaptive_methods.short_variant(method)
                original_method = method
                method = _copy_method_budget(method)
                method["planned_long_session"] = day.weekday() == preferences.get("long_session_day")
                method["actual_sport"] = sport
                race_minutes = profile.get("race_duration_min")
                if race_minutes and method["zone"] == "Z3" and method["purpose"] != "SUPPORTING" and period in {"SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"}:
                    blend = min(1., max(0., (race_minutes - 50.) / 20.))
                    method["position"] = .85 - .30 * blend
                if sport not in method["sports"]:
                    continue
                z = method["zone"]
                if allocation is not None and period_objectives and z in reserved_support and period_objectives[z]["basis"] == "DIRECT_Q":
                    allocation = {**allocation, z:max(0., allocation[z]-reserved_support[z])}
                supporting = method["purpose"] == "SUPPORTING"
                mixed = bool(method.get("mixed_component"))
                if mixed and allocation is not None and period_objectives is not None:
                    # Supplements can use residual component work on an easy
                    # day; the key-session calendar must not zero this quota.
                    allocation = {**allocation, z: period_objectives[z]["remaining"] or 0.}
                readiness_rule = adaptive_methods.readiness_policy(method, profile, ready)
                # A maintenance quality dose can prepare the next building day.
                # Key spacing belongs to the resolved role, not the template label.
                initial_purpose = method["purpose"]
                background = bool(cycle_state and cycle_state.get("background_development") and mesocycle_focus.growth_weight(cycle_state, z) > 0)
                if initial_purpose == "BUILDING" and (reentry_dose_active(periodization, day) or z not in selected_accents and not background or taper or cycle_state and cycle_state["kind"] == "RECOVERY"):
                    initial_purpose = "MAINTENANCE"
                if method.get("double_threshold"):
                    initial_purpose = "BUILDING"
                initial_purpose = adaptive_methods.purpose_for(initial_purpose, readiness_rule)
                post_target_maintenance = bool(continuity_allowed and z in maintenance_reserve["references"]
                    and planning_continuity.eligible(period_objectives[z], planned_easy=z in {"Z1", "Z2"})
                    and not mixed and not method.get("combined_methods") and not method.get("double_threshold")
                    and not method.get("neuromuscular_profile")
                    and method["structure"] not in method_registry.COMBINED_STRUCTURES)
                if post_target_maintenance and z in {"Z1", "Z2"}:
                    # Additional easy work serves an otherwise empty requested
                    # day, not every optional second session.
                    post_target_maintenance = not day_spent and not actual_day_minutes and _absolute_minimum(method)
                taper_simple = (taper_continuity_allowed and not mixed and not method.get("combined_methods")
                    and not method.get("double_threshold") and not method.get("neuromuscular_profile")
                    and method["structure"] not in method_registry.COMBINED_STRUCTURES)
                taper_light = bool(taper_simple and z in {"Z1", "Z2"}
                    and initial_purpose in {"MAINTENANCE", "RECOVERY"})
                if taper_light and allocation is not None:
                    taper_quota = planning_continuity.taper_limits(period_objectives, budgets, q_remaining,
                        zone=z, light=True, manual=manual_components)
                    allocation = {**allocation, z: max(allocation[z], taper_quota["objective"][z] or 0.)}
                if post_target_maintenance:
                    initial_purpose = "RECOVERY" if method["purpose"] == "RECOVERY" else "MAINTENANCE"
                    allocation = {**allocation, z: max(0., period_objectives[z]["target"]
                        + maintenance_reserve["q"][z] - period_objectives[z]["actual"] - period_objectives[z]["planned"])}
                is_key = training_sessions.role_is_key(z, initial_purpose, post_target=post_target_maintenance)
                if not is_key and z in {"Z3", "Z4", "Z5"} and allocation is not None and period_objectives is not None:
                    allocation = {**allocation, z: period_objectives[z]["remaining"] or 0.}
                is_strength = z == "STR" or method["structure"] == "AEROBIC_STRENGTH" or any(p["zone"] == "STR" for p in method.get("combined_methods", []))
                is_threshold = not mixed and (z == "Z3" or method.get("interval_profile", {}).get("goal") == "THRESHOLD")
                threshold_choice = (controls or {}).get("threshold_method", "AUTO")
                race_development_zones = (([cycle_state["race_component"]] if period == "COMPETITION" else cycle_state["mesocycle_accents"])
                                          if cycle_state and cycle_state.get("race_component") else [])
                rejection = None
                progressions = [((part.get("method_library") or {}).get("resolution") or {}).get("progression") or {}
                                for part in [method, *method.get("combined_methods", [])]]
                if any(p.get("requires_fresh_building") for p in progressions) and readiness_rule["maximum_role"] != "BUILDING":
                    rejection = ("PROGRESSION_RECOVERY_REQUIRED", "Удължаването изисква допустима готовност и потвърдено изпълнение; използвай последния изпълним вариант.")
                elif (not method.get("mixed_component") and method["structure"] in method_registry.COMBINED_STRUCTURES) and not shortage["shortage"]:
                    rejection = ("SEPARATE_SESSIONS_AVAILABLE", "Комбинацията не е необходима: няма доказан недостиг на места за отделните основни методи.")
                elif mixed and not shortage["shortage"] and z in standalone_zones:
                    rejection = ("SEPARATE_SESSIONS_AVAILABLE", "Има допустим отделен основен метод за същия компонент в тази сесия.")
                elif period not in method["periods"]:
                    rejection = ("PERIOD_NOT_SUPPORTED", "Методът не е включен в този период.")
                elif (z == "Z1" and not mixed and (profile.get("_allocation_policy") or {}).get("spread_recovery")
                    and any(s["zone"] == "Z1" for prior in prior_today for s in planning_schedule.day_sessions(prior))
                    and any(d > day and segment_keys[d] == segment_keys[day] for d,s in free_slots)):
                    rejection = ("EASY_WORK_DISTRIBUTED", "Леката работа е разпределена и в следващите тренировъчни дни; днешната основна задача в Z1 вече е зададена.")
                elif method.get("neuromuscular_profile") and (slot_index != 0 or day.weekday() not in method["neuromuscular_profile"]["days"]):
                    rejection = ("NMS_DAY_PREFERENCE", "Кратките ускорения са включени само в избраните дни и в първата подходяща сесия.")
                elif cycle_state and controls["accent_mode"] == "AUTO" and not cycle_state["explicit"] and period in {"PRECOMPETITION", "COMPETITION"} and method["purpose"] == "BUILDING" and not is_strength and z not in race_development_zones:
                    rejection = ("RACE_COMPONENT_PRIORITY", "Развиващата специална работа следва състезателните акценти за периода; останалите компоненти получават поддържане.")
                elif supporting and taper:
                    rejection = ("SUPPORT_DURING_TAPER", "Тейпърът не се запълва с допълваща работа; поддържащата доза се избира от основния метод.")
                elif limited and method["purpose"] != "RECOVERY":
                    rejection = ("INSUFFICIENT_HISTORY", "При ограничена история са разрешени само леки ограничени предложения.")
                elif controls and not is_strength and not by_sport_minutes.get(sport, 0.) and sport not in return_sports and method["purpose"] != "RECOVERY":
                    rejection = ("NO_ACTUAL_MODE_EXPOSURE", "За това средство няма скорошна история. Допуска се само ограничено леко въвеждане, независимо от опита в други спортове.")
                elif not threshold_allowed(method, threshold_choice):
                    rejection = ("THRESHOLD_METHOD_PREFERENCE", "Избран е друг вид прагова тренировка в профила.")
                elif is_strength and (strength_sessions >= strength_limit or (last_strength_day and (day-last_strength_day).days < 2)):
                    rejection = ("STRENGTH_SESSION_LIMIT", "Достигнат е силовият лимит или липсва достатъчно разстояние между силовите сесии.")
                elif is_key and (key_sessions >= key_limit or (last_key_day and (day - last_key_day).days < 2)):
                    rejection = ("KEY_SESSION_LIMIT", "Достигнат е лимитът или липсва достатъчно разстояние между ключови сесии.")
                elif is_key and day not in key_slots and not post_target_maintenance:
                    probe = capacity_for(method, settings, speed, context, today, profile.get("allow_expert_fallback", True),
                                         use_model_prior=(controls or {}).get("capacity_policy") == "MODEL_WITH_PRIOR")
                    rejection = (("CAPACITY_UNAVAILABLE", "Няма допустима индивидуална крива за този метод и средство.")
                                 if probe is None else ("KEY_SLOT_RESERVED", "Ключовите сесии са разположени първи в подходящите дни; този ден остава за лека работа или почивка."))
                elif readiness_rule["dose_factor"] <= 0:
                    rejection = (readiness_rule["reason"], "Под 50% Recovery не се задава целева работа над Z1. Ролята следва целта; развиващата доза използва половината дефицит, поддържащата е 60% от метода без Recovery множител.")
                elif not limited and (period_objectives[z]["target"] if period_objectives else budgets[z]["target_weekly_effective"]) <= 0:
                    rejection = ("NO_COMPONENT_TARGET", "Липсва установена компонентна база. Въвеждането на нов развиващ товар изисква отделна треньорска цел.")
                elif allocation is not None and allocation[z] <= .001:
                    rejection = ("WEEKLY_NEED_COVERED", "Не остава разпределен товар за този компонент в седмицата. Свободната сесия не се запълва задължително.")
                if rejection:
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": rejection[0], "reason": rejection[1]})
                    continue
                learned_intensity = _learned_method(method, profile, progression, day, period, cycle_state,
                                                     taper=taper, limited=limited)
                evidence = capacity_for(method, settings, speed, context, today, profile.get("allow_expert_fallback", True), use_model_prior=(controls or {}).get("capacity_policy") == "MODEL_WITH_PRIOR")
                if evidence is None:
                    reason = "Няма допустима оценка на капацитета по индивидуалната крива за този метод и средство."
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": "CAPACITY_UNAVAILABLE", "reason": reason})
                    continue
                evidence["readiness_policy"] = readiness_rule
                evidence["readiness_dose_factor"] = readiness_rule["dose_factor"]
                if method.get("developmental_variant") or mixed:
                    evidence["dose_capacity_basis"] = "INDEPENDENT_CONTINUOUS_TMAX"
                if learned_intensity:
                    evidence["individual_learning"] = learned_intensity
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
                    # Each session uses its own continuous Tmax, without an interval multiplier.
                    evidence["dose_capacity_basis"] = "INDEPENDENT_CONTINUOUS_TMAX"
                    evidence["paired_capacity"] = {**paired, "zone":other["zone"], "dose_capacity_basis": "INDEPENDENT_CONTINUOUS_TMAX"}
                    primary_capacity = evidence["capacity_minutes"]
                    paired_capacity = paired["capacity_minutes"]
                    method["min_work_min"] = math.ceil(4*max(method["single_min_work_min"], other["min_work_min"]*primary_capacity/paired_capacity))/2
                if mixed or method["structure"] in {"ALTERNATING", "CRUISE_ALTERNATING", "AEROBIC_STRENGTH", "AEROBIC_SUPPORT"}:
                    secondary = capacity_for({**method, "zone": "Z1", "position": .35, "structure": "CONTINUOUS"},
                                             settings, speed, context, today, profile.get("allow_expert_fallback", True), use_model_prior=(controls or {}).get("capacity_policy") == "MODEL_WITH_PRIOR")
                    if secondary is None:
                        continue
                    evidence["secondary_capacity"] = secondary
                    if mixed:
                        # A common scale shrinks the complete combination when Q/E or time is scarce.
                        evidence["mixed_component"] = True
                        evidence["easy_to_primary_ratio"] = 1.  # Resolved from both role budgets below.
                        if period_objectives is not None:
                            # Compare executable quality cores first. The easy
                            # finish is optional and receives only surplus after
                            # other complete qualities have preparation space.
                            evidence["easy_work_cap_minutes"] = 0.
                purpose = method["purpose"]
                # Easy endurance can need a full building-method dose to carry
                # the weekly load, even while another quality is the accent.
                # This uses the existing coach fraction, never a new multiplier.
                endurance_allocation = False
                if allocation and z in {"Z1", "Z2"} and purpose == "BUILDING" and not taper:
                    maintenance_work = min((method["method_library"]["resolution"]["standard_work_seconds"] / 60 * .6
                                            if method.get("method_library") else evidence["capacity_minutes"] *
                                            (.39 if method["structure"] == "CONTINUOUS" else continuous_maintenance_fraction(profile))),
                                           method["max_work_min"])
                    maintenance_blocks = _blocks(method, maintenance_work, evidence, settings)
                    if maintenance_blocks:
                        maintenance_q, maintenance_load, _ = candidate_load(maintenance_blocks, capacity=evidence)
                        endurance_allocation = allocation[z] > objective_load(maintenance_q, maintenance_load)[z] + .5
                background_development = bool(cycle_state and cycle_state.get("background_development") and mesocycle_focus.growth_weight(cycle_state, z) > 0)
                if purpose == "BUILDING" and (reentry_dose_active(periodization, day) or z not in selected_accents and not endurance_allocation and not background_development or taper or cycle_state and cycle_state["kind"] == "RECOVERY"):
                    purpose = "MAINTENANCE"
                if method.get("double_threshold"):
                    purpose = "BUILDING"
                if post_target_maintenance:
                    purpose = "RECOVERY" if method["purpose"] == "RECOVERY" else "MAINTENANCE"
                purpose = adaptive_methods.purpose_for(purpose, readiness_rule)
                if is_strength and strength_building >= 2:
                    purpose = "MAINTENANCE"
                if taper and method.get("strength_profile", {}).get("key") == "POWER":
                    purpose = "ACTIVATION"
                fraction = _nominal_fraction(method, purpose, profile, "RE_ENTRY" if reentry_dose_active(periodization, day) else period, evidence, controls)
                requested = evidence["capacity_minutes"] * fraction
                if method.get("combined_methods"):
                    requested = evidence["combined_budgets"][0]["requested_work_minutes"]/max(1e-9, readiness_rule["dose_factor"])
                if mixed:
                    evidence["easy_to_primary_ratio"] = (evidence["secondary_capacity"]["capacity_minutes"]
                        * continuous_maintenance_fraction(profile) / max(.001, requested*readiness_rule["dose_factor"]))
                    evidence["mixed_primary_max_fraction"] = fraction * readiness_rule["dose_factor"]
                dose_ceiling = (progression or {}).get("config", {}).get("max_dose_fraction", .8)
                if method["structure"] in {"ALTERNATING", "CRUISE_ALTERNATING", "AEROBIC_STRENGTH"}:
                    secondary = evidence["secondary_capacity"]
                    if method["structure"] in {"ALTERNATING", "CRUISE_ALTERNATING"}:
                        requested = min(requested, secondary["capacity_minutes"] * fraction)
                    else:
                        requested = min(requested, secondary["capacity_minutes"] * continuous_maintenance_fraction(profile) * .5 + 3)
                if method["structure"] == "THRESHOLD_HIGH":
                    fraction *= .5
                    requested *= .5
                    if not _prepare_threshold_high_budget(method, evidence, profile, settings, speed, context, today,
                            primary_fraction=fraction, readiness_factor=readiness_rule["dose_factor"]):
                        continue
                base_fraction = fraction
                fraction *= readiness_rule["dose_factor"]
                requested *= readiness_rule["dose_factor"]
                minimum_work = _minimum_work(method, evidence, settings)
                if minimum_work is None:
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": "MINIMUM_CAPACITY_DOSE",
                        "reason": f"Методният максимум не допуска минималните {method.get('minimum_fraction', MIN_AEROBIC_DOSE_FRACTION)*readiness_rule['dose_factor']*100:g}% от капацитета за този вариант."})
                    continue
                method["absolute_min_work_min"] = method["min_work_min"]
                method["min_work_min"] = minimum_work
                evidence.update(min_dose_fraction=method.get("minimum_fraction", MIN_AEROBIC_DOSE_FRACTION)*readiness_rule["dose_factor"] if z != "STR" and not _absolute_minimum(method) and not method.get("combined_methods") else None,
                                minimum_primary_work_minutes=minimum_work*_primary_work_share(method), minimum_structure_work_minutes=minimum_work,
                                minimum_dose_scope="METHOD_ABSOLUTE" if _absolute_minimum(method) else "EACH_THRESHOLD_SESSION" if method.get("double_threshold") else "SUPPLEMENTARY_COMPONENT" if mixed else "SESSION")
                event_specificity = training_targets.specificity(method, profile.get("race_duration_min"), period)
                evidence["specificity"] = event_specificity
                overhead = (method["warmup_min"] + method["cooldown_min"] + method.get("recovery_min", 0.)) * (2 if method.get("double_threshold") else 1)
                if method.get("paired_method"):
                    other = method["paired_method"]
                    overhead = sum(m["warmup_min"] + m["cooldown_min"] + m.get("recovery_min",0.) for m in (method,other))
                limits = [{"code": "METHOD_WORK_CAP", "limit_minutes": method["max_work_min"]},
                          {"code": "TECHNICAL_SESSION_CEILING" if availability_mode == "AUTO_HISTORY" else "DAILY_AVAILABLE_WORK", "limit_minutes": max(0., day_available - overhead)}]
                allocation_policy = profile.get("_allocation_policy") or {}
                search_cap = allocation_policy.get("easy_caps", {}).get(day.isoformat())
                if search_cap is not None and z in allocation_policy.get("easy_cap_zones", ["Z1", "Z2"]):
                    limits.append({"code": "ALTERNATIVE_EASY_ALLOCATION", "limit_minutes": search_cap})
                if mixed or method.get("developmental_variant"):
                    limits.append({"code": "SUPPLEMENTARY_DOSE_CAP" if mixed else "DEVELOPMENTAL_DOSE_CAP",
                                   "limit_minutes": evidence["capacity_minutes"]*fraction})
                if not automatic_time:
                    limits.append({"code": "REMAINING_WEEKLY_WORK", "limit_minutes": max(0., remaining - overhead)})
                session_ceiling = float("inf")
                if controls and not is_strength:
                    # The old cumulative weekly ceiling incorrectly made the
                    # athlete's total programme equal the history of one means.
                    # Keep the observed session exposure separate from total
                    # volume and from the capacity estimate for this exact means.
                    session_ceiling = observed_session_ceiling(sport, day, overhead,
                        quality=purpose == "BUILDING" and z in {"Z3", "Z4", "Z5"} and not taper
                            and not reentry_dose_active(periodization, day))
                    limits.append({"code": "ACTUAL_SPORT_SESSION_EXPOSURE", "limit_minutes": max(0., session_ceiling*(2 if method.get("double_threshold") else 1)-overhead)})
                if event_specificity["work_cap_min"] is not None:
                    # Specificity limits each session, not the entire double day.
                    specificity_cap = event_specificity["work_cap_min"]
                    if method.get("double_threshold"):
                        other_cap = training_targets.specificity(method["paired_method"], profile.get("race_duration_min"), period)["work_cap_min"]
                        specificity_cap = 2*min(specificity_cap, other_cap*evidence["capacity_minutes"]/evidence["paired_capacity"]["capacity_minutes"])
                    limits.append({"code": "RACE_DURATION_WORK_CAP", "limit_minutes": specificity_cap})
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
                if purpose == "RECOVERY" or limited or post_target_maintenance and z in {"Z1", "Z2"}:
                    limits.append({"code": "LOW_ABSOLUTE_RECOVERY_CAP", "limit_minutes": profile.get("recovery_session_cap_min", 30.)})
                if reentry_dose_active(periodization, day) and not _interval_budget_profile(method) and not method.get("strength_profile"):
                    reentry_fraction = .39 if method["structure"] == "CONTINUOUS" and z in {"Z1", "Z2"} else continuous_maintenance_fraction(profile)
                    limits.append({"code": "REENTRY_DOSE_CAP", "limit_minutes": evidence["capacity_minutes"] * reentry_fraction * readiness_rule["dose_factor"]})
                if taper and (weekly_minutes > 0 or not return_supported):
                    # Taper is an intentional reduction, never a 7/40 deficit
                    # to refill. Its volume cap applies even mid-draft.
                    limits.append({"code": "TAPER_DAILY_WORK_CAP", "limit_minutes": max(0., taper_day_headroom - overhead),
                        "already_used_minutes": _round(day_spent + actual_day_minutes), "scope": "ALL_SESSIONS_SAME_DAY"})
                work = _whole_work_at_most(method, requested, min(r["limit_minutes"] for r in limits))
                if work < method["min_work_min"]:
                    time_limited = not automatic_time and remaining < overhead + method["min_work_min"]
                    exposure_limited = any(r["code"] == "ACTUAL_SPORT_SESSION_EXPOSURE" and r["limit_minutes"] < method["min_work_min"] for r in limits)
                    item["rejected_alternatives"].append({"method_id": method["id"],
                        "code": "INSUFFICIENT_TIME_BUDGET" if time_limited else "ACTUAL_SPORT_SESSION_EXPOSURE" if exposure_limited else "INSUFFICIENT_DOSE_BUDGET",
                        "reason": (f"Остават {max(0., remaining):g} минути от лимита за периода; методът изисква поне {overhead + method['min_work_min']:g} минути с подготвителните части."
                                   if time_limited else f"Минималният вариант изисква поне {overhead + method['min_work_min']:g} минути; историческата граница за една сесия с това средство е {session_ceiling:.1f} минути."
                                   if exposure_limited else "Оставащият бюджет е под минималната работна доза на метода.")})
                    continue
                blocks = _blocks(method, work, evidence, settings)
                # Interval roles own 100%/60% of the expert method budget; joint
                # canonical Q/E constraints below account for adjacent effects.
                max_usage = _dose_ceiling(method, purpose, profile, period, evidence, controls,
                    dose_ceiling, reentry=reentry_dose_active(periodization, day))
                evidence["base_max_dose_fraction"] = max_usage
                max_usage *= readiness_rule["dose_factor"]
                evidence["maximum_dose_fraction"] = max_usage
                while blocks and not _dose_fits(blocks, evidence, z, max_usage, tolerance=1e-6) and work >= method["min_work_min"]:
                    work = _previous_work(method, work)
                    blocks = _blocks(method, work, evidence, settings) if work >= method["min_work_min"] else []
                evidence.update(max_dose_fraction=max_usage*evidence.get("whole_dose_rounding_factor", 1.),
                    dose_capacity_basis=evidence.get("dose_capacity_basis", "WHOLE_STRUCTURE_WORK_CAPACITY"))
                limits.append({"code": "WHOLE_STRUCTURE_DOSE_FRACTION", "limit_minutes": work})
                # Whole repetitions/circuits, active rests and transitions all
                # have to fit. Lower work never authorizes a longer rest.
                def fits_time(parts):
                    return (sum(b["duration_min"] for b in parts) <= min(day_dose_available, remaining) + .001
                            and all(sum(b["duration_min"] for b in parts if b.get("session_index", 1) == slot) <= session_ceiling + .001
                                    for slot in {b.get("session_index", 1) for b in parts}))
                while work >= method["min_work_min"] and blocks and not fits_time(blocks):
                    work = _previous_work(method, work)
                    blocks = _blocks(method, work, evidence, settings)
                if not blocks or work < method["min_work_min"]:
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": "STRUCTURE_DOES_NOT_FIT", "reason": "Цели повторения, паузи и общата доза не се побират едновременно."})
                    continue
                direct, effective, technical = candidate_load(blocks, capacity=evidence)
                # Balance only the complete doses needed, not all free slots.
                # This avoids taking a maximum now and stranding a tiny remainder.
                stranded_fraction = 0.
                if allocation and (allocation_policy.get("balance_complete_doses", True)
                    or z == "Z1" and purpose == "RECOVERY" and allocation_policy.get("spread_recovery")) and not method.get("double_threshold") and not method.get("combined_methods") and not mixed:
                    minimum_blocks = _blocks(method, method["min_work_min"], evidence, settings)
                    minimum_q, minimum_e, _ = candidate_load(minimum_blocks, capacity=evidence)
                    minimum_load = objective_load(minimum_q, minimum_e)[z]
                    maximum_load = objective_load(direct, effective)[z]
                    remaining_slots = (planning_allocation.remaining_slots(opportunities[z], day, slot_index,
                        segment_keys=allocation_segments) if is_key else separate_slots)
                    share = planning_allocation.dose_share(allocation[z], minimum_load, maximum_load, remaining_slots)
                    if z == "Z1" and purpose == "RECOVERY" and allocation_policy.get("spread_recovery"):
                        future_days = len({d for d,s in free_slots if d >= day
                            and (allocation_segments is None or allocation_segments[d] == allocation_segments[day])})
                        count = min(future_days, math.floor((allocation[z]+.001)/max(.001, minimum_load)))
                        if count > 1:
                            share = min(maximum_load, allocation[z]/count)
                    if share is not None and share < maximum_load:
                        lo, hi = method["min_work_min"], work
                        for _ in range(16):
                            midpoint = (lo+hi)/2
                            trial = _blocks(method, midpoint, evidence, settings)
                            trial_q, trial_e, _ = candidate_load(trial, capacity=evidence)
                            if trial and objective_load(trial_q, trial_e)[z] <= share + .001:
                                lo = midpoint
                            else:
                                hi = midpoint
                        work = max(method["min_work_min"], _quantized_work(method, lo))
                        blocks = _blocks(method, work, evidence, settings)
                        direct, effective, technical = candidate_load(blocks, capacity=evidence)
                        limits.append({"code": "COMPLETE_DOSE_ALLOCATION", "limit_minutes": _round(work)})
                    elif share is None and maximum_load < allocation[z]:
                        remainder = allocation[z] % maximum_load if maximum_load else 0.
                        if 1. < remainder < minimum_load:
                            stranded_fraction = remainder / max(1., allocation[z])
                if not limited:
                    headroom = (planning_continuity.limits(period_objectives, budgets, q_remaining, maintenance_reserve,
                        zone=z, maintenance=post_target_maintenance,
                        preparation=bool(continuity_allowed and "Z1" not in manual_components
                            and period_objectives["Z1"]["basis"] == "DIRECT_Q"
                            and z != "Z1"
                            and not any(b["kind"] == "WORK" and b["zone"] == "Z1" for b in blocks)))
                        if period_objectives else None)
                    if taper_continuity_allowed:
                        headroom = planning_continuity.taper_limits(period_objectives, budgets, q_remaining,
                            zone=z, light=taper_light,
                            preparation=taper_simple and not any(b["kind"] == "WORK" and b["zone"] == "Z1" for b in blocks),
                            manual=manual_components)
                    def fits_component_budget(candidate, candidate_q, *, allowance=headroom):
                        if allowance is not None:
                            return planning_continuity.fits(candidate_q, candidate, allowance, period_objectives)
                        return (all(candidate[z] <= budgets[z]["deficit_effective"] + .001 for z in COMPONENTS)
                                and all(candidate_q[z] <= available_q + .001 for z, available_q in q_remaining.items())
                                and (period_objectives is None or all(
                                    planning_allocation.prescription_headroom(period_objectives[z]) is not None
                                    and value <= planning_allocation.prescription_headroom(period_objectives[z]) + .005
                                    for z, value in objective_load(candidate_q, candidate).items())))
                    if not fits_component_budget(effective, direct):
                        minimum_blocks = _blocks(method, method["min_work_min"], evidence, settings)
                        minimum_q, minimum_load, _ = candidate_load(minimum_blocks, capacity=evidence)
                        if not minimum_blocks or not fits_component_budget(minimum_load, minimum_q):
                            period_blockers = [c for c, value in objective_load(minimum_q, minimum_load).items()
                                if period_objectives and (planning_allocation.prescription_headroom(period_objectives[c]) is None or value > planning_allocation.prescription_headroom(period_objectives[c]) + .005)]
                            if period_blockers and all(minimum_load[c] <= budgets[c]["deficit_effective"] + .001 for c in COMPONENTS) and all(minimum_q[c] <= v + .001 for c, v in q_remaining.items()):
                                item["rejected_alternatives"].append({"method_id": method["id"], "code": "PERIOD_COMPONENT_REMAINDER",
                                    "reason": "Остатъкът от целта за периода не допуска минималната цяла доза: " + "; ".join(
                                        f"{c}: нужни {objective_load(minimum_q, minimum_load)[c]:.1f}, остават {period_objectives[c]['remaining'] or 0.:.1f} приравнени мин" for c in period_blockers),
                                    "blocking_components": period_blockers})
                                continue
                            checked_q = headroom["q"] if headroom else q_remaining
                            checked_budgets = ({c: {**b, "deficit_effective": headroom["e"][c]}
                                for c,b in budgets.items()} if headroom else budgets)
                            q_blockers = [z for z,v in checked_q.items() if minimum_q[z] > v + .001]
                            item["rejected_alternatives"].append({"method_id": method["id"], "code": "DIRECT_Q_PROGRESSION_BUDGET",
                                "reason": "Минималният вариант надвишава оставащия приравнен обем Q: " + "; ".join(f"{z}: нужни {minimum_q[z]:.1f}, остават {checked_q[z]:.1f} мин" for z in q_blockers),
                                "blocking_components": q_blockers} if q_blockers else _budget_rejection(method["id"], minimum_load, checked_budgets, profile))
                            continue
                        lo, hi = method["min_work_min"], work
                        for _ in range(24):
                            midpoint = (lo + hi) / 2
                            trial_blocks = _blocks(method, midpoint, evidence, settings)
                            trial_q, trial_effective, _ = candidate_load(trial_blocks, capacity=evidence)
                            if trial_blocks and fits_component_budget(trial_effective, trial_q):
                                lo = midpoint
                            else:
                                hi = midpoint
                        work = _quantized_work(method, lo)
                        limits.append({"code": "ROLLING_Q_AND_7_40_BUDGET", "limit_minutes": _round(work)})
                        if work < method["min_work_min"]:
                            item["rejected_alternatives"].append(_budget_rejection(method["id"], effective, budgets, profile))
                            continue
                        blocks = _blocks(method, work, evidence, settings)
                        if not blocks:
                            item["rejected_alternatives"].append({"method_id": method["id"], "code": "MINIMUM_STRUCTURE_NOT_MET", "reason": "Бюджетът не допуска минималната цяла структура."})
                            continue
                        direct, effective, technical = candidate_load(blocks, capacity=evidence)
                dose_target_q = (max(0., (period_objectives[z]["remaining"] or 0.)-reserved_support.get(z, 0.))
                                 if period_objectives else 0.)
                if (period_objectives and period_objectives[z]["basis"] == "DIRECT_Q"
                    and not post_target_maintenance and (period_objectives[z]["remaining"] or 0.) > .001
                    and direct[z] > dose_target_q+.001):
                    # The 110% band permits whole repetitions on either side
                    # of 100%; it is not a reason to fill every component to 110%.
                    target_q = dose_target_q
                    lo, hi = method["min_work_min"], work
                    for _ in range(22):
                        mid = (lo+hi)/2
                        trial = _blocks(method, mid, evidence, settings)
                        tq, _, _ = candidate_load(trial, capacity=evidence)
                        if tq[z] <= target_q:
                            lo = mid
                        else:
                            hi = mid
                    variants = []
                    for w in {method["min_work_min"], lo, min(work, hi+1e-6)}:
                        w = _quantized_work(method, w)
                        parts = _blocks(method, w, evidence, settings)
                        if not parts:
                            continue
                        cq, ce, ct = candidate_load(parts, capacity=evidence)
                        if (fits_time(parts) and _dose_fits(parts, evidence, z, max_usage, tolerance=1e-6)
                            and fits_component_budget(ce, cq)):
                            variants.append((abs(cq[z]-target_q), cq[z] > target_q, w, parts, cq, ce, ct))
                    if variants:
                        distance, _, work, blocks, direct, effective, technical = min(variants, key=lambda v: (not v[1], v[0], v[2]))
                        if distance >= target_q-.001:
                            item["rejected_alternatives"].append({"method_id": method["id"],
                                "code": "WHOLE_DOSE_WOULD_WORSEN_Q_GAP",
                                "reason": "Минималната цяла доза отдалечава компонента от 100% повече от оставащия недостиг."})
                            continue
                        limits.append({"code": "UPPER_WHOLE_Q_DOSE_IF_ADMISSIBLE", "limit_minutes": _round(work)})
                after_rows = _add_forecast_session(forecast_rows, day, effective)
                def recovery_conflict(candidate_rows, candidate_q, candidate_e, candidate_minutes, *, protect_future=True,
                                      candidate_primary=z if is_key or is_strength else None,
                                      candidate_count=2 if method.get("double_threshold") else 1,
                                      first_quality=bool(period_objectives and z in {"Z3", "Z4", "Z5"}
                                          and planning_allocation.quality_priority(z, period_objectives))):
                    protected_dates = reservable_dates
                    if first_quality and period_objectives and not planning_allocation.quality_priority("STR", period_objectives):
                        # A speculative repeat of an already represented
                        # strength component cannot exclude the first legal
                        # exposure of another quality. Keep future key tasks
                        # protected; every actual dose still passes Q/E,
                        # readiness, spacing and session-quota checks.
                        protected_dates = {task for task in protected_dates if task[1] != "STRENGTH"}
                    if protect_future and protected_dates and reserved_future_work(candidate_q, candidate_e, protected_dates,
                        candidate_primary=candidate_primary,
                        candidate_count=candidate_count, candidate_minutes=candidate_minutes) is None:
                        return "RESERVE_FUTURE_QUALITY_BUDGET", "Дозата използва необходимия Q/7–40 бюджет за бъдеща ключова или силова задача, включително загрявката и товара от по-високите и съседните зони. В текущия цикъл се защитава дозата според оставащата цел."
                    return None
                conflict = recovery_conflict(after_rows, direct, effective, sum(b["duration_min"] for b in blocks))
                if conflict:
                    # A large optional dose failing a future readiness check
                    # does not mean its minimum complete variant must fail.
                    minimum_blocks = _blocks(method, method["min_work_min"], evidence, settings)
                    minimum_q, minimum_load, minimum_technical = candidate_load(minimum_blocks, capacity=evidence)
                    minimum_minutes = sum(b["duration_min"] for b in minimum_blocks)
                    if minimum_blocks and not recovery_conflict(_add_forecast_session(forecast_rows, day, minimum_load), minimum_q, minimum_load, minimum_minutes):
                        lo, hi = method["min_work_min"], work
                        while hi-lo > .25:
                            midpoint = (lo+hi)/2
                            trial = _blocks(method, midpoint, evidence, settings)
                            trial_q, trial_load, _ = candidate_load(trial, capacity=evidence)
                            if trial and not recovery_conflict(_add_forecast_session(forecast_rows, day, trial_load), trial_q, trial_load, sum(b["duration_min"] for b in trial)):
                                lo = midpoint
                            else:
                                hi = midpoint
                        work = max(method["min_work_min"], _quantized_work(method, lo))
                        blocks = _blocks(method, work, evidence, settings)
                        direct, effective, technical = candidate_load(blocks, capacity=evidence)
                        after_rows = _add_forecast_session(forecast_rows, day, effective)
                        conflict = recovery_conflict(after_rows, direct, effective, sum(b["duration_min"] for b in blocks))
                        limits.append({"code": "FUTURE_QUALITY_RESERVATION_WORK_CAP", "limit_minutes": _round(work)})
                    elif conflict[0] == "RESERVE_FUTURE_QUALITY_BUDGET" and (is_key or is_strength) and minimum_blocks:
                        # A future opportunity must not cancel today's due,
                        # executable quality. If even the current minimum cannot
                        # preserve that future guess, shrinking today's legal
                        # dose has no benefit. Reconcile later opportunities
                        # against the complete selected dose instead.
                        conflict = recovery_conflict(after_rows, direct, effective,
                            sum(b["duration_min"] for b in blocks), protect_future=False)
                        if not conflict and fits_component_budget(effective, direct):
                            evidence["future_reservation_relaxed_for_current_quality"] = True
                        elif not conflict:
                            conflict = ("RESERVE_FUTURE_QUALITY_BUDGET", "Минималната текуща доза не се побира в компонентния бюджет.")
                if conflict:
                    item["rejected_alternatives"].append({"method_id": method["id"], "code": conflict[0], "reason": conflict[1]})
                    continue
                after = recovery_v2.simulate(after_rows, configs["zones"], target=day, include_details=False, include_deadlines=False, calculation_cache=recovery_calculation_cache)
                total = sum(b["duration_min"] for b in blocks)
                actual_work = sum(b["duration_min"] for b in blocks if b["kind"] == "WORK")
                primary_share = _primary_work_share(method)
                primary_actual = sum(b["duration_min"] for b in blocks if b["kind"] == "WORK" and b["zone"] == z)
                evidence.update(base_fraction=base_fraction*primary_share, fraction=fraction*primary_share,
                                structure_base_fraction=base_fraction, structure_fraction=fraction,
                                structure_requested_work_minutes=_round(requested), structure_work_budget_minutes=_round(work),
                                fraction_scope="PRIMARY_COMPONENT", structure_work_scope="COMBINED_WORK" if primary_share != 1 else "PRIMARY_WORK",
                                requested_work_minutes=_round(requested * (1+evidence["easy_to_primary_ratio"] if mixed else 1) + evidence.get("combination_high_work_cap", 0.)), prescribed_work_minutes=_round(actual_work),
                                applied_structure_fraction=_round(_dose_usage(blocks, evidence, z)),
                                applied_minimum_capacity_fraction=_round(_minimum_dose_usage(blocks, evidence, z, primary_only=bool(mixed or method.get("developmental_variant")))),
                                requested_primary_work_minutes=_round(requested*primary_share),
                                primary_work_budget_minutes=_round(work*primary_share), applied_fraction=_round(primary_actual / evidence["capacity_minutes"]), dose_reduced=work + .01 < requested,
                                limits=limits, technical_spill_reference={z: _round(v) for z, v in technical.items()},
                                technical_spill_reference_role="LEGACY_HISTORICAL_TREF_DIAGNOSTIC_NOT_SPILL_CAPACITY",
                                source_method_id=method["source_id"], source_method_version=method["source_version"],
                                explanation="Капацитетът е от индивидуалната крива или обозначената експертна опора. Recovery намалява обема веднъж; усилието се запазва.")
                if method.get("method_library"):
                    evidence["explanation"] = "Основата е конкретната стандартна доза от избрания метод. Публикуваният протокол и адаптациите са разграничени. Поддържането използва 60% от тази доза; текущата роля определя еднократната корекция за Recovery. Запазват се интензивността, работният интервал и почивките. Избират се допустими цели повторения или непрекъснат вариант; ограниченията на плана могат да намалят или отложат задачата."
                    evidence["method_dose_factors"].append({"stage": "WHOLE_VARIANT_AND_PLAN_LIMITS", "work_minutes": _round(actual_work),
                        "difference_from_role_recovery_minutes": _round(actual_work-evidence["unrounded_requested_work_minutes"])})
                elif method["structure"] == "MODEL_INTERVALS":
                    evidence["explanation"] = "Метаболитният профил задава общия работен бюджет, повторенията, паузите и резерв от две отсечки. Процентите за Z1–Z3 не се прилагат втори път. Сумарната работа може да надхвърли непрекъснатата устойчивост само в границите на този профил. Изграждането използва избрания процент (начално 65%) от индивидуалния методен капацитет, поддържането и вработването — 60% от тази развиваща доза без Recovery множител. Развиващата доза се намалява с половината от дефицита на Recovery. Числата са видими начални треньорски настройки; 7/40 и Recovery могат да намалят дозата или да отложат сесията."
                elif method["structure"] == "METABOLIC_INTERVALS":
                    evidence["explanation"] = "Устойчивостта е индивидуално зададена за описаното усилие и средство. Цели повторения и активни паузи използват общия методен бюджет. Пулсът е наблюдение; не определя усилието или дозата. Прогнозният приравнен товар използва горната зонова референция и ще бъде заменен с реалното изпълнение."
                elif z == "STR":
                    evidence["explanation"] = "Дозата е от отделния силов профил, без аеробен Tmax. Работата, преходите и почивките се отчитат веднъж в силовата експозиция; загрявката и разпускането имат собствен аеробен товар. Остават поне 3 качествени повторения в резерв."
                if method["structure"] in {"ALTERNATING", "CRUISE_ALTERNATING", "AEROBIC_STRENGTH", "THRESHOLD_HIGH"}:
                    evidence["combination_allocation"] = "COMPONENT_METHOD_BUDGETS_WITH_SHARED_CANONICAL_Q_E"
                    evidence["explanation"] += " Компонентите имат отделни методни дози; общият товар с низходящото влияние и съседните добавки и общото време се проверяват съвместно."
                if method.get("combined_methods"):
                    evidence["explanation"] = method["instructions"] + " " + method["adaptation"]
                if method.get("double_threshold"):
                    evidence["combination_allocation"] = "TWO_INDEPENDENT_TMAX_DOSES_SHARED_Q_E_BUDGET"
                    evidence["double_threshold_fraction"] = (controls or {}).get("double_threshold_fraction", .5)
                    evidence["planned_gap_hours"] = (controls or {}).get("double_threshold_gap_hours", 6.)
                    evidence["explanation"] = (f"Две отделно изчислени цели по {evidence['double_threshold_fraction']*100:g}% от развиващата доза при съответното усилие: дълги 6–10 мин и кратки 60 сек интервали. "
                        f"Планирай поне {evidence['planned_gap_hours']:g} часа между сесиите. Няма повторна проверка за 90% между тях. "
                        "Recovery проверява готовността в началото на деня и сумарния дневен товар; не изчислява възстановяване по часове. "
                        "Общите Q/7–40 и времеви бюджети могат да намалят двете дози. Втората сесия се изпълнява само при запазени техника, поносимост и контрол на усилието.")
                if mixed:
                    evidence["explanation"] = (f"Допълваща работа в {z} след 15 мин загрявка и упражнения, следвана от леката част. "
                        f"Готовност {ready[z]:.1f}%, общ множител на дозата {readiness_rule['dose_factor']:.3f}. "
                        "Краткият блок използва само остатъка по Q/7–40. Z1, общата доза и готовността за следващите ключови задачи се проверяват отделно.")
                elif method.get("developmental_variant"):
                    evidence["explanation"] += " Приложен е вариант с по-кратки отсечки и по-дълги почивки според възрастта, опита или експозицията. Началният дял от Tmax следва целта на тренировката."
                if mixed:
                    evidence["explanation"] += f" Допълващият компонент е до {fraction*100:g}% от Tmax след Recovery; леката част има собствен поддържащ бюджет. Общият товар с преливането участва веднъж в Q/7–40."
                elif _absolute_minimum(method):
                    evidence["explanation"] += f" Минималната работна част за тази лека задача е {method['absolute_min_work_min']:g} минути. Максималната доза запазва ограниченията на капацитета, Recovery и общия товар."
                elif method.get("combined_methods"):
                    evidence["explanation"] += " Всяка част запазва собствената си минимална цяла доза."
                elif z != "STR":
                    evidence["explanation"] += f" Дозовият дял е от {method.get('minimum_fraction', MIN_AEROBIC_DOSE_FRACTION)*readiness_rule['dose_factor']*100:g}% до {max_usage*100:g}% от съответния капацитет."
                evidence["explanation"] += f" Основна доза: {base_fraction*primary_share*100:g}% × Recovery {readiness_rule['dose_factor']:.3f} = {fraction*primary_share*100:.1f}%. Запазват се цели повторения и подготовката."
                if primary_share != 1:
                    evidence["explanation"] += " При този метод общият работен бюджет включва редуването на двата компонента; показаната основна доза е само делът на основния компонент."
                if method.get("strength_profile"):
                    evidence["strength_prescription"] = strength_methods.summary(blocks, method)
                    evidence["explanation"] = ("Постоянен набор според основния спорт. Дозата е от изграждащите повторения за едно занимание, без Tmax. "
                        "Всички упражнения се запазват; намаляват се цели серии. Почивките се включват веднъж в STR, без загрявката и разпускането. "
                        "Времената на работните серии са прогнозни; водят повторенията, тежестта и резервът.")
                normalized_deficit = budgets[z]["deficit_effective"] / max(1., budgets[z]["target_weekly_effective"])
                priority_weight = mesocycle_focus.growth_weight(cycle_state, z) if cycle_state and cycle_state.get("component_indices") else float(z in selected_accents)
                ranking_targets = {c: r["target"] for c, r in period_objectives.items()} if period_objectives else None
                score = (2. * planning_allocation.coverage(objective_load(direct, effective), allocation, selected_accents, targets=ranking_targets)
                         if allocation is not None else normalized_deficit) + priority_weight - 20. * stranded_fraction
                if period in {"PRECOMPETITION", "COMPETITION"} and sport == profile.get("actual_sport"):
                    score += .25
                # Cover qualities over actual execution plus this proposed
                # week, counting WORK blocks, never warm-up/cascade as coverage.
                trained_dates = set().union(*(dates for (_, component), dates in recorded_exposures.items() if component == z))
                proposed_dates = {d["date"] for d in result_days if d.get("session") and
                    sum(b["duration_min"] for b in d["session"]["blocks"] if b["kind"] == "WORK" and b["zone"] == z) >= 1}
                last_stimulus = max(trained_dates | proposed_dates, default=None)
                since = (day-date.fromisoformat(last_stimulus)).days if last_stimulus else 28
                maintenance_need = min(3., since/7)
                score += maintenance_need - (0. if allocation is not None else 2.5*len(proposed_dates))
                if post_target_maintenance:
                    # Balance actual + proposed coverage. An overfilled high
                    # zone gains no priority by copying the original mix again.
                    score -= max(0., (period_objectives[z]["actual"] + period_objectives[z]["planned"])
                                 / max(1., period_objectives[z]["target"]) - 1.)
                if is_key and len(key_slots) > 1 and day == key_slots[-1] and z not in selected_accents:
                    score += 1.5
                evidence["selection"] = {"accent": z in selected_accents, "role": purpose,
                                         "days_since_component_work": since, "proposed_component_days": len(proposed_dates),
                                         "maintenance_priority": maintenance_need, "stranded_fraction": stranded_fraction}
                if post_target_maintenance:
                    evidence["selection"]["post_target_maintenance"] = True
                if allocation is not None:
                    evidence["selection"].update(component_allocation={k: _round(v) for k, v in allocation.items()},
                                                   coverage_score=_round(planning_allocation.coverage(objective_load(direct, effective), allocation, selected_accents, targets=ranking_targets)),
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
                if method.get("combined_methods"):
                    score += .4
                if method.get("method_library"):
                    score += .15
                if method.get("race_specific") and period in race_specific.PERIODS and z in selected_accents:
                    score += 1. if method["race_specific"]["role"] == "RACE" else .5
                if method["structure"] in {"ALTERNATING", "CRUISE_ALTERNATING", "AEROBIC_STRENGTH", "THRESHOLD_HIGH"}:
                    score += .15
                family = method_family(method)
                repeats = sum(s.get("method_family", s.get("dose_evidence", {}).get("method_family")) == family
                              for d in result_days for s in planning_schedule.day_sessions(d))
                diversity_penalty = 0. if method.get("strength_profile") else min(1.5, .45 * repeats)
                score += strength_methods.priority(method, period)
                score -= diversity_penalty
                if is_threshold and day.weekday() in set(threshold_days + double_days):
                    score += .4
                evidence["method_family"] = family
                evidence["selection"].update(method_family=family, family_repeats=repeats,
                                               diversity_penalty=diversity_penalty)
                if method.get("double_threshold"):
                    score += 10.
                if method.get("neuromuscular_profile"):
                    score += 1.
                learned_preference = None if learned_intensity else _learned_preference(
                    method, sport, profile, progression, day, period, cycle_state, purpose=purpose, duration=total,
                    original_method=original_method, target_speed=evidence.get("target_speed_kmh"), taper=taper, limited=limited)
                if learned_preference:
                    score += learned_preference["applied_score_delta"]
                    evidence["selection"]["individual_learning"] = learned_preference
                blocks = training_guidance.annotate_lactate(blocks, profile, sport, settings, day)
                if method.get("double_threshold"):
                    training_guidance.annotate_double_threshold(blocks, profile)
                choice_methods[(sport, method["id"])] = _copy_method_budget(method)
                def choice_check(parts, *, capacity=deepcopy(evidence), zone=z, ceiling=max_usage,
                                 time_ceiling=session_ceiling, conflict_check=recovery_conflict,
                                 budget_check=fits_component_budget if not limited else lambda e, q: True):
                    if (not parts or not _dose_fits(parts, capacity, zone, ceiling, tolerance=1e-6)
                        or sum(b["duration_min"] for b in parts) > min(day_dose_available, remaining) + .001
                        or any(sum(b["duration_min"] for b in parts if b.get("session_index", 1) == part) > time_ceiling + .001
                               for part in {b.get("session_index", 1) for b in parts})):
                        return False
                    q, e, _ = candidate_load(parts, capacity=capacity)
                    return (budget_check(e, q) and not conflict_check(
                        _add_forecast_session(forecast_rows, day, e), q, e, sum(b["duration_min"] for b in parts)))
                choice_checks[(sport, method["id"])] = choice_check
                choice_headroom[(sport, method["id"])] = headroom if not limited else None
                if not mixed and method["structure"] not in method_registry.COMBINED_STRUCTURES and direct[z] > .001:
                    standalone_zones.add(z)
                elif mixed and not shortage["shortage"]:
                    evidence["selection"]["supporting_basis"] = "NO_ADMISSIBLE_STANDALONE_IN_THIS_SLOT"
                choices.append((score, method["id"], {"method_id": method["id"], "title": method["title"],
                                "execution_descriptor": {**method_descriptor(original_method, sport),
                                    "purpose": purpose, "resolved_role": purpose, "post_target_maintenance": post_target_maintenance,
                                    **{key: evidence[key] for key in ("speed_role", "initial_heuristic", "calibration_source") if key in evidence}},
                                "sport": sport, "zone": z, "purpose": purpose, "method_family": family, "blocks": blocks, "is_key_session": is_key,
                                "neuromuscular_exposure": training_guidance.nms_exposure(blocks),
                                "double_threshold": method.get("double_threshold", False), "mixed_component": mixed,
                                "paired_session": {"zone":method["paired_method"]["zone"], "method_id":method["paired_method"]["id"]} if method.get("paired_method") else None,
                                "main_work_minutes": _round(actual_work), "total_minutes": _round(total),
                                "canonical_effective_load": {z: _round(v) for z, v in effective.items()},
                                "direct_equivalent_minutes": {z: _round(v) for z, v in direct.items()},
                                "method_library": deepcopy(method.get("method_library") or method.get("method_card")),
                                "registry_version": method_registry.VERSION,
                                "dose_evidence": evidence}, after_rows, after))
            if choices:
                minimum_cores = {}
                if period_objectives is not None:
                    for candidate in choices:
                        proposed = candidate[2]
                        identity = (proposed["sport"], proposed["method_id"])
                        # Pairs retain their selected complete dose during
                        # balancing; rank the bundle against that same dose.
                        minimum_parts = (proposed["blocks"] if proposed.get("double_threshold") else
                            _blocks(choice_methods[identity], proposed["dose_evidence"].get("minimum_structure_work_minutes", proposed["dose_evidence"]["minimum_primary_work_minutes"]),
                                    proposed["dose_evidence"], settings))
                        minimum_q, minimum_e, _ = candidate_load(minimum_parts, capacity=proposed["dose_evidence"])
                        minimum_cores[identity] = (minimum_parts, minimum_q, minimum_e)
                placement_readiness_cache, placement_core_cache = {}, {}
                def reservation_check(parts, protected_cores, *, capacity):
                    """Check one causal, dated bundle without executing it."""
                    current_q, current_e, _ = candidate_load(parts, capacity=capacity)
                    total_q, total_e = dict(current_q), dict(current_e)
                    total_minutes = sum(b["duration_min"] for b in parts)
                    daily_minutes = {day:total_minutes}
                    shadow = _add_forecast_session(forecast_rows, day, current_e)
                    shadow_days = list(result_days)+[{"date":day.isoformat(), "status":"TRAINING",
                        "sessions":[{"direct_equivalent_minutes":current_q, "total_minutes":total_minutes}]}]
                    records = []
                    all_records = []
                    ledger = sorted(reserved_keys + protected_cores,
                        key=lambda r:(r["date"], min(r.get("slots", [r.get("slot", 0)]))))
                    for record in ledger:
                        placed_day = record["date"]
                        method, cap = _copy_method_budget(record["method"]), _copy_capacity_budget(record["capacity"])
                        core = record["blocks"]
                        protected = record in protected_cores
                        if protected and not record.get("objective_work"):
                            method["min_work_min"] = method.get("absolute_min_work_min", method["min_work_min"])
                        # All sessions on one day share causal day-start
                        # readiness; only earlier dates are intervening load.
                        before = [r for r in shadow if r["date"] != placed_day.isoformat()] + [
                            r for r in rows if r["date"] == placed_day.isoformat()]
                        readiness_key = (placed_day, tuple((r["date"],r["zone"],r["effective_load"])
                            for r in before if r["date"] <= placed_day.isoformat()))
                        if readiness_key not in placement_readiness_cache:
                            placement_readiness_cache[readiness_key] = {r["zone"]:r["readiness_percent"]
                                for r in recovery_v2.simulate(before, configs["zones"],
                                    target=placed_day, include_details=False, include_deadlines=False, calculation_cache=recovery_calculation_cache)["current"]}
                        future_ready = placement_readiness_cache[readiness_key]
                        cap["readiness_policy"] = adaptive_methods.readiness_policy(method, profile, future_ready)
                        factor = cap["readiness_policy"]["dose_factor"]
                        cap["readiness_dose_factor"] = factor
                        future_period, future_taper = _phase(periodization, placed_day)
                        future_state = day_goals[placed_day][2]
                        purpose = record["purpose"]
                        slot = min(record.get("slots", [record.get("slot", 0)]))
                        if purpose == "BUILDING" and not method.get("double_threshold") and (
                            reentry_dose_active(periodization, placed_day) or method["zone"] not in day_goals[placed_day][1] or future_taper
                            or future_state and future_state["kind"] == "RECOVERY"):
                            purpose = "MAINTENANCE"
                        nominal = _nominal_fraction(method, purpose, profile, "RE_ENTRY" if reentry_dose_active(periodization, placed_day) else future_period, cap, controls)
                        factor = cap["readiness_dose_factor"]
                        if protected and not record.get("objective_work"):
                            # Rebuild a whole minimum at placement-day R.
                            # Better recovery may require a larger minimum;
                            # lower recovery may make a smaller core legal.
                            cap["easy_work_cap_minutes"] = 0.
                            core_key = (method.get("actual_sport"), method["id"], cap["capacity_minutes"],
                                method["min_work_min"], factor)
                            if core_key not in placement_core_cache:
                                minimum = _minimum_work(method, cap, settings)
                                placement_core_cache[core_key] = (minimum,
                                    _blocks(method, minimum, cap, settings) if minimum is not None else [])
                            minimum, core = placement_core_cache[core_key]
                            if minimum is None:
                                return None
                        ceiling = _dose_ceiling(method, purpose, profile, future_period, cap, controls,
                            (progression or {}).get("config", {}).get("max_dose_fraction", .8),
                            reentry=reentry_dose_active(periodization, placed_day))
                        primary = sum(b["duration_min"] for b in core if b["kind"] == "WORK" and b["zone"] == method["zone"])
                        work = minimum if protected and not record.get("objective_work") else record.get("work", primary)
                        if (factor <= 0 or work > _role_work_limit(cap, cap["capacity_minutes"]*nominal*factor)+.001
                            or not _dose_fits(core, cap, method["zone"], ceiling*factor)
                            or method["zone"] != "STR" and not _absolute_minimum(method) and
                                _minimum_dose_usage(core, cap, method["zone"], primary_only=bool(
                                    method.get("mixed_component") or method.get("developmental_variant")))+.001
                                < method.get("minimum_fraction", MIN_AEROBIC_DOSE_FRACTION)*factor):
                            return None
                        minutes = sum(b["duration_min"] for b in core)
                        daily_minutes[placed_day] = daily_minutes.get(placed_day, 0.)+minutes
                        daily_ceiling = day_available if placed_day == day else available[placed_day.weekday()]
                        if daily_minutes[placed_day] > daily_ceiling+.001:
                            return None
                        if minutes > time_headroom(placed_day, shadow_days)+.001:
                            return None
                        total_minutes += minutes
                        cq, ce, _ = _candidate_load(core, settings, before, placed_day, zone_tmax_minutes=cap.get("zone_tmax_minutes"))
                        future_budget, future_q, future_segment = component_budgets(shadow, shadow_days, placed_day)
                        if not reservation_fits(placed_day, method, core, cq, ce, future_budget, future_q, future_segment):
                            return None
                        shadow = _add_forecast_session(shadow, placed_day, ce)
                        shadow_days.append({"date":placed_day.isoformat(), "status":"TRAINING",
                            "sessions":[{"direct_equivalent_minutes":cq, "total_minutes":minutes}]})
                        if segment_keys[placed_day] == segment_keys[day]:
                            for c in COMPONENTS:
                                total_q[c] += cq[c]
                                total_e[c] += ce[c]
                        if protected:
                            records.append({"component":method["zone"], "method_id":method["id"],
                                "date":placed_day.isoformat(), "slot":slot+1, "minutes":_round(minutes),
                                "readiness_dose_factor":factor})
                        all_records.append({"component":method["zone"], "method_id":method["id"],
                            "date":placed_day.isoformat(), "slot":slot+1, "minutes":_round(minutes),
                            "direct_q":{c:_round(v) for c,v in cq.items()}})
                    # Every dated task has already spent the shared ledger once.
                    # Rechecking the aggregate against today's unextended Q
                    # would reject a legal preparation reserve a second time.
                    if not automatic_time and total_minutes > remaining+.001:
                        return None
                    return {"q":{c:total_q[c]-current_q[c] for c in COMPONENTS},
                            "e":{c:total_e[c]-current_e[c] for c in COMPONENTS}, "records":records,
                            "all_records":all_records}
                relative_recovery = bool(cycle_state and cycle_state["kind"] == "RECOVERY"
                    and any(g.get("recovery_reference") for g in goals.values()))
                def needs_preparation(component):
                    return (planning_allocation.quality_priority(component, period_objectives)
                        or planning_allocation.remaining_priority(component, period_objectives))
                def preparation_bundle(candidate):
                    session = candidate[2]
                    selected_method = choice_methods[(session["sport"], session["method_id"])]
                    selected_evidence = session["dose_evidence"]
                    selected_work = selected_evidence.get("structure_work_budget_minutes", selected_evidence["primary_work_budget_minutes"])
                    selected_minimum = selected_evidence.get("minimum_structure_work_minutes", selected_evidence["minimum_primary_work_minutes"])
                    minimum_parts, minimum_q, minimum_e = minimum_cores[(session["sport"], session["method_id"])]
                    protected_q = {c: 0. for c in COMPONENTS}
                    protected_e = {c: 0. for c in COMPONENTS}
                    protected_components = []
                    current_cost = 2 if session.get("double_threshold") else 1
                    future_slots = [(d, s) for d, s, count in schedule
                        if count > 0 and segment_keys[d] == segment_keys[day] and (d > day or d == day and s >= slot_index + current_cost)
                        and (d, s) not in reserved_key_slots
                        and not any(a.get("date") == d.isoformat() for a in existing_in_draft)
                        and not any(ev["event_type"] in {"MAIN_RACE", "CONTROL_RACE", "TEST", "UNAVAILABLE"}
                                    and str(ev["start_date"]) <= d.isoformat() <= str(ev["end_date"]) for ev in events)]
                    same_cycle_keys = sum(segment_keys[d] == segment_keys[day] for d,_ in reserved_key_slots)
                    slots_left = min(len(future_slots), max(0, session_limit-sessions-current_cost-same_cycle_keys))
                    minimum_options = {}
                    def core_priority(option):
                        return (not reservation_preference(option[7], option[8]["method"].get("actual_sport", primary_sport),
                                                           [d for d,_ in option[5]]), *option[:2])
                    # Strength can become executable after today's spacing
                    # restriction. Keep that future core visible before an
                    # optional easy finish consumes its preparation allowance.
                    if session["zone"] != "STR" and needs_preparation("STR"):
                        for future_day, future_kind, options in future_options:
                            if future_kind != "STRENGTH" or segment_keys[future_day] != segment_keys[day]:
                                continue
                            compatible = [(d,s) for d,s in future_slots if d == future_day]
                            future_ready = {r["zone"]:r["readiness_percent"] for r in recovery_v2.simulate(
                                _add_forecast_session(forecast_rows, day, minimum_e), configs["zones"],
                                target=future_day, include_details=False, include_deadlines=False, calculation_cache=recovery_calculation_cache)["current"]}
                            for option in options:
                                if not compatible or option["double"] or not executable_reservation(option, future_ready):
                                    continue
                                candidate_core = (option["q"]["Z1"], sum(option["e"].values()), 1,
                                    option["q"], option["e"], compatible, option["minutes"], option["method_id"],
                                    {"method":option["method"], "capacity":option["capacity"], "blocks":option["blocks"],
                                     "purpose":option["purpose"], "work":option["work"], "dose_ceiling":option["dose_ceiling"]})
                                if "STR" not in minimum_options or core_priority(candidate_core) < core_priority(minimum_options["STR"]):
                                    minimum_options["STR"] = candidate_core
                    for candidate in choices:
                        alternative = candidate[2]
                        component = alternative["zone"]
                        if (component in {"Z1", session["zone"]}
                            or not needs_preparation(component)):
                            continue
                        # The existing chronological reservation already owns
                        # key dates, spacing and paired-session allowances.
                        # This pass protects additional complete supplements
                        # and strength, without reserving those keys twice.
                        if alternative["is_key_session"]:
                            continue
                        cost = 2 if alternative.get("double_threshold") else 1
                        if component == "STR":
                            previous = day if session["zone"] == "STR" else last_strength_day
                            if (strength_sessions + int(session["zone"] == "STR") >= strength_limit
                                or not any(not previous or (d-previous).days >= 2 for d, _ in future_slots)):
                                continue
                        alternative_method = choice_methods[(alternative["sport"], alternative["method_id"])]
                        alternative_evidence = alternative["dose_evidence"]
                        core, cq, ce = minimum_cores[(alternative["sport"], alternative["method_id"])]
                        objective_work = component in {"Z3", "Z4", "Z5", "STR"}
                        if objective_work:
                            core = alternative["blocks"]
                            cq, ce, _ = candidate_load(core, capacity=alternative_evidence)
                        core_minutes = sum(b["duration_min"] for b in core)
                        compatible = [(d,s) for d,s in future_slots if _phase(periodization, d)[0] in alternative_method["periods"]
                            and not (_phase(periodization, d)[1] and alternative_method["purpose"] == "SUPPORTING")
                            and core_minutes <= (available[d.weekday()] if d > day else
                                                 day_available-sum(b["duration_min"] for b in minimum_parts))+.001
                            and (component != "STR" or not previous or (d-previous).days >= 2)
                            ]
                        if not compatible:
                            continue
                        option = (cq["Z1"], sum(ce.values()), cost, cq, ce, compatible, core_minutes, alternative["method_id"],
                            {"method":alternative_method, "capacity":alternative_evidence, "blocks":core,
                             "purpose":alternative["purpose"], "objective_work":objective_work,
                             "work":alternative_evidence["structure_work_budget_minutes"]})
                        if core and (component not in minimum_options or core_priority(option) < core_priority(minimum_options[component])):
                            minimum_options[component] = option
                    used_future_slots = set()
                    used_future_minutes = {}
                    for record in reserved_keys:
                        used_future_minutes[record["date"]] = used_future_minutes.get(record["date"], 0.)+record["minutes"]
                    protected_methods = []
                    protected_cores = []
                    for component, (_, _, cost, cq, ce, compatible, core_minutes, method_id, specification) in sorted(minimum_options.items(), key=lambda item: item[1][:2]):
                        if cost > slots_left:
                            continue
                        placement, checked = None, None
                        for d,s in compatible:
                            if ((d,s) in used_future_slots or used_future_minutes.get(d, 0.)+core_minutes >
                                (available[d.weekday()] if d > day else day_available-sum(b["duration_min"] for b in minimum_parts))+.001):
                                continue
                            trial = {**specification, "date":d, "slot":s}
                            checked = reservation_check(minimum_parts, protected_cores+[trial], capacity=selected_evidence)
                            if checked is not None:
                                placement = (d,s)
                                break
                        if placement is None:
                            continue
                        protected_cores.append(trial)
                        protected_q, protected_e = checked["q"], checked["e"]
                        protected_components.append(component)
                        protected_methods = checked["records"]
                        used_future_slots.add(placement)
                        used_future_minutes[placement[0]] = used_future_minutes.get(placement[0], 0.)+next(
                            r["minutes"] for r in checked["records"] if r["date"] == placement[0].isoformat() and r["slot"] == placement[1]+1)
                        slots_left -= cost
                    return dict(method=selected_method, evidence=selected_evidence, work=selected_work,
                        minimum=selected_minimum, parts=minimum_parts, minimum_q=minimum_q, minimum_e=minimum_e,
                        protected_q=protected_q, protected_e=protected_e, components=protected_components,
                        methods=protected_methods, cores=protected_cores)
                # Preparation is an unavoidable part of already reserved
                # quality. Primary components are scored when actually placed.
                reserved_credit = {z:sum(r["q"][z] for r in reserved_keys
                    if segment_keys[r["date"]] == segment_keys[day]) if z == "Z1"
                    and period_objectives and period_objectives[z]["basis"] == "DIRECT_Q" else 0. for z in COMPONENTS}
                def choice_priority(c):
                    preferred = [r for r in reservation_methods.get(day.isoformat(), []) if r.get("slot") == slot_index+1]
                    allowance = choice_headroom[(c[2]["sport"], c[2]["method_id"])] or {}
                    extra_taper = (taper_continuity_allowed and allowance.get("mode") and
                        planning_continuity.evidence(c[2]["direct_equivalent_minutes"],
                            c[2]["canonical_effective_load"], allowance, active_segment))
                    gap_priority = ()
                    if period_objectives and (profile.get("_allocation_policy") or {}).get("component_gap_first"):
                        load = objective_load(c[2]["direct_equivalent_minutes"], c[2]["canonical_effective_load"])
                        support = (planning_continuity.evidence(c[2]["direct_equivalent_minutes"],
                            c[2]["canonical_effective_load"], allowance, active_segment) or {}).get("support_q", {})
                        # Rank the proposed week, including the quality work
                        # already reserved. Its mandatory preparation must not
                        # reward another early easy finish for filling Z1 twice.
                        future_support = min(reserved_credit["Z1"], max(0.,
                            (period_objectives["Z1"].get("actual") or 0.)+period_objectives["Z1"]["planned"]
                            +load["Z1"]+reserved_credit["Z1"]-period_objectives["Z1"]["target"]
                            -period_objectives["Z1"].get("support_reserve_q", 0.)-support.get("Z1", 0.))) if period_objectives["Z1"]["basis"] == "DIRECT_Q" else 0.
                        projected = [(z, {**row, "planned": row["planned"]+load[z]+reserved_credit[z],
                            "support_reserve_q":row.get("support_reserve_q", 0.)+support.get(z, 0.)
                                +(future_support if z == "Z1" else 0.)})
                                     for z, row in period_objectives.items()]
                        gap_priority = tuple(-v for v in planning_allocation.gap_score(projected))
                    return (not c[2]["is_key_session"] if day in key_slots and slot_index == first_plannable_slot(day) else False,
                        bool(allowance.get("mode") in {"POST_TARGET_MAINTENANCE", "PLANNED_EASY_MAINTENANCE"} or extra_taper),
                        *gap_priority,
                        bool(preferred) and not any(r["method_id"] == c[2]["method_id"] and r["sport"] == c[2]["sport"] for r in preferred),
                        c[2]["direct_equivalent_minutes"]["Z1"] if gap_priority and c[2]["zone"] != "Z1" else 0.,
                        not (c[2]["zone"] == "Z1" and c[2]["purpose"] != "RECOVERY"
                             and choice_methods[(c[2]["sport"], c[2]["method_id"])]["structure"] in {"CONTINUOUS", "TWO_REPETITIONS"})
                            if relative_recovery and day.weekday() == preferences.get("long_session_day") else False,
                        -planning_allocation.quality_priority(c[2]["zone"], period_objectives) if period_objectives else 0,
                        -planning_allocation.remaining_priority(c[2]["zone"], period_objectives) if period_objectives and relative_recovery else 0,
                        bool(choice_methods[(c[2]["sport"], c[2]["method_id"])].get("combined_methods")
                             or choice_methods[(c[2]["sport"], c[2]["method_id"])]["structure"] == "AEROBIC_STRENGTH")
                            if (profile.get("_allocation_policy") or {}).get("prefer_standalone") else False)
                # A lower leading priority cannot win a lexicographic sort.
                # Keep all alternatives available as future cores, but only
                # compute the expensive bundle tie-breaker for possible winners.
                best_priority = min(choice_priority(c) for c in choices)
                bundles = {(c[2]["sport"], c[2]["method_id"]):preparation_bundle(c) for c in choices
                    if choice_priority(c) == best_priority} if period_objectives else {}
                executable_quality = bool(period_objectives and any(
                    c[2]["zone"] in {"Z3", "Z4", "Z5", "STR"}
                    and planning_allocation.remaining_priority(c[2]["zone"], period_objectives)
                    and choice_priority(c) == best_priority for c in choices))
                def closes_easy_objective(candidate):
                    session = candidate[2]
                    remaining_q = (period_objectives["Z1"]["remaining"] if period_objectives else q_remaining.get("Z1"))
                    return (session["zone"] == "Z1" and remaining_q is not None
                            and remaining_q-session["direct_equivalent_minutes"]["Z1"] < .5)
                choices.sort(key=lambda c: (*choice_priority(c),
                    # Extra reservations protect unfinished work from an easy
                    # finish. They must not reward repeatedly postponing that
                    # work: Z1 can reserve more different components merely
                    # because it performs none of their primary work today.
                    -sum(bool(planning_allocation.quality_priority(z, period_objectives)
                              or relative_recovery and planning_allocation.remaining_priority(z, period_objectives))
                         for z in bundles.get((c[2]["sport"], c[2]["method_id"]), {}).get("components", [])),
                    # A small easy finish must not consume the preparation
                    # for an executable residual quality objective. Preserve
                    # the existing ranking among the other legal candidates.
                    bool(executable_quality and closes_easy_objective(c)),
                    not closes_easy_objective(c), -c[0], c[1]))
                _, _, session, selected_rows, selected_after = choices[0]
                if period_objectives is not None:
                    bundle = bundles[(session["sport"], session["method_id"])]
                    selected_method, selected_evidence = bundle["method"], bundle["evidence"]
                    selected_work, selected_minimum = bundle["work"], bundle["minimum"]
                    minimum_parts = bundle["parts"]
                    protected_q, protected_e = bundle["protected_q"], bundle["protected_e"]
                    protected_components = bundle["components"]
                    def balanced_fit(parts):
                        if not choice_checks[(session["sport"], session["method_id"])](parts):
                            return False
                        if protected_components:
                            return reservation_check(parts, bundle["cores"], capacity=selected_evidence) is not None
                        if not any(protected_q.values()) and not any(protected_e.values()):
                            return True  # The selected candidate's own policy was checked above.
                        return reservation_check(parts, bundle["cores"], capacity=selected_evidence) is not None
                    parts = session["blocks"]
                    if protected_components and not balanced_fit(parts):
                        if not session.get("double_threshold") and balanced_fit(minimum_parts):
                            lo, hi = selected_minimum, selected_work
                            for _ in range(16):
                                midpoint = (lo+hi)/2
                                trial = _blocks(selected_method, midpoint, selected_evidence, settings)
                                if balanced_fit(trial):
                                    lo = midpoint
                                else:
                                    hi = midpoint
                            selected_work = max(selected_minimum, _quantized_work(selected_method, lo))
                            parts = _blocks(selected_method, selected_work, selected_evidence, settings)
                            if not balanced_fit(parts):
                                selected_work, parts = selected_minimum, minimum_parts
                        else:
                            # A reservation is optional. It cannot remove the
                            # complete, executable current quality.
                            protected_components = []
                            protected_q = {c:0. for c in COMPONENTS}
                            protected_e = {c:0. for c in COMPONENTS}
                    if session.get("mixed_component"):
                        core_parts = parts
                        primary_minutes = sum(b["duration_min"] for b in parts if b["kind"] == "WORK" and b["zone"] == session["zone"])
                        lo, hi = 0., primary_minutes*selected_evidence["easy_to_primary_ratio"]*(
                            profile.get("_allocation_policy") or {}).get("optional_easy_fraction", 1.)
                        # Enrich only the optional finish; the primary effort,
                        # whole repetitions and full rests stay unchanged.
                        for _ in range(16):
                            midpoint = (lo+hi)/2
                            trial_evidence = {**selected_evidence, "easy_work_cap_minutes":midpoint}
                            trial = _blocks(selected_method, selected_work, trial_evidence, settings)
                            trial_q, _, _ = candidate_load(trial, capacity=trial_evidence)
                            easy_needed = (period_objectives["Z1"]["remaining"] or 0.)
                            if (trial_q["Z1"]+protected_q["Z1"] <= easy_needed+.001 and balanced_fit(trial)):
                                lo = midpoint
                            else:
                                hi = midpoint
                        selected_evidence["easy_work_cap_minutes"] = math.floor(lo*60)/60
                        parts = _blocks(selected_method, selected_work, selected_evidence, settings)
                        if not balanced_fit(parts):
                            selected_evidence["easy_work_cap_minutes"] = 0.
                            parts = core_parts
                    if parts != session["blocks"]:
                        q, e, technical = candidate_load(parts, capacity=selected_evidence)
                        actual_work = sum(b["duration_min"] for b in parts if b["kind"] == "WORK")
                        total = sum(b["duration_min"] for b in parts)
                        session.update(blocks=training_guidance.annotate_lactate(parts, profile, session["sport"], settings, day),
                            main_work_minutes=_round(actual_work), total_minutes=_round(total),
                            direct_equivalent_minutes={c:_round(v) for c,v in q.items()},
                            canonical_effective_load={c:_round(v) for c,v in e.items()})
                        primary_share = _primary_work_share(selected_method)
                        primary_actual = sum(b["duration_min"] for b in parts if b["kind"] == "WORK" and b["zone"] == session["zone"])
                        selected_evidence.update(prescribed_work_minutes=_round(actual_work),
                            structure_work_budget_minutes=_round(selected_work),
                            primary_work_budget_minutes=_round(selected_work*primary_share), applied_fraction=_round(primary_actual/selected_evidence["capacity_minutes"]),
                            applied_structure_fraction=_round(_dose_usage(parts, selected_evidence, session["zone"])),
                            applied_minimum_capacity_fraction=_round(_minimum_dose_usage(parts, selected_evidence, session["zone"],
                                primary_only=bool(session.get("mixed_component") or selected_method.get("developmental_variant")))),
                            technical_spill_reference={c:_round(v) for c,v in technical.items()},
                            dose_reduced=selected_work*primary_share+.01 < selected_evidence["requested_primary_work_minutes"])
                        selected_evidence["selection"]["coverage_score"] = _round(planning_allocation.coverage(
                            objective_load(q, e), selected_evidence["selection"]["component_allocation"], selected_accents,
                            targets={c:r["target"] for c,r in period_objectives.items()}))
                        selected_rows = _add_forecast_session(forecast_rows, day, e)
                        selected_after = recovery_v2.simulate(selected_rows, configs["zones"], target=day, include_details=False, include_deadlines=False, calculation_cache=recovery_calculation_cache)
                    if protected_components:
                        final_reservation = reservation_check(parts, bundle["cores"], capacity=selected_evidence)
                        protected_q, protected_e = final_reservation["q"], final_reservation["e"]
                        selected_evidence["limits"].append({"code":"LONG_TERM_QUALITY_PREPARATION",
                            "limit_minutes":_round(selected_work), "protected_components":protected_components,
                            "protected_methods":final_reservation["records"],
                            "reserved_methods":final_reservation["all_records"],
                            "reserved_direct_q":{c:_round(v) for c,v in protected_q.items()},
                            "reserved_effective":{c:_round(v) for c,v in protected_e.items()}})
                    selected_evidence["selection"]["long_term_uncovered_quality"] = bool(
                        planning_allocation.quality_priority(session["zone"], period_objectives))
                forecast_rows, after = selected_rows, selected_after
                allowance = choice_headroom[(session["sport"], session["method_id"])]
                continuity = (planning_continuity.evidence(session["direct_equivalent_minutes"],
                    session["canonical_effective_load"], allowance, active_segment) if allowance else None)
                if continuity:
                    continuity["history_basis"] = "MEASURED" if all_sources_known else "SUPPORTED_WITH_ESTIMATES"
                    session["dose_evidence"]["continuity"] = continuity
                    session["dose_evidence"]["explanation"] += " " + planning_continuity.explanation(continuity["mode"])
                if session.get("mixed_component") and session["dose_evidence"].get("selection", {}).get("component_allocation"):
                    item["load_budget"]["component_allocation"] = session["dose_evidence"]["selection"]["component_allocation"]
                after_ready = {r["zone"]: _round(r["readiness_percent"]) for r in after["current"]}
                parts = _session_parts(session, settings, day_start_rows, day, slot_index)
                covered_slots[day] = slot_index + len(parts)
                item.update(status="TRAINING", session=parts[0], sessions=parts,
                            readiness_after=after_ready if forecast_known else {z: None for z in COMPONENTS},
                            explanation=f"{session['title']}: {session['main_work_minutes']:g} минути основна работа, {session['total_minutes']:g} минути общо. " + ("Развитие на акцентния компонент." if session["purpose"] == "BUILDING" else "Поддържане на компонента извън основния акцент." if session["purpose"] == "MAINTENANCE" else "Активираща силова задача с намалена доза." if session["purpose"] == "ACTIVATION" else "Лека възстановителна задача.") + " Дозата е проверена спрямо капацитета, 7/40 и Recovery.")
                if continuity:
                    item["explanation"] += " " + planning_continuity.explanation(continuity["mode"])
                item["rejected_alternatives"] += [{"method_id": c[1], "code": "LOWER_CURRENT_PRIORITY", "reason": "Допустим метод с по-нисък текущ приоритет; не се добавя втора пълна доза."} for c in choices[1:]]
                remaining -= session["total_minutes"]
                if session["zone"] != "STR":
                    sport_spent[session["sport"]] = sport_spent.get(session["sport"], 0.) + session["total_minutes"]
                sessions += len(parts)
                if strength_activity(session):
                    strength_sessions += 1
                    strength_building += int(session["purpose"] not in {"MAINTENANCE", "ACTIVATION"})
                    last_strength_day = day
                if session.get("is_key_session", session["zone"] in {"Z3", "Z4", "Z5"}):
                    key_sessions += len(parts)
                    last_key_day = day
            else:
                item["explanation"] = "Няма метод с едновременно подходяща доза, бюджет и готовност. Почивката е допустим резултат."
                q_blockers = sorted({z for r in item["rejected_alternatives"]
                    if r["code"] == "DIRECT_Q_PROGRESSION_BUDGET" for z in r.get("blocking_components", [])})
                if q_blockers:
                    item["explanation"] = ("Оставащият приравнен обем в " + ", ".join(q_blockers)
                        + " не допуска минималната цяла тренировка с подготовката ѝ.")
                effective_blockers = sorted({z for r in item["rejected_alternatives"]
                    if r["code"] == "COMPONENT_BUDGET_EXHAUSTED" for z in r.get("blocking_components", [])})
                if effective_blockers:
                    item["explanation"] += (" Бюджетът за ефективен товар (включително 7/40) в "
                        + ", ".join(effective_blockers) + " не допуска минималния цял вариант на останалите методи.")
                if q_blockers or effective_blockers:
                    if taper_continuity_allowed:
                        item["explanation"] += " Ограниченият тейпър резерв до 10% за лека работа в Z1–Z2 или подготовка в Z1 не осигурява допустим цял вариант. Той не увеличава бюджета за основната работа в Z3–Z5."
                    elif continuity_allowed:
                        item["explanation"] += " Приложимите резерви за поддържане и подготовка също не осигуряват допустима доза."
                    else:
                        restrictions = []
                        if taper:
                            restrictions.append("предсъстезателно разтоварване")
                        if cycle_state and cycle_state["kind"] == "RECOVERY":
                            restrictions.append("възстановителен микроцикъл")
                        if reentry_dose_active(periodization, day):
                            restrictions.append("връщане след пауза")
                        if period == "TRANSITION":
                            restrictions.append("преходен период")
                        if not all_sources_known:
                            restrictions.append("непълно измерена история")
                        if not period_objectives:
                            restrictions.append("липсващи съпоставими цели за периода")
                        item["explanation"] += (" Резервите за поддържане и подготовка не се прилагат: "
                            + ", ".join(restrictions) + ".")
                manual_blockers = sorted({z for r in item["rejected_alternatives"] for z in r.get("manual_target_components", [])})
                if manual_blockers:
                    item["explanation"] = ("Ръчна цел за " + ", ".join(manual_blockers)
                        + " ограничава тренировките. Провери „Индивидуални цели по компоненти“ в профила. "
                        "Стойностите са приравнени минути за 7 дни; ниска цел за Z1 ограничава и по-високите аеробни зони.")
        if day_spent and item["session"] is None:
            after_day = recovery_v2.simulate(forecast_rows, configs["zones"], target=day, include_details=False, include_deadlines=False, calculation_cache=recovery_calculation_cache)
            item["readiness_after"] = {r["zone"]: _round(r["readiness_percent"]) if forecast_known else None for r in after_day["current"]}
        result_days.append(item)
        if item["session"] is None and item["status"] in {"REST", "UNAVAILABLE"} and not day_spent and not known_actual_load:
            forecast_rows = _with_forecast_day(forecast_rows, day, {})
        if slot_index+1 >= slots_today and day in key_slots:
            reserve_remaining_keys(day)
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
        if item.get("locked"):
            combined["locked"] = True
        if combined["sessions"]:
            combined["session"] = combined["sessions"][0]
            combined["status"] = "TRAINING"
            combined["explanation"] = f"{len(combined['sessions'])} сесии · {sum(v['total_minutes'] for v in combined['sessions']):g} минути общо. Общ бюджет по компоненти; готовността е оценена за началото на деня."
    result_days = list(grouped.values())
    for item in result_days:
        # More individual repeats must not duplicate pages of identical
        # guidance. Keep the complete text/reference on its first occurrence.
        for session in item.get("sessions", []):
            if item.get("locked"):
                continue
            seen_instructions, seen_lactate = {}, set()
            for index, block in enumerate(session["blocks"], 1):
                instruction_key = (block["kind"], block["zone"], block.get("instructions"))
                if instruction_key in seen_instructions:
                    block["instructions"] = f"Виж част {seen_instructions[instruction_key]}."
                else:
                    seen_instructions[instruction_key] = index
                if block.get("lactate_reference"):
                    key = json.dumps(block["lactate_reference"], sort_keys=True)
                    if key in seen_lactate:
                        block.pop("lactate_reference")
                    else:
                        seen_lactate.add(key)
        item["rejected_alternatives"] = compact_rejections(item["rejected_alternatives"])
    planned_sessions = [s for d in result_days for s in planning_schedule.day_sessions(d)]
    for session in planned_sessions:
        if any(b["zone"] == "STR" for b in session["blocks"]):
            session["strength_duration"] = _strength_duration(session["blocks"])
        if session.get("dose_evidence", {}).get("strength_profile"):
            session["dose_evidence"]["strength_prescription"] = strength_methods.summary(
                session["blocks"], {"strength_profile": session["dose_evidence"]["strength_profile"]})
    if planning_history_supported:
        warnings.extend(_race_recovery_warnings(events, result_days, forecast_rows, configs["zones"],
            calculation_cache=recovery_calculation_cache))
    forecast_complete = forecast_known and not blocked
    if visible_state is not None:
        # A race in the provisional tail makes later forecasts unknown. It
        # must not relabel the already evaluated visible week as blocked.
        blocked, forecast_known, activation_eligible = visible_state
    allocation_report = planning_allocation.report(goal_windows, rows, forecast_rows, result_days,
        sum(n for d,n in slot_counts.items() if d <= display_end), session_limit, weekly_minutes, source=source, segment_keys=segment_keys if component_governed else None) if component_governed and forecast_known and not blocked else None
    if allocation_report:
        continuity_sessions = [{"date":d["date"], "method_id":s["method_id"],
            "mode":s["dose_evidence"]["continuity"]["mode"],
            "rolling_limits_preserved":s["dose_evidence"]["continuity"]["rolling_limits_preserved"],
            "additional_effective":s["dose_evidence"]["continuity"]["additional_effective"],
            "additional_q":{z:v for z,v in s["dose_evidence"]["continuity"]["additional_q"].items() if v}}
            for d in result_days for s in planning_schedule.day_sessions(d)
            if s.get("dose_evidence", {}).get("continuity")]
        if continuity_sessions:
            allocation_report["continuity"] = {"version":planning_continuity.VERSION,
                "preparation_fraction":planning_continuity.PREPARATION_FRACTION,
                "taper_fraction":planning_continuity.TAPER_FRACTION,
                "reference_doses":{k:v for k,v in continuity_references.items() if v},
                "sessions":continuity_sessions,
                "targets_unchanged":True,
                "rolling_limits_preserved":all(s["rolling_limits_preserved"] for s in continuity_sessions)}
        # Optimistic structural upper bound, separate from physiological gates
        # and the desired target. Never relabel a greedy miss as a hard limit.
        role_q = {"BUILDING": 0., "MAINTENANCE": 0.}
        for method in methods:
            if method["zone"] != "STR":
                continue
            for role in role_q:
                cap = {"capacity_minutes": method["max_work_min"], "target_hr_bpm": None, "strength_profile": deepcopy(method.get("strength_profile"))}
                nominal = _nominal_fraction(method, role, profile, "GENERAL_PREPARATION", cap, controls)
                blocks = _blocks(method, _quantized_work(method, min(method["max_work_min"], cap["capacity_minutes"]*nominal)), cap, settings)
                role_q[role] = max(role_q[role], sum(b["duration_min"] for b in blocks if b["zone"] == "STR"))
        strength_bounds = []
        for segment in allocation_report.get("segments") or [allocation_report]:
            first, last = segment["window_start"], segment["window_end"]
            strength_accent = any("STR" in goals[1] for d, goals in day_goals.items()
                                  if first <= d.isoformat() <= last)
            quota = strength_methods.session_limit(controls, accent=strength_accent) if profile.get("strength_enabled") else 0
            actual_strength = [s for s in strength_recorded_sessions if first <= s.date <= last]
            remaining_slots = max(0, quota-len(actual_strength))
            building_slots = min(remaining_slots, max(0, 2-sum(recorded_strength_building(s)
                for s in actual_strength))) if strength_accent else 0
            actual_q = segment["components"]["STR"].get("actual") or 0.
            upper = actual_q + building_slots*role_q["BUILDING"] + (remaining_slots-building_slots)*role_q["MAINTENANCE"]
            desired = segment["components"]["STR"]["target"]
            strength_bounds.append({"window_start": first, "window_end": last,
                "desired_q": desired, "actual_q": actual_q,
                "attainable_upper_bound_q": _round(upper), "session_quota": quota,
                "quota_bound_below_target": upper+.001 < desired})
        allocation_report["strength_feasibility"] = planning_allocation.strength_feasibility(strength_bounds)
    lookahead_days = [dict(d, provisional=True) for d in result_days if d["date"] > display_end.isoformat()]
    if allocation_report:
        allocation_report.update(scope="WHOLE_MICROCYCLES", visible_start=start_date.isoformat(),
            visible_end=display_end.isoformat(), lookahead_end=end_date.isoformat(),
            forecast_complete=forecast_complete,
            lookahead_sessions=sum(len(planning_schedule.day_sessions(d)) for d in lookahead_days),
            lookahead_minutes=_round(sum(s["total_minutes"] for d in lookahead_days for s in planning_schedule.day_sessions(d))))
    result_days = [d for d in result_days if d["date"] <= display_end.isoformat()]
    race_course.annotate_days(result_days, events)
    race_course.annotate_days(lookahead_days, events)
    for result_day in [*result_days, *lookahead_days]:
        if not result_day.get("locked"):
            for planned_session in planning_schedule.day_sessions(result_day):
                execution_ranges.annotate(planned_session)
                planned_session["q_accounting"] = _q_accounting(planned_session["blocks"], settings)
                if planned_session["q_accounting"]["status"] == "INCOMPLETE":
                    activation_eligible = False
                    if not any(w["code"] == "PLANNED_Q_INCOMPLETE" for w in warnings):
                        warnings.append(_warning("PLANNED_Q_INCOMPLETE",
                            "Част от предписаното време няма допустим разчет на Q. Необходим е преглед на работните опори; неприравненото време не е нулев товар."))
    planned_sessions = [s for d in result_days for s in planning_schedule.day_sessions(d)]
    existing_in_draft = [a for a in existing_in_draft if a["date"] <= display_end.isoformat()]
    recorded_in_draft = [s for s in recorded_in_draft if s.date <= display_end.isoformat()]
    actual_window_minutes = sum(float(a.get("duration_min") or 0.) for a in existing_in_draft)
    end_date = display_end
    if not automatic_time and component_governed:
        # The visible-window report uses the same prospective envelope as the
        # allocator, independently of the explicitly configured rolling cap.
        visible_time_limit = weekly_minutes if estimated_initial_time else rolling_time_limit
        weekly_ceiling = min(sum(available[(start_date+timedelta(days=i)).weekday()]
                                 for i in range((end_date-start_date).days+1)),
                             visible_time_limit*((end_date-start_date).days+1)/7
                             if visible_time_limit is not None else float("inf"))
        remaining = max(0., weekly_ceiling-actual_window_minutes-sum(s["total_minutes"] for s in planned_sessions))
    parameters = {"version": PARAMETER_VERSION, "method_library": method_library.provenance(), "method_registry_version": method_registry.VERSION,
                  "continuity_policy": {"version":planning_continuity.VERSION,
                      "preparation_fraction":planning_continuity.PREPARATION_FRACTION,
                      "quality_preparation_extra":"TEN_PERCENT_OF_FROZEN_Q_TARGETS_PLUS_EASY_REFERENCE_BUDGET_ONLY_FOR_PREPARATION",
                      "whole_dose_rounding":"UPWARD_WITHIN_COMPONENT_ROLE_RECOVERY_AND_TIME_LIMITS",
                      "building_session_exposure_tolerance":planning_allocation.COVERAGE_TOLERANCE,
                      "maintenance_reserve":"ONE_NOMINAL_MAINTENANCE_REFERENCE_PER_EXECUTED_COMPONENT",
                      "basis":"COACH_POLICY_NOT_PHYSIOLOGICAL_THRESHOLD", "rolling_limits_preserved_outside_taper":True,
                      "taper":{"maximum_excess_fraction":planning_continuity.TAPER_FRACTION,
                          "scope":"Z1_Z2_LIGHT_WORK_OR_Z1_PREPARATION_ONLY",
                          "history":"MEASURED_OR_EXPLICIT_SUPPORTED_ESTIMATE",
                          "anchor":"ORIGINAL_MICROCYCLE_Q_E_AND_ROLLING_E_TARGETS",
                          "actual_and_planned_excess_charged":True,
                          "daily_cap_scope":"ALL_ACTUAL_AND_PLANNED_SESSIONS"}},
                  "race_course_policy": race_course.VERSION, "execution_range_policy": execution_ranges.VERSION,
                  "recorded_session_policy": {"version": training_sessions.VERSION,
                      "max_end_to_start_gap_minutes": training_sessions.MAX_GAP_MINUTES,
                      "same_calendar_day": True, "unknown_timing": "SEPARATE_RECORDS",
                      "key_classification": [{"date":s.date, **s.key_evidence} for s in recorded_sessions
                          if (start_date-timedelta(days=6)).isoformat() <= s.date <= end_date.isoformat()]},
                  "method_progression": {k:v for k,v in profile["_method_progression"].items() if k != "observations"}, "race_duration": event_duration, "status": "COACH_HEURISTICS_FOR_REVIEW",
                  "individual_learning": (progression or {}).get("individual_learning"),
                  "recovery_mode": "LOAD_ONLY", "ready_threshold_percent": 50,
                  "readiness_dose_policy": "GOAL_ROLE_HALF_DEFICIT_BUILDING_60_PERCENT_MAINTENANCE", "diagnostic_reference_percent": 90,
                  "race_recovery_policy": "ADVISORY_ONLY_BELOW_90_PERCENT",
                  "allocation_policy": profile.get("_allocation_policy") or {},
                  "strength_exposure_policy": "WORK_AND_INTERSET_REST_EXCLUDING_WARMUP_COOLDOWN",
                  "strength_method_version": strength_methods.VERSION,
                  "strength_reduced_fraction_per_session": profile.get("strength_maintenance_fraction", .6),
                  "minimum_aerobic_dose_fraction": MIN_AEROBIC_DOSE_FRACTION,
                  "minimum_dose_policy": "NOMINAL_FRACTION_SCALED_BY_READINESS_WITH_COMPLETE_STRUCTURE",
                  "adaptive_methods_version": adaptive_methods.VERSION,
                  "load_progression": public_management(load_progression.public_context(progression)),
                  "building_fraction": continuous_building_fraction(profile), "maintenance_fraction": continuous_maintenance_fraction(profile),
                  "reentry_fraction": continuous_maintenance_fraction(profile), "reentry_dose_policy": "MAINTENANCE_ROLE_BUDGET", "recovery_session_cap_min": profile.get("recovery_session_cap_min", 30),
                  "weekly_volume_source": volume_source, "baseline_weekly_minutes": _round(weekly_minutes),
                  "planning_history": planning_evidence,
                  "historical_selected_weekly_minutes": _round(historical_weekly_minutes),
                  "historical_training_weekly_minutes": volume["historical_training_weekly_minutes"],
                  "available_weekly_minutes": sum(available) if availability_mode == "MANUAL" else None,
                  "availability_mode": availability_mode, "history_policy": history_policy, "training_sports": training_sports,
                  "planning_controls": controls, "volume_evidence": volume_evidence, "horizon": horizon_context,
                  "schedule_version": planning_schedule.VERSION, "max_sessions_per_day": 3,
                  "component_allocation_version": planning_allocation.VERSION,
                  "method_anchor_version": method_anchors.VERSION,
                  "weekly_minutes_ceiling": None if automatic_time else _round(weekly_ceiling),
                  "weekly_time_limit_minutes": controls["weekly_target_hours"]*60 if controls and controls.get("weekly_target_hours") is not None else None,
                  "time_budget": None if automatic_time else {
                      **({"basis": "RECORDED_HISTORY_INITIAL_ENVELOPE"} if estimated_initial_time else {}),
                      "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
                      "period_limit_minutes": _round(weekly_ceiling),
                      "actual_minutes": _round(actual_window_minutes),
                      "planned_minutes": _round(sum(s["total_minutes"] for s in planned_sessions)),
                      "remaining_minutes": _round(max(0., remaining)),
                      "actual_excess_minutes": _round(max(0., actual_window_minutes-weekly_ceiling)),
                  },
                  "volume_governor": "COMPONENT_7_40" if component_governed else "LIMITED_HISTORY_ENVELOPE",
                  "prescription_governor": "LONG_TERM_MICROCYCLE_Q" if component_governed else "LIMITED_HISTORY_ENVELOPE",
                  "index_envelope": {"version": planning_envelope.VERSION, "maximum_index": planning_envelope.INDEX_CEILING,
                                     "basis": "PROSPECTIVE_CANONICAL_E", "desired_index_is_ceiling": False},
                  "mesocycle_week_index": meso_week,
                  "mesocycle_factor": meso_factor, "mesocycle_length_weeks": meso_length,
                  "mesocycle_factor_policy": "COMPONENT_7_40_TARGETS" if component_governed else "ACTUAL_REFERENCE_BOUNDED_DEVELOPMENT_DELOAD_0.78",
                  "automatic_volume_progression": not component_governed, "component_target_modulation": component_governed,
                  "progression_percent": profile.get("progression_percent", 5), "target_reference": target_reference,
                  "targets_version": training_targets.VERSION, "reserved_key_dates": [d.isoformat() for d in key_slots],
                  "key_schedule_changes":key_schedule_changes,"consistency_version":planning_consistency.VERSION,
                  "taper_volume_factor": .7, "minimum_test_anchors": 2, "minimum_zone_index_activities": 3,
                  "max_index_age_days": speed_anchor_policy.MAX_INDEX_AGE_DAYS, "history_days_for_unrestricted_draft": planning_history.MINIMUM_DAYS,
                  "z1_working_band_width_bpm": Z1_WORKING_BAND_WIDTH_BPM,
                  "recovery_config_revision": configs["expected_revision"], "recovery_settings": configs["zones"],
                  "expert_continuous_capacity_upper_edge_min": {z: sum(r) / 120 for z, r in hr_speed.TMAX_RANGES_S.items()},
                  "canonical_effective_load": {
                      "version": COMPONENT_LOAD_VERSION, "scope": "ACTIVITY",
                      "basis": "ENTIRE_DIRECT_EQUIVALENT_LOAD", "recursive": False,
                      "neighbors": "IMMEDIATE_ONLY",
                      "downward_targets": "ALL_LOWER_AEROBIC_ZONES",
                      "downward_factors": dict(DOWNWARD_FACTORS),
                      "downward_threshold": None,
                      "coefficient_status": "COACH_EXPERT_STARTING_VALUES", "capacity": "CONTINUOUS_TMAX_AT_ZONE_REFERENCE",
                      "tiers": [{"minimum_ratio": .5, "maximum_ratio_inclusive": .8, "up": .2, "down": .1},
                                {"minimum_ratio_exclusive": .8, "up": .3, "down": .2}],
                      "contexts_by_sport": component_contexts}}
    provenance = {"generation_id": envelope.get("generation_id"), "revision": envelope.get("revision"),
                  "as_of": as_of, "history_days": history_days, "quality": quality,
                  "component_history_days": component_history_days,
                  "speed_model_version": speed_by_sport[primary_sport].get("model_version") if speed_by_sport[primary_sport] else None,
                  "speed_models_by_sport": {s: {"version": v.get("model_version"), "status": v.get("status"), "active_test_keys": v.get("active_test_keys", [])} if v else None for s,v in speed_by_sport.items()},
                  "dosing_models_by_sport": {s: {k: (v.get("dosing_model") or {}).get(k) for k in ("model_version", "status", "weights")} if v else None for s,v in speed_by_sport.items()},
                  "recovery_model_version": recovery_v2.VERSION, "method_catalog_version": METHODS_VERSION,
                  "method_library": method_library.provenance(),
                  "speed_active_test_keys": speed_by_sport[primary_sport].get("active_test_keys", []) if speed_by_sport[primary_sport] else [],
                  "readiness_known": all_sources_known, "known_history_only_forecast": not forecast_known,
                  "readiness_basis": "ESTIMATED_LOAD" if planning_evidence["estimated"] and forecast_known else "MEASURED_LOAD" if all_sources_known else "UNKNOWN",
                  "planning_history_supported": planning_history_supported, "planning_history": planning_evidence,
                  "snapshot_fingerprint": _hash(snapshot), "settings_fingerprint": _hash({"bounds": settings.zone_bounds_bpm, "hrmax": settings.hrmax_bpm, "timezone": settings.timezone})}
    parameters["speed_equivalence_comparison_version"] = plan_speed_comparison.VERSION
    speed_comparison = plan_speed_comparison.build(result_days, component_contexts,
        source=measured_source, start_date=start_date.isoformat(), end_date=end_date.isoformat())
    compact_plan_metadata({"days": result_days, "lookahead_days": lookahead_days})
    # Common speed-equivalence anchors/settings are retained once in the
    # per-sport comparison header. Runtime contexts stay complete and immutable.
    parameters["canonical_effective_load"]["contexts_by_sport"] = {
        sport: {**{key: value for key, value in context.items() if key != "comparison_parameters"},
                **({"comparison_parameters_ref": context["comparison_parameters"]["parameters_version"]}
                   if context.get("comparison_parameters") else {})}
        for sport, context in component_contexts.items()}
    fingerprint = _hash({"engine": VERSION, "source": provenance, "profile": profile, "events": events,
                         "preferences": preferences, "accents": accents, "parameters": parameters,
                         "speed": speed_by_sport, "start_date": start_date.isoformat()})
    all_blocked = all(d["status"] == "REVIEW_REQUIRED" for d in result_days)
    return {"schema_version": "planning-draft-v1", "engine_version": VERSION, "fingerprint": fingerprint,
            "status": "BLOCKED" if all_blocked else "LIMITED_DRAFT" if limited or blocked else "DRAFT",
            "generated_at": now.isoformat(), "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
            "activation_eligible": activation_eligible,
            "source": provenance, "periodization": periodization, "days": result_days,
            "lookahead_days": lookahead_days,
            "speed_equivalence_comparison": speed_comparison,
            "long_term": _long_term_outlook(profile, periodization, target_reference, accents, preferences, rows, today, limited, volume=volume, events=events, progression=progression, planning_evidence=planning_evidence),
            "parameters": parameters, "warnings": warnings, "catalog": method_registry.plan_catalog(profile),
            "allocation": allocation_report,
            "q_execution": planning_q.execution_report(source, today, [{
                "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
                "allocation": allocation_report}], include_history=True),
            "history_comparison": volume_evidence["weeks"],
            "component_history": load_progression.history(measured_source, _daily_rows(measured_source, today), today),
            "summary": {"sessions": len(planned_sessions),
                        "volume_by_microcycle": planning_schedule.microcycle_volume(source, result_days, meso_anchor,
                            program_start, program_end) if component_governed else [],
                        "key_sessions": sum(s.get("is_key_session", s["zone"] in {"Z3", "Z4", "Z5"}) for s in planned_sessions),
                        "actual_sessions": len(recorded_in_draft),
                        "actual_activity_records": len(existing_in_draft),
                        "actual_minutes": _round(actual_window_minutes),
                        "total_minutes": _round(actual_window_minutes+sum(s["total_minutes"] for s in planned_sessions)),
                        "planned_minutes": _round(sum(s["total_minutes"] for s in planned_sessions)),
                        "unused_weekly_minutes": None if automatic_time else _round(max(0., remaining)), "requires_review": True}}
