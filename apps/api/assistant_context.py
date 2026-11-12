"""Small read-only assistant projections of saved onFlows records.

No lifecycle/engine function is called here. Rights are supplied by a fresh
server-side authorization check; the caller must check them again before
returning a model response or performing a command.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from math import isfinite
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from biathlon import method_library
from biathlon.planning_schedule import day_sessions
from .body_observations import LabReport, WeightReport
from .management_plan_store import PlanStore
from .management_projection import public_management
from .management_store import ManagementStore
from .model_schemas import ManualSpeedTestInput
from .oauth_store import PersistentStoreFailure
from .response_monitoring import DailyReport, OptionalTest, ResponseBlock, SessionReport

VERSION = "assistant-context-v2"
from . import assistant_reads

QUERY_KINDS = frozenset({"day", "status", "history", "method", "inputs", "navigation"}) | assistant_reads.KINDS
PLAN_QUERY_KINDS = frozenset({"day", "method"}) | assistant_reads.PLAN_KINDS
INPUT_MODELS = {"DAILY": DailyReport, "WEIGHT": WeightReport, "SESSION": SessionReport,
                "TEST": OptionalTest, "LAB": LabReport, "BLOCK": ResponseBlock}
CARD_FIELDS = frozenset({"id", "title", "registry_kind", "registry_version", "sports", "periods", "family",
    "source", "original", "adaptation", "guidance", "provenance", "activation_status", "non_executable_reason",
    "automatic_selection", "prescription", "components", "coach_profile", "dosing", "dosing_ref"})
SESSION_FIELDS = frozenset({"method_id", "title", "sport", "zone", "purpose", "main_work_minutes", "total_minutes",
    "double_threshold", "paired_session", "mixed_component", "is_key_session", "registry_version",
    "canonical_effective_load", "direct_equivalent_minutes", "execution_range_policy", "race_course_guidance",
    "q_accounting", "neuromuscular_exposure"})
BLOCK_FIELDS = frozenset({"kind", "label", "zone", "duration_min", "duration_s", "target_hr_bpm", "target_speed_kmh",
    "repetition", "instructions", "primary_control", "speed_basis", "speed_role", "load_sport", "passive_rest",
    "recovery_type", "recovery_mode_hint", "reserve_repetitions", "repetitions", "exercise_id", "combination_part",
    "parent_method_id", "parent_method_card_ref", "hr_reference_range_bpm", "hr_reference_basis", "hr_role",
    "target_speed_range_kmh", "duration_range_seconds", "target_pace_range_seconds_km", "distance_range_m", "distance_basis",
    "lactate_reference", "initial_heuristic", "calibration_source", "calibration_eligible", "session_index"})
EVIDENCE_FIELDS = frozenset({"explanation", "limits", "fallback_reasons", "capacity_source", "capacity_minutes",
    "capacity_is_estimate", "within_observed_test_window", "supported_test_duration_s", "capacity_confidence",
    "capacity_reference", "model_version", "target_hr_bpm", "target_speed_kmh", "fraction", "base_fraction",
    "requested_work_minutes", "prescribed_work_minutes", "readiness_dose_factor", "dose_reduced", "effort_anchor",
    "effort_profile", "strength_prescription", "planned_gap_hours", "minimum_structure_work_minutes", "min_dose_fraction",
    "max_dose_fraction", "minimum_dose_scope", "applied_structure_fraction", "standard_method_work_minutes",
    "method_budget_role_fraction", "nominal_dose_policy", "method_dose_factors", "unrounded_requested_work_minutes",
    "zone_tmax_minutes", "zone_tmax_sources", "resolved_role", "reentry_dose_policy", "dosing", "speed_role",
    "initial_heuristic", "calibration_source", "target_speed_source", "target_speed_model_version"})
SNAPSHOT_FIELDS = frozenset({"generation_id", "analysis_revision", "analysis_as_of", "settings_fingerprint",
    "snapshot_fingerprint", "evaluation_date", "profile_revision", "engine_version", "speed_model_version"})


DAILY_SCALES = {
    "sleep_quality": ["Много добър", "Добър", "Среден", "Лош", "Много лош"],
    "fatigue": ["Без умора", "Лека", "Умерена", "Силна", "Много силна"],
    "soreness": ["Няма", "Лека", "Умерена", "Силна", "Много силна"],
    "stress": ["Много нисък", "Нисък", "Умерен", "Висок", "Много висок"],
    "motivation": ["Много силно желание", "Силно", "Средно", "Слабо", "Липсва"],
    "competition_motivation": ["Много силно", "Силно", "Средно", "Слабо", "Липсва"],
}


def input_contract(kind):
    """Only the selected canonical input schema, not the scientific catalog."""
    from .assistant_app_actions import APP_COMMAND_KINDS, input_contract as app_contract
    if kind in APP_COMMAND_KINDS:
        return app_contract(kind)
    result = {"patch_rule": "Omitted fields are retained; null explicitly clears only nullable fields. Defaults do not authorize inferring an observation."}
    if kind == "MANAGEMENT_PROFILE":
        from .assistant_planning_commands import PROFILE_FIELDS, CONTROL_FIELDS
        from .management_schemas import ManagementProfile
        schema = ManagementProfile.model_json_schema()
        return {**result, "allowed_fields": sorted(PROFILE_FIELDS), "planning_controls_fields": sorted(CONTROL_FIELDS),
            "properties": {key: value for key, value in schema.get("properties", {}).items() if key in PROFILE_FIELDS},
            "required_for_new_record": schema.get("required", []), "$defs": schema.get("$defs", {}),
            "reset_rule": "planning_controls:null restores the canonical default controls. method_library.overrides:{} clears all overrides; {method_id:null} removes only the named override. Non-empty partial overrides preserve unspecified controls.",
            "plan_change": "Updates inputs; does not approve or replace the saved programme."}
    if kind == "CALENDAR_EVENT":
        from .schemas import PlanningCalendarEvent
        model = PlanningCalendarEvent
        result["extra_patch_fields"] = {"include_identical_copies": "true includes only exact duplicate entries of the selected existing event in the shown preview; omit to edit only its entry_key"}
    else:
        model = ManualSpeedTestInput if kind == "MANUAL_SPEED_TEST" else INPUT_MODELS.get(kind)
    if model is None:
        return result
    schema = model.model_json_schema()
    server_owned = {"expected_revision", "event_id", "test_id", "sample_id", "input_method"}
    properties = {key: value for key, value in schema.get("properties", {}).items() if key not in server_owned}
    result.update({"properties": properties, "required_for_new_record": [field for field in schema.get("required", []) if field not in server_owned],
        "identity_rule": "Use the exact entry_key and revision from this read. For a new calendar event use the server-provided new_input_keys.CALENDAR_EVENT. New observation/test keys may remain empty for canonical allocation. Never request or invent test_id/sample_id/event_id/input_method.",
        **({"$defs": schema["$defs"]} if schema.get("$defs") else {})})
    if kind == "DAILY":
        result["scales_1_to_5"] = deepcopy(DAILY_SCALES)
        result["units"] = {"sleep_hours": "h"}
    elif kind == "WEIGHT":
        result["units"] = {"morning_kg": "kg", "before_kg": "kg", "after_kg": "kg", "fluid_l": "L", "urine_l": "L"}
        result["standardization"] = "morning_standardized and comparable require explicit measurement context."
    elif kind == "SESSION":
        result["scales"] = {"rpe": "0–10", "feel": ["Много добре", "Добре", "Средно", "Зле", "Много зле"]}
        result["units"] = {"duration_minutes": "min", "planned_speed_kmh": "km/h", "executed_speed_kmh": "km/h"}
        result["entry_guidance"] = "Use a completed activity, not a planned session. Reuse its saved duration or suggested_duration_minutes. Ask only which activity if ambiguous and when the rating was made if timing is unknown. Do not request optional feel, lactate, speed, method, or notes for an RPE-only entry."
    elif kind == "MANUAL_SPEED_TEST":
        result["units"] = {"duration_s": "s", "distance_m": "m", "speed_kmh": "km/h"}
        result["requires_attestation"] = ["maximal", "comparable", "flat_terrain"]
    return result


def _pick(value, keys):
    return {key: deepcopy(item) for key, item in value.items() if key in keys} if isinstance(value, dict) else {}


def _date(value, default):
    if value is None:
        return default
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise HTTPException(422, "Посочи точна календарна дата.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, "Невалидна календарна дата.") from exc


def _require(access, key):
    if access.get(key) is not True:
        raise HTTPException(403, "Нямаш достъп до тези данни за спортиста.")


def recovery_display_state(history):
    """The boolean policy of web/lib/recovery-display.ts, without raw numbers."""
    quality = history.get("data_quality") if isinstance(history, dict) else None
    quality = quality if isinstance(quality, dict) else {}
    estimation = quality.get("estimation") or {}
    estimated = (quality.get("readiness_available") is True
        and quality.get("readiness_basis") == "ESTIMATED_LOAD"
        and quality.get("estimated") is True
        and quality.get("missing_days") == 0
        and estimation.get("supported") is True
        and estimation.get("unresolved_activity_count") == 0
        and estimation.get("missing_calendar_days") == [])
    unavailable = quality.get("readiness_available") is False or (quality.get("complete") is False and not estimated)
    return {"estimated": estimated, "unavailable": unavailable}


def present_recovery(history):
    """Remove inadmissible numbers before model context, including forecasts."""
    if not isinstance(history, dict):
        return {"availability": "UNAVAILABLE", "reason": "RECOVERY_NOT_PREPARED"}
    state = recovery_display_state(history)
    quality = history.get("data_quality")
    common = {"basis": "MODELLED_DAILY_LOAD_RECOVERY", "is_measurement": False,
        **_pick(history, {"schema_version", "as_of", "source_as_of", "source_stale", "model", "config_revision", "time_resolution"})}
    recorded = (isinstance(quality, dict) and quality.get("complete") is True
        and quality.get("estimated") is not True
        and quality.get("readiness_available") is not False
        and quality.get("readiness_basis") in {None, "RECORDED_LOAD"})
    # Legacy numeric output without quality, or contradictory explicit quality,
    # does not establish admissibility even if it contains plausible numbers.
    if state["unavailable"] or not (state["estimated"] or recorded):
        return {**common, "availability": "UNAVAILABLE", "reason": "INCOMPLETE_LOAD_COVERAGE" if quality else "QUALITY_NOT_AVAILABLE",
            "quality": _pick(quality, {"complete", "missing_days", "partial_activities", "readiness_basis", "readiness_available", "covered_days", "expected_days"})}
    rows = []
    for current in history.get("current", []):
        if not isinstance(current, dict):
            continue
        number = current.get("readiness_percent")
        if isinstance(number, (int, float)) and not isinstance(number, bool) and isfinite(number) and 0 <= number <= 100:
            rows.append(_pick(current, {"zone", "readiness_percent", "days_to_practical_recovery", "baseline_source", "history_days"}))
    if not rows:
        return {**common, "availability": "UNAVAILABLE", "reason": "RECOVERY_NOT_PREPARED"}
    return {**common, "availability": "ESTIMATED" if state["estimated"] else "STALE" if history.get("source_stale") else "AVAILABLE",
        "estimated": state["estimated"], "quality": _pick(quality, {"complete", "readiness_basis", "missing_days", "covered_days", "expected_days"}),
        "current": rows}


def _card(value):
    card = _pick(value, CARD_FIELDS)
    if isinstance(card.get("components"), list):
        card["components"] = [_card(c) for c in card["components"] if isinstance(c, dict)]
    card["source_status"] = (card.get("provenance") or {}).get("status", "NOT_SPECIFIED")
    card["is_executable_recipe"] = bool(card.get("prescription") or card.get("registry_kind") == "COACH_PROFILE")
    return card


def _session(value, identity, access):
    result = {**_pick(value, SESSION_FIELDS), "session_id": identity}
    result["blocks"] = []
    for block in value.get("blocks", []):
        if not isinstance(block, dict):
            continue
        shown = _pick(block, BLOCK_FIELDS)
        for key in ("method_library", "parent_method_card"):
            if isinstance(block.get(key), dict):
                shown[key] = _card(block[key])
        result["blocks"].append(shown)
    if isinstance(value.get("method_library"), dict):
        result["method_library"] = _card(value["method_library"])
    if access.get("can_view_recovery") is True:
        evidence = _pick(public_management(value.get("dose_evidence") or {}), EVIDENCE_FIELDS)
        result["dose_evidence"] = {"availability": "AVAILABLE" if evidence else "UNAVAILABLE", **evidence}
    else:
        result["dose_evidence"] = {"availability": "UNAVAILABLE", "reason": "RECOVERY_ACCESS_REQUIRED"}
    return result


def _plan_day(repository, alias, access, selected, today):
    store = PlanStore(repository)
    day_reader = getattr(store, "assistant_day", None)
    record = day_reader(alias, selected) if callable(day_reader) else store.current(alias)
    if record is None:
        return {"availability": "UNAVAILABLE", "reason": "NO_APPROVED_PLAN", "date": selected.isoformat(), "sessions": []}
    payload = record["payload"]
    plan = payload.get("plan")
    common = {"plan_revision": record["revision"], "recorded_at": record.get("recorded_at"),
        "plan_status": payload.get("status"), "proposal_available": isinstance(payload.get("proposal"), dict)}
    if not isinstance(plan, dict):
        return {**common, "availability": "UNAVAILABLE", "reason": "NO_APPROVED_PLAN", "date": selected.isoformat(), "sessions": []}
    common.update({"engine_version": plan.get("engine_version"), "plan_fingerprint": plan.get("fingerprint"),
        "input_snapshot": _pick(plan.get("input_snapshot"), SNAPSHOT_FIELDS), "generated_at": plan.get("generated_at"),
        "start_date": plan.get("start_date"), "end_date": plan.get("end_date"),
        "source": _pick(plan.get("source"), SNAPSHOT_FIELDS), "kind": "HISTORICAL_APPROVED" if selected < today else "APPROVED"})
    day = next((d for d in plan.get("days", []) if isinstance(d, dict) and d.get("date") == selected.isoformat()), None)
    if day is None:
        return {**common, "availability": "UNAVAILABLE", "reason": "DATE_NOT_IN_APPROVED_PLAN", "date": selected.isoformat(), "sessions": []}
    result = {**common, "availability": "AVAILABLE", "date": selected.isoformat(), "day_status": day.get("status"),
        "sessions": [_session(s, f"{selected.isoformat()}:{index + 1}", access) for index, s in enumerate(day_sessions(day)) if isinstance(s, dict)]}
    if access.get("can_view_recovery") is True:
        result["day_explanation"] = day.get("explanation")
    if payload.get("status") == "PAUSED":
        result["prescription_active"] = False
    else:
        result["prescription_active"] = payload.get("status") in {"ACTIVE", "REVIEW_REQUIRED"}
    return result


def _dashboard(repository, alias):
    operation = getattr(repository, "dashboard_preparation", None)
    if callable(operation):
        # READ only: this does not invoke the fallback calculator used by some
        # offline HTTP adapters. A cold assistant never computes a model.
        from .prepared_models import model_key
        state = operation("READ", alias=alias, model_key=model_key())
        envelope = state.get("envelope") if isinstance(state, dict) and state.get("ready") is True else None
        if envelope is None:
            return None
    else:
        operation = getattr(repository, "active_analysis", None)
        envelope = operation(alias) if callable(operation) else None
    if not isinstance(envelope, dict) or not isinstance(envelope.get("snapshot_payload"), dict):
        raise PersistentStoreFailure("Invalid assistant snapshot")
    for field in ("training_status", "load_history", "recovery_history"):
        value = envelope["snapshot_payload"].get(field)
        if isinstance(value, dict) and value.get("athlete_id", alias) != alias:
            raise PersistentStoreFailure("Assistant snapshot scope differs")
    return envelope


def _source(envelope, today):
    as_of = envelope.get("analysis_as_of") or (envelope.get("snapshot_payload", {}).get("training_status") or {}).get("as_of")
    return {"generation_id": envelope.get("generation_id"), "analysis_revision": envelope.get("revision"),
        "as_of": as_of, "activated_at": envelope.get("activated_at"), "freshness": "STALE" if isinstance(as_of, str) and as_of[:10] < today.isoformat() else "CURRENT" if isinstance(as_of, str) else "UNKNOWN"}


def _status(repository, alias, today):
    envelope = _dashboard(repository, alias)
    if envelope is None:
        return {"availability": "PENDING", "reason": "PREPARED_ANALYSIS_PENDING"}
    snapshot = envelope["snapshot_payload"]
    status = snapshot.get("training_status") or {}
    # Recovery percentages in legacy training_status obey the same admissibility
    # gate as recovery_history; never expose duplicate unsanitized fields.
    zones = [_pick(row, {"zone", "raw_time_min", "equivalent_time_min", "tref_min", "status_7_40"})
        for row in status.get("zones", []) if isinstance(row, dict)]
    return {"availability": "AVAILABLE", **_source(envelope, today), "model": status.get("model"),
        "data_quality": status.get("data_quality"), "zones": zones,
        "recovery": present_recovery(snapshot.get("recovery_history")),
        "units": {"raw_time_min": "CLOCK_MINUTES", "equivalent_time_min": "Q_EQUIVALENT_MINUTES", "status_7_40": "RATIO_7_TO_40"}}


def _history(repository, alias, start, end, today):
    from biathlon.learning_provenance import heuristic_load_dates

    envelope = _dashboard(repository, alias)
    if envelope is None:
        return {"availability": "PENDING", "reason": "PREPARED_ANALYSIS_PENDING", "start": start.isoformat(), "end": end.isoformat()}
    source = envelope["snapshot_payload"].get("load_history") or {}
    first, last = start.isoformat(), end.isoformat()
    estimated_days = sorted(day for day in heuristic_load_dates(source) if first <= day <= last)
    fields = {"date", "zone", "raw_time_min", "equivalent_time_min", "effective_load", "e7_daily", "e40_daily", "tref_used_min", "real_time_min", "status_7_40", "coverage", "estimated", "source", "status"}
    daily = [_pick(row, fields) for row in source.get("daily", []) if isinstance(row, dict) and first <= str(row.get("date", "")) <= last]
    for row in daily:
        if row.get("date") in estimated_days:
            row.update(estimated=True, estimation_basis="INITIAL_HEURISTIC")
    strength = [_pick(row, fields) for row in (source.get("strength") or {}).get("daily", []) if isinstance(row, dict) and first <= str(row.get("date", "")) <= last]
    activity_rows = [row for row in source.get("activities", [])
        if isinstance(row, dict) and first <= str(row.get("date", "")) <= last]
    activities = [_pick(row, {"activity_ref", "date", "sport", "duration_min", "quality_status", "hr_coverage_percent"})
        for row in activity_rows]
    source_fields = {"version", "equivalence_version", "leading_indicator", "profile_version", "speed_role",
        "initial_heuristic", "calibration_source", "calibration_eligible", "estimated_descent_seconds"}
    for activity, row in zip(activities, activity_rows):
        if isinstance(row.get("load_source"), dict):
            activity["load_source"] = _pick(row["load_source"], source_fields)
    observed = {row.get("date") for row in daily}
    return {"availability": "AVAILABLE" if daily else "UNAVAILABLE", **_source(envelope, today), "start": first, "end": last,
        "source_period_start": source.get("period_start"), "source_period_end": source.get("period_end"),
        "quality": source.get("quality"), "daily": daily, "strength": strength, "activities": activities,
        "estimated_days": estimated_days,
        "unobserved_days": [(start + timedelta(days=n)).isoformat() for n in range((end - start).days + 1) if (start + timedelta(days=n)).isoformat() not in observed],
        "missing_day_means_rest": False, "units": {"raw_time_min": "CLOCK_MINUTES", "equivalent_time_min": "Q_EQUIVALENT_MINUTES", "effective_load": "E_EFFECTIVE_LOAD"}}


def _entry(repository, alias, kind, key):
    path = "/onflows_response_entries?select=kind,entry_key,revision,payload,recorded_at"
    path += f"&athlete_alias=eq.{quote(alias, safe='')}&kind=eq.{kind}&entry_key=eq.{quote(key, safe='')}&order=revision.desc&limit=1"
    rows = repository._json(repository._request("GET", path))
    if not isinstance(rows, list) or len(rows) > 1 or any(not isinstance(r, dict) or r.get("kind") != kind or r.get("entry_key") != key or type(r.get("revision")) is not int or r["revision"] < 1 or not isinstance(r.get("payload"), dict) for r in rows):
        raise PersistentStoreFailure("Invalid assistant input record")
    return {"kind": kind, "entry_key": key, "revision": rows[0]["revision"] if rows else 0,
        "payload": _pick(rows[0]["payload"], set(INPUT_MODELS[kind].model_fields) - {"expected_revision"}) if rows else None}


def _inputs(repository, alias, access, query, today):
    kind = query.get("input_kind")
    key = query.get("key") or query.get("entry_key")
    from .assistant_app_actions import APP_COMMAND_KINDS, PLAN_KINDS, read_input
    if kind in APP_COMMAND_KINDS:
        _require(access, "can_edit_plan" if kind == "ATHLETE_SETTINGS" else "can_view_plan" if kind in PLAN_KINDS else "can_view_recovery")
        selectors = {"draft_date": query.get("date") or query.get("start"), "draft_revision": query.get("revision")} if kind == "PLAN_ACTIVATE" else {}
        return read_input(repository, alias, kind, key, **selectors)
    if kind is None:
        _require(access, "can_view_recovery")
        key = key or today.isoformat()
        _date(key, today)
        return {"availability": "AVAILABLE", "entries": [_entry(repository, alias, kind, key) for kind in ("DAILY", "WEIGHT")],
            "allowed_kinds": ["DAILY", "WEIGHT", "SESSION", "TEST", "LAB", "BLOCK", "MANUAL_SPEED_TEST", "MANAGEMENT_PROFILE", "CALENDAR_EVENT"]}
    if kind == "MANAGEMENT_PROFILE":
        _require(access, "can_view_plan")
        profile = ManagementStore(repository).profile(alias)
        # Same editable configuration as the application. Computed evidence is
        # absent because only canonical ManagementProfile fields are selected.
        from .assistant_planning_commands import PROFILE_FIELDS, CONTROL_FIELDS
        shown = _pick(profile.get("profile"), PROFILE_FIELDS)
        if isinstance(shown.get("planning_controls"), dict):
            shown["planning_controls"] = _pick(shown["planning_controls"], CONTROL_FIELDS)
        return {"availability": "AVAILABLE", "kind": kind, "entry_key": "management-profile-v1", "revision": profile["revision"], "payload": shown}
    if kind == "CALENDAR_EVENT":
        _require(access, "can_view_plan")
        from .assistant_planning_commands import calendar_state, identical_event_ids
        state = calendar_state(repository, alias)
        events = state["payload"].get("events", [])
        if key is not None and (not isinstance(key, str) or len(key) > 128):
            raise HTTPException(422, "Невалидно календарно събитие.")
        event = next((e for e in events if e.get("event_id") == key), None) if key else None
        return {"availability": "AVAILABLE", "kind": kind, "entry_key": key, "revision": state["revision"],
            "calendar_revision": state["revision"], "payload": deepcopy(event) if key else None,
            "identical_copy_count": len(identical_event_ids(events, event)) if key else None,
            "events": deepcopy(events) if not key else None,
            "entries": [{"kind": kind, "entry_key": e["event_id"], "revision": state["revision"],
                "payload": deepcopy(e), "identical_copy_count": len(identical_event_ids(events, e))} for e in events] if not key else None}
    if kind == "MANUAL_SPEED_TEST":
        _require(access, "can_view_recovery")
        if key in (None, ""):
            return {"availability": "AVAILABLE", "kind": kind, "entry_key": "", "revision": 0, "payload": None, "creating": True}
        if not isinstance(key, str) or not re.fullmatch(r"manual_[a-f0-9]{32}", key):
            raise HTTPException(422, "Посочи точния максимален тест.")
        path = "/onflows_model_entries?select=kind,entry_key,revision,payload,recorded_at"
        path += f"&athlete_alias=eq.{quote(alias, safe='')}&kind=eq.SPEED_TEST&entry_key=eq.{key}&order=revision.desc&limit=1"
        rows = repository._json(repository._request("GET", path))
        if not isinstance(rows, list) or len(rows) > 1 or any(not isinstance(r, dict) or r.get("kind") != "SPEED_TEST" or r.get("entry_key") != key or type(r.get("revision")) is not int or r["revision"] < 1 or not isinstance(r.get("payload"), dict) or r["payload"].get("source") != "MANUAL" for r in rows):
            raise PersistentStoreFailure("Invalid assistant manual test record")
        shown = _pick(rows[0]["payload"], set(ManualSpeedTestInput.model_fields) - {"expected_revision"}) if rows else None
        if shown:
            shown["speed_kmh" if rows[0]["payload"].get("measurement_input") == "DISTANCE" else "distance_m"] = None
        return {"availability": "AVAILABLE", "kind": kind, "entry_key": key, "revision": rows[0]["revision"] if rows else 0, "payload": shown}
    if kind not in INPUT_MODELS:
        raise HTTPException(422, "Неподдържан вид входни данни.")
    _require(access, "can_view_recovery")
    if kind == "SESSION" and key in (None, ""):
        selected = _date(query.get("date"), today)
        if selected > today:
            raise HTTPException(422, "Оценката след тренировка изисква изпълнена активност.")
        # The same activity calendar and response entries used by the form;
        # no training engine or planned-session identifier is involved.
        compact_reader = getattr(repository, "_rpc_row", None)
        if callable(compact_reader):
            # Same generation-pinned calendar as the form, without transferring
            # the full 90-day scientific snapshot for a single RPE entry.
            calendar = compact_reader("active_onflows_activity_calendar?select=generation_id,revision,activities", {
                "p_athlete_alias": alias, "p_period_start": selected.isoformat(), "p_period_end": selected.isoformat()}) or {}
        else:
            calendar = repository.active_activity_calendar(alias, selected, selected) or {}
        if not isinstance(calendar, dict) or not isinstance(calendar.get("activities", []), list):
            raise PersistentStoreFailure("Invalid assistant session calendar")
        activities = [a for a in calendar.get("activities", []) if isinstance(a, dict) and a.get("local_date") == selected.isoformat()]
        entries = []
        for activity in activities[:20]:
            entry = _entry(repository, alias, kind, activity["activity_ref"])
            seconds = activity.get("elapsed_time_s")
            entry["activity"] = _pick(activity, {"activity_ref", "local_date", "name", "sport"})
            entry["suggested_duration_minutes"] = seconds / 60 if isinstance(seconds, (int, float)) and 0 < seconds <= 86400 else None
            entries.append(entry)
        return {"availability": "AVAILABLE", "kind": kind, "date": selected.isoformat(), "entries": entries,
            "activity_count": len(activities), "truncated": len(activities) > 20}
    if key is None and kind in {"DAILY", "WEIGHT"}:
        key = today.isoformat()
    if key in (None, "") and kind in {"TEST", "LAB", "BLOCK"}:
        return {"availability": "AVAILABLE", "kind": kind, "entry_key": "", "revision": 0, "payload": None, "creating": True}
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise HTTPException(422, "Посочи точния запис или активност.")
    if kind in {"DAILY", "WEIGHT"}:
        _date(key, today)
    return {"availability": "AVAILABLE", **_entry(repository, alias, kind, key)}


def read(repository, alias, access, query, now=None, *, actor=None):
    """Read one registered capability; never accept a model-supplied scope."""
    if not isinstance(query, dict) or query.get("kind") not in QUERY_KINDS:
        raise HTTPException(422, "Неподдържана справка.")
    allowed = {"kind", "date", "start", "end", "session_id", "method_id", "input_kind", "key", "entry_key"} | assistant_reads.PARAMETERS
    if any(key not in allowed for key in query):
        raise HTTPException(422, "Неподдържани параметри на справката.")
    # Strict provider schemas include every optional query field as null. Reads
    # treat those as omitted so canonical defaults apply (pagination, period,
    # interval start). This normalization must never touch command patches,
    # where null is an explicit request to clear a nullable input.
    query = {key: value for key, value in query.items() if value is not None}
    from .assistant_app_actions import PLAN_KINDS
    if query["kind"] == "navigation":
        if not (access.get("can_view_plan") or access.get("can_view_recovery")):
            raise HTTPException(403, "Нямаш достъп до данните на този спортист.")
        from .assistant_navigation import read as navigation
        return {"schema_version": VERSION, "query_kind": "navigation", **navigation(access, query.get("key") or "")}
    if query["kind"] == "inputs" and query.get("input_kind") == "ATHLETE_SETTINGS":
        _require(access, "can_edit_plan")
    elif query["kind"] in PLAN_QUERY_KINDS or query["kind"] == "inputs" and query.get("input_kind") in {"MANAGEMENT_PROFILE", "CALENDAR_EVENT", *PLAN_KINDS}:
        _require(access, "can_view_plan")
    else:
        _require(access, "can_view_recovery")
    settings = repository.athlete_settings(alias)
    if settings is None:
        return {"availability": "UNAVAILABLE", "reason": "ATHLETE_SETTINGS_REQUIRED"}
    tz = settings.timezone
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(tz)).date()
    kind = query["kind"]
    if kind in assistant_reads.KINDS:
        result = assistant_reads.read(repository, alias, access, query, settings, today, _date, actor=actor)
    elif kind in {"day", "method"}:
        _require(access, "can_view_plan")
        selected = _date(query.get("date"), today)
        result = _plan_day(repository, alias, access, selected, today)
        if kind == "method":
            identifier = query.get("session_id")
            method_id = query.get("method_id")
            sessions = [s for s in result["sessions"] if (not identifier or s["session_id"] == identifier) and (not method_id or s.get("method_id") == method_id)]
            if sessions:
                result["sessions"] = sessions
                result["method_source"] = "FROZEN_SESSION_CARD"
            elif method_id:
                entry = next((m for m in method_library.entries() if m["id"] == method_id), None)
                result = {"availability": "AVAILABLE" if entry else "UNAVAILABLE", "method_source": "CURRENT_CATALOG_REFERENCE",
                    "is_individual_prescription": False, "catalog_version": method_library.VERSION,
                    "method": _card(entry) if entry else None, "reason": None if entry else "METHOD_NOT_FOUND"}
            else:
                result = {**result, "availability": "UNAVAILABLE", "reason": "SAVED_SESSION_NOT_FOUND", "sessions": []}
    elif kind == "status":
        _require(access, "can_view_recovery")
        result = _status(repository, alias, today)
    elif kind == "history":
        _require(access, "can_view_recovery")
        end = _date(query.get("end"), today)
        start = _date(query.get("start"), end - timedelta(days=13))
        if start > end or end > today or (end - start).days >= 90:
            raise HTTPException(422, "Избери минал или текущ период до 90 дни.")
        result = _history(repository, alias, start, end, today)
    else:
        result = _inputs(repository, alias, access, query, today)
    if kind == "inputs" and isinstance(result.get("kind"), str):
        result["input_kind"] = result["kind"]
        result["input_contract"] = input_contract(result["kind"])
    return {"schema_version": VERSION, "query_kind": kind, "timezone": tz, "today": today.isoformat(), **result}
