"""Semantic planning summaries for explanatory conversations.

This is a presentation of saved plans, never a second planning engine. Numeric
ranges describe the saved weeks; they are not a new dose or a prediction. The
normal permission projection must run before these helpers for plan-only users.
Detailed, versioned source fields remain addressable through section reads.
"""
from copy import deepcopy
from math import isfinite


COMPONENTS = ("Z1", "Z2", "Z3", "Z4", "Z5", "STR")


def _pick(value, fields):
    return {key: deepcopy(item) for key, item in value.items() if key in fields} if isinstance(value, dict) else {}


def _rows(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _range(values):
    values = [value for value in values if type(value) in (int, float) and isfinite(value)]
    return [min(values), max(values)] if values else None


def _events(value, limit=40):
    rows = _rows(value)
    selected = [_pick(row, {"event_id", "event_type", "name", "start_date", "end_date", "note_bg"}) for row in rows]
    return {"events": selected[:limit], "total_events": len(selected), "omitted_events": max(0, len(selected) - limit)}


def _profile(value):
    if not isinstance(value, dict):
        return None
    if "profile" in value or "configured" in value:
        return {**_pick(value, {"configured", "revision", "today", "timezone"}), "profile": _profile(value.get("profile"))}
    result = _pick(value, {"schema_version", "sport", "actual_sport", "discipline", "race_duration_min",
        "program_start", "program_end", "horizon_mode", "availability_mode", "training_days", "available_minutes",
        "recent_weekly_hours", "reentry_days", "taper_days", "transition_days", "max_key_sessions_per_week",
        "building_fraction", "maintenance_fraction", "reentry_fraction", "recovery_session_cap_min",
        "adaptation_mode", "progression_percent", "strength_enabled", "component_targets_weekly"})
    controls = value.get("planning_controls")
    if isinstance(controls, dict):
        result["planning_controls"] = _pick(controls, {"sessions_per_week", "sessions_by_day", "threshold_days",
            "threshold_method", "double_threshold_days", "double_threshold_components", "double_threshold_fraction",
            "double_threshold_gap_hours", "intensity_days", "strength_days", "long_session_day", "training_sports",
            "weekly_target_hours", "capacity_policy", "mesocycle_anchor", "wave", "accent_mode", "accent_limit",
            "automatic_focus_count", "ranked_indices", "shock_indices", "accents", "accent_index", "maintenance_index"})
        cycles = _rows(controls.get("cycles"))
        result["planning_controls"]["cycles"] = [_pick(row, {"start_date", "end_date", "name", "kind", "accents",
            "target_index", "volume_factor", "recovery_days"}) for row in cycles[:12]]
        result["planning_controls"]["cycle_coverage"] = {"total": len(cycles), "omitted": max(0, len(cycles) - 12)}
    for name, fields in (("load_progression", {"enabled", "low_volume_annual_percent", "upper_volume_annual_percent",
            "precompetition_factor", "competition_factor", "feedback_enabled", "training_level"}),
            ("individual_learning", {"mode", "exploration_enabled", "max_volume_step_percent", "max_intensity_step"}),
            ("training_return", {"mode", "pause_start", "resume_date", "historical_weekly_q"})):
        if isinstance(value.get(name), dict):
            result[name] = _pick(value[name], fields)
    methods = value.get("method_library")
    if isinstance(methods, dict):
        result["method_library"] = _pick(methods, {"enabled"})
        overrides = methods.get("overrides") or {}
        if isinstance(overrides, dict):
            result["method_library"]["overridden_method_count"] = len(overrides)
    return result


def _week(row):
    result = _pick(row, {"start_date", "end_date", "days", "phases", "accents", "mesocycle_weeks",
        "volume_budget_minutes", "volume_role"})
    if isinstance(row.get("cycle"), dict):
        result["cycle"] = _pick(row["cycle"], {"kind", "week", "name", "label", "factor", "volume_factor", "intensity_factor"})
    result["components"] = {zone: _pick((row.get("components") or {}).get(zone), {"target_weekly_q", "target_index_7_40"})
        for zone in COMPONENTS if isinstance((row.get("components") or {}).get(zone), dict)}
    return result


def _phase_ranges(weeks):
    """Summarize contiguous saved weekly rows, keeping mixed-phase boundaries."""
    groups = []
    for week in weeks:
        phases = tuple(week.get("phases") or [])
        if groups and groups[-1][0] == phases:
            groups[-1][1].append(week)
        else:
            groups.append((phases, [week]))
    result = []
    for phases, rows in groups:
        accents = list(dict.fromkeys(zone for row in rows for zone in (row.get("accents") or [])))
        cycles = list(dict.fromkeys(str((row.get("cycle") or {}).get("kind")) for row in rows if (row.get("cycle") or {}).get("kind")))
        result.append({"start_date": rows[0].get("start_date"), "end_date": rows[-1].get("end_date"),
            "phases": list(phases), "week_count": len(rows), "accents": accents, "cycle_kinds": cycles,
            "volume_budget_minutes_range": _range(row.get("volume_budget_minutes") for row in rows),
            "volume_roles": list(dict.fromkeys(row["volume_role"] for row in rows if row.get("volume_role"))),
            "components": {zone: {"target_weekly_q_range": _range((row.get("components") or {}).get(zone, {}).get("target_weekly_q") for row in rows),
                "target_index_7_40_range": _range((row.get("components") or {}).get(zone, {}).get("target_index_7_40") for row in rows),
                "unknown_q_weeks": sum((row.get("components") or {}).get(zone, {}).get("target_weekly_q") is None for row in rows),
                "unknown_index_weeks": sum((row.get("components") or {}).get(zone, {}).get("target_index_7_40") is None for row in rows)} for zone in COMPONENTS}})
    return result


def _long_term(value):
    result = _pick(value, {"schema_version", "as_of", "basis", "targets_version", "reference_cutoff", "limited",
        "goal_role", "readiness_forecast", "automatic_camp_load_increase"})
    weeks = _rows(value.get("weeks"))
    # A repeated compaction must not replace full-horizon aggregates with the
    # already reduced sample. This also supports a provider budget recovery.
    if value.get("summary_scope") == "SAVED_WEEKLY_TARGETS_NOT_A_PRESCRIPTION":
        return deepcopy(value)
    result["weeks"] = [_week(row) for row in weeks[:4]]
    result["phase_ranges"] = _phase_ranges(weeks)
    result["week_coverage"] = {"total": len(weeks), "exact_rows_returned": min(4, len(weeks)),
        "summarized_rows": max(0, len(weeks) - 4), "range_scope": "All saved weekly rows, including the exact rows above; do not sum ranges or interpret them as every week's dose."}
    result["summary_scope"] = "SAVED_WEEKLY_TARGETS_NOT_A_PRESCRIPTION"
    # These fields have already been removed for plan-only permissions. Retain
    # compact actual/estimated baselines for readers who can view the evidence.
    if isinstance(value.get("baseline"), dict):
        result["baseline"] = {zone: _pick(value["baseline"].get(zone), {"b50", "c40", "known", "estimated",
            "actual_index_7_40", "estimated_index_7_40"}) for zone in COMPONENTS if zone in value["baseline"]}
    if isinstance(value.get("planning_history"), dict):
        result["planning_history"] = _pick(value["planning_history"], {"supported", "estimated", "reason", "scope", "missing_days", "covered_days"})
    if isinstance(value.get("progression"), dict):
        progression = value["progression"]
        result["progression"] = _pick(progression, {"version", "basis", "as_of", "cycle_start", "cycle_days",
            "history_usable", "estimated_history_supported", "percentages_are_coaching_parameters",
            "normative_role", "overall_basis", "requires_catchup"})
        result["progression"]["components"] = {zone: _pick((progression.get("components") or {}).get(zone),
            {"annual_rate_percent", "governed_annual_rate_percent", "reference_q", "current_observed_q",
             "recent_observed_q", "recent_estimated_q", "planned_growth_percent"}) for zone in COMPONENTS
            if isinstance((progression.get("components") or {}).get(zone), dict)}
    return result


def _periodization(value):
    result = _pick(value, {"engine_version", "entry_basis"})
    if isinstance(value.get("parameters"), dict):
        result["parameters"] = _pick(value["parameters"], {"source", "date_ranges", "taper_is_overlay"})
    fields = {"kind", "label_bg", "start_date", "end_date", "days", "main_race_event_id", "event_id",
        "event_name", "reason", "note_bg", "volume_factor", "load_factor", "prevent_deficit_refill",
        "planned_days", "requested_days"}
    for name in ("phases", "taper_windows", "reentry_windows", "warnings"):
        result[name] = [_pick(row, fields | {"code", "message"}) for row in _rows(value.get(name))
            if row.get("code") != "DRAFT_REQUIRES_COACH_REVIEW"]
    events = _rows(value.get("calendar_context"))
    # Prioritize every primary race over lower-priority calendar context.
    result["main_races"] = deepcopy(value["main_races"]) if isinstance(value.get("main_races"), dict) else _events([row for row in events if row.get("event_type") == "MAIN_RACE"])
    result["other_events"] = deepcopy(value["other_events"]) if isinstance(value.get("other_events"), dict) else _events([row for row in events if row.get("event_type") != "MAIN_RACE"], 16)
    if isinstance(value.get("next_main_race"), dict):
        result["next_main_race"] = _events([value["next_main_race"]])["events"][0]
    return result


def current_calendar_plan(profile, events, *, prepared_entry=None):
    """Resolve the real calendar horizon without reading physiology or dosing.

    This uses the same pure calendar functions as the planner. History-dependent
    entry length is reused only by a caller that verified matching saved inputs;
    otherwise the canonical calendar template is labelled as such.
    """
    from biathlon.planning_schedule import horizon
    from biathlon.periodization import build_periodization
    if not isinstance(profile, dict) or not profile.get("program_start") or not profile.get("program_end"):
        return {"availability": "UNAVAILABLE", "reason": "PROGRAMME_DATES_NOT_CONFIGURED"}
    try:
        scheduled, horizon_info = horizon(profile, events)
        reentry = profile.get("reentry_days")
        entry_basis = "EXPLICIT_PROFILE" if reentry is not None else "CALENDAR_TEMPLATE_HISTORY_NOT_RESOLVED"
        if reentry is None and isinstance(prepared_entry, dict) and type(prepared_entry.get("days_override")) is int:
            reentry, entry_basis = prepared_entry["days_override"], "MATCHING_PREPARED_HISTORY"
        periods = build_periodization(scheduled["program_start"], scheduled["program_end"], events,
            reentry_days_override=reentry, taper_days=profile.get("taper_days", 7), transition_days=profile.get("transition_days", 0))
    except (KeyError, ValueError, TypeError):
        return {"availability": "UNAVAILABLE", "reason": "CALENDAR_INPUTS_INCOMPLETE"}
    return {"availability": "AVAILABLE", "basis": "CURRENT_SAVED_PROFILE_AND_CALENDAR",
        "start_date": scheduled["program_start"], "end_date": scheduled["program_end"],
        "horizon": {**_pick(horizon_info, {"mode", "requested_end", "effective_end", "version"}),
            "outside_main_races": _events(horizon_info.get("outside_main_races"))},
        "periodization": _periodization(periods), "entry_basis": entry_basis,
        "interpretation": "The effective horizon follows the canonical calendar rule; raw profile.program_end is only the requested fallback in AUTO_CALENDAR mode. This calendar structure does not compute daily doses, weekly load or readiness. When entry_basis is CALENDAR_TEMPLATE_HISTORY_NOT_RESOLVED, phase dates are a calendar template; the prepared engine may adjust the initial entry length using training history."}


def _outlook(value):
    if not isinstance(value, dict):
        return None
    if "outlook" in value:
        return {**_pick(value, {"configured", "availability", "reason", "view_kind", "preparation"}), "outlook": _outlook(value["outlook"])}
    result = _pick(value, {"schema_version", "profile_revision", "generated_at", "engine_version", "start_date", "end_date", "preparation"})
    if isinstance(value.get("periodization"), dict):
        result["periodization"] = _periodization(value["periodization"])
    if isinstance(value.get("long_term"), dict):
        result["long_term"] = _long_term(value["long_term"])
    if isinstance(value.get("race_duration"), dict):
        result["race_duration"] = _pick(value["race_duration"], {"minutes", "duration_min", "source", "reason", "sport", "distance_m"})
    if isinstance(value.get("source"), dict):
        result["source"] = _pick(value["source"], {"generation_id", "revision", "as_of"})
    if isinstance(value.get("volume_context"), dict):
        result["volume_context"] = _pick(value["volume_context"], {"baseline_weekly_minutes", "weekly_volume_source",
            "missing_history", "reported_history_only", "available_weekly_minutes", "weekly_time_limit_minutes", "availability_mode"})
    if "history_comparison" in value:
        history = _rows(value["history_comparison"])
        result["recent_actual_weeks"] = [_pick(row, {"start_date", "end_date", "covered_days", "actual_minutes", "training_days"}) for row in history[-4:]]
        result["actual_history_coverage"] = {"total_weeks": len(history), "returned_weeks": min(4, len(history)),
            "interpretation": "Recorded actual clock minutes. Missing totals remain unknown; these are not the future targets."}
    result["interpretation"] = "Prepared goals from the shown profile/source revision, not an exact daily prescription or a future recovery forecast. Weekly Q and effective load are not necessarily clock minutes. HISTORICAL_MIX_EQUIVALENT_NOT_TIME_LIMIT is a historical-mix volume equivalent, not a prescribed duration or time allowance."
    return result


def _record(value, *, days_basis=None):
    if not isinstance(value, dict):
        return None
    result = _pick(value, {"revision", "status", "plan_status", "has_proposal", "engine_version", "recorded_at", "stale", "stale_reason", "days_basis", "plan_role", "preparation"})
    for name in ("plan", "proposal", "payload"):
        if isinstance(value.get(name), dict):
            result[name] = {**_record(value[name]), **_pick(value[name], {"start_date", "end_date", "generated_at"}),
                **({"plan_role": "CURRENT" if name == "plan" else "CANDIDATE"} if name != "payload" else {})}
    days = []
    for day in _rows(value.get("days")):
        from biathlon.planning_schedule import day_sessions
        sessions = [_pick(session, {"session_id", "method_id", "title", "sport", "sport_label_bg", "zone", "purpose",
            "total_minutes", "main_work_minutes", "double_threshold", "is_key_session"}) for session in _rows(day_sessions(day))]
        days.append({**_pick(day, {"date", "status", "period", "taper"}), "sessions": sessions})
    if "days" in value:
        result["days"] = days
        if days_basis:
            result["days_basis"] = days_basis
    return result


def compact_management(payload, view="overview"):
    """Summarize an already-authorized management payload for all user roles."""
    if not isinstance(payload, dict) or payload.get("availability") in {"PENDING", "UNAVAILABLE"}:
        return deepcopy(payload)
    if payload.get("summary_version") == "assistant-planning-summary-v1":
        return deepcopy(payload)
    result = _pick(payload, {"availability", "reason", "view_kind", "schema_version", "configured", "revision",
        "source_revision", "source_generation_id", "generation_id", "as_of", "today", "timezone", "stale", "status",
        "current_calendar", "current_calendar_plan", "profile_basis", "prepared_basis", "prepared_availability", "prepared_reason", "preparation"})
    if view == "outlook":
        result.update(_outlook(payload))
    elif view in {"week", "active"}:
        result.update(_record(payload))
    else:
        if "profile" in payload:
            result["profile"] = _profile(payload["profile"])
        for name in ("overview_active", "active"):
            if name in payload:
                record = payload[name]
                basis = ("GENERATED_CANDIDATE" if record.get("has_proposal") else "CURRENT_PLAN") if name == "overview_active" and isinstance(record, dict) else None
                result[name] = _record(record, days_basis=basis)
        if "outlook" in payload:
            result["outlook"] = _outlook(payload["outlook"])
    result["summary_version"] = "assistant-planning-summary-v1"
    result["detail_queries"] = [
        {"kind": "management", "view": "profile", "purpose": "Full editable planning controls and method preferences"},
        {"kind": "management", "view": "outlook", "section": "outlook.long_term.weeks", "purpose": "Exact weekly targets; use offset/limit for more rows"},
        {"kind": "management", "view": "outlook", "section": "outlook.periodization", "purpose": "Complete phase, taper and calendar rationale"},
        {"kind": "day", "purpose": "Exact current session blocks for a selected date"}]
    result["summary_limits"] = "Excludes detailed session blocks, raw history, repeated evidence and full candidate bodies. Retains current plan status and saved goals separately. Exact omitted fields remain available through the detail queries."
    return result


def compact_management_context(context):
    """Budget recovery for an existing authorized tool result; no new reads."""
    if not isinstance(context, dict) or context.get("query_kind") != "management" or not isinstance(context.get("data"), dict):
        return deepcopy(context)
    payload = context["data"]
    if "profile" in payload or "overview_active" in payload or "active" in payload:
        view = "overview"
    elif "outlook" in payload:
        view = "outlook"
    elif "plan" in payload or "proposal" in payload:
        view = "active"
    else:
        return deepcopy(context)
    result = {**deepcopy(context), "data": compact_management(payload, view)}
    partial = [page for page in _rows(context.get("pagination"))
        if page.get("offset", 0) > 0 or page.get("total", 0) > page.get("returned", 0)]
    if partial:
        result["data"]["source_page_limits"] = deepcopy(partial)
        for page in partial:
            path = str(page.get("section", ""))
            if not path.endswith("long_term.weeks"):
                continue
            parent = result["data"]
            for key in path.split(".")[:-1]:
                parent = parent.get(key) if isinstance(parent, dict) else None
            if isinstance(parent, dict) and isinstance(parent.get("week_coverage"), dict):
                parent["week_coverage"].update(total=page.get("total"), source_offset=page.get("offset", 0),
                    source_rows=page.get("returned"),
                    range_scope="Only the rows of the already-read source page, not the whole saved horizon. Use the original pagination to read missing weeks.")
        note = " The incoming read was already paged: source_page_limits defines any part of the horizon not present in these summaries."
        if note not in result["data"]["summary_limits"]:
            result["data"]["summary_limits"] += note
    return result
