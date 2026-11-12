"""Plan-only assistant context: prescriptions and editable inputs, not evidence.

These projections run before section selection/pagination. They intentionally
allowlist the mixed management payload so newly added physiological evidence
does not become readable through a plan permission by accident.
"""
from copy import deepcopy
import re

from .management_schemas import ManagementProfile


def _scalars(value, fields):
    if not isinstance(value, dict):
        return {}
    return {key: deepcopy(item) for key, item in value.items() if key in fields
            and (item is None or isinstance(item, (str, int, float, bool))
                 or isinstance(item, (list, tuple)) and all(v is None or isinstance(v, (str, int, float, bool)) for v in item))}


def _rows(value, project):
    return [project(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _schema_value(value, schema, definitions):
    """Project partial saved configuration without inserting schema defaults."""
    if "$ref" in schema:
        schema = definitions.get(schema["$ref"].rsplit("/", 1)[-1], {})
    if "anyOf" in schema:
        candidates = schema["anyOf"]
        selected = next((s for s in candidates if (value is None and s.get("type") == "null")
                         or (isinstance(value, dict) and ("$ref" in s or s.get("type") == "object"))
                         or (isinstance(value, (list, tuple)) and s.get("type") == "array")), candidates[0])
        return _schema_value(value, selected, definitions)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        patterns = schema.get("patternProperties", {})
        result = {}
        for key, item in value.items():
            selected = properties.get(key)
            if selected is None:
                selected = next((rule for pattern, rule in patterns.items() if re.search(pattern, key)), extra)
            if isinstance(selected, dict):
                result[key] = _schema_value(item, selected, definitions)
        return result
    if isinstance(value, (list, tuple)):
        items, prefix = schema.get("items", {}), schema.get("prefixItems", [])
        return [_schema_value(item, prefix[index] if index < len(prefix) else items, definitions)
                for index, item in enumerate(value)]
    return deepcopy(value)


def project_profile(payload):
    """Keep all canonical editable controls, including scientific configuration."""
    if not isinstance(payload, dict):
        return None
    if "profile" in payload or "configured" in payload:
        result = _scalars(payload, {"configured", "revision", "timezone", "today", "availability", "reason", "view_kind"})
        if "profile" in payload:
            result["profile"] = project_profile(payload["profile"])
        return result
    schema = ManagementProfile.model_json_schema()
    return _schema_value(payload, schema, schema.get("$defs", {}))


def _session(value):
    from .assistant_context import BLOCK_FIELDS, SESSION_FIELDS, _card
    result = _scalars(value, SESSION_FIELDS | {"session_id"})
    result["blocks"] = _rows(value.get("blocks"), lambda b: _scalars(b, BLOCK_FIELDS))
    for name in ("canonical_effective_load", "direct_equivalent_minutes"):
        if isinstance(value.get(name), dict):
            result[name] = _scalars(value[name], {"Z1", "Z2", "Z3", "Z4", "Z5", "STR"})
    for name, fields in (("execution_range_policy", {"version", "dose_basis", "note"}),
                         ("race_course_guidance", {"event_id", "event_name", "status", "note"})):
        if isinstance(value.get(name), dict):
            result[name] = _scalars(value[name], fields)
    if isinstance(value.get("method_library"), dict):
        result["method_library"] = _card(value["method_library"])
    return result


def _day(value):
    result = _scalars(value, {"date", "status", "period", "taper"})
    from biathlon.planning_schedule import day_sessions
    result["sessions"] = _rows(day_sessions(value), _session)
    return result


def _long_term(value):
    result = _scalars(value, {"schema_version", "as_of", "targets_version", "limited", "goal_role", "readiness_forecast", "automatic_camp_load_increase"})
    def week(row):
        item = _scalars(row, {"start_date", "end_date", "days", "phases", "accents", "mesocycle_weeks", "volume_budget_minutes", "volume_role"})
        if isinstance(row.get("cycle"), dict):
            item["cycle"] = _scalars(row["cycle"], {"kind", "week", "name", "label", "start_date", "end_date", "factor", "volume_factor", "intensity_factor"})
        components = row.get("components") or {}
        item["components"] = {zone: _scalars(target, {"target_weekly_q", "desired_weekly_q", "target_period_q", "target_weekly_effective", "target_period_effective", "target_index_7_40"})
                              for zone, target in components.items() if zone in {"Z1", "Z2", "Z3", "Z4", "Z5", "STR"} and isinstance(target, dict)}
        return item
    result["weeks"] = _rows(value.get("weeks"), week)
    return result


def _periodization(value):
    from .schemas import PlanningCalendarEvent
    result = _scalars(value, {"engine_version"})
    for name in ("phases", "taper_windows", "reentry_windows"):
        result[name] = _rows(value.get(name), lambda row: _scalars(row, {
            "kind", "label_bg", "start_date", "end_date", "days", "event_id", "event_name", "taper_days", "load_factor", "note_bg",
            "main_race_event_id", "reason", "volume_factor", "prevent_deficit_refill", "planned_days", "requested_days"}))
    result["warnings"] = _rows(value.get("warnings"), lambda row: _scalars(row, {"code", "message"}))
    if isinstance(value.get("parameters"), dict):
        result["parameters"] = _scalars(value["parameters"], {"source", "date_ranges", "taper_is_overlay"})
    schema = PlanningCalendarEvent.model_json_schema()
    for name in ("calendar_context",):
        result[name] = _rows(value.get(name), lambda row: _schema_value(row, schema, schema.get("$defs", {})))
    if isinstance(value.get("next_main_race"), dict):
        result["next_main_race"] = _schema_value(value["next_main_race"], schema, schema.get("$defs", {}))
    return result


def _plan(value):
    if not isinstance(value, dict):
        return None
    result = _scalars(value, {"schema_version", "status", "start_date", "end_date", "generated_at", "engine_version", "profile_revision", "plan_role", "fingerprint"})
    if isinstance(value.get("input_snapshot"), dict):
        result["input_snapshot"] = _scalars(value["input_snapshot"], {"evaluation_date", "profile_revision", "engine_version"})
    for name in ("days", "lookahead_days"):
        if name in value:
            result[name] = _rows(value[name], _day)
    if isinstance(value.get("long_term"), dict):
        result["long_term"] = _long_term(value["long_term"])
    if isinstance(value.get("periodization"), dict):
        result["periodization"] = _periodization(value["periodization"])
    if isinstance(value.get("race_duration"), dict):
        result["race_duration"] = _scalars(value["race_duration"], {"minutes", "duration_min", "duration_minutes", "source", "reason", "sport", "distance_m", "event_id", "event_name"})
    if isinstance(value.get("volume_context"), dict):
        result["volume_context"] = _scalars(value["volume_context"], {"available_weekly_minutes", "weekly_time_limit_minutes", "availability_mode"})
    return result


def _record(value):
    result = _scalars(value, {"revision", "recorded_at", "entry_key", "operation", "stale", "stale_reason", "status", "plan_status", "has_proposal", "engine_version", "detail_query"})
    if isinstance(value.get("preparation"), dict):
        result["preparation"] = _scalars(value["preparation"], {"status", "prepared_at", "evaluated_on", "reason"})
    for name in ("plan", "proposal", "payload"):
        if name in value:
            result[name] = _plan(value[name])
    if "days" in value:
        result["days"] = _rows(value["days"], _day)
    return result


def project_management(payload, view):
    if not isinstance(payload, dict):
        return {}
    result = _scalars(payload, {"availability", "reason", "view_kind", "schema_version", "configured", "revision", "latest_revision", "start_date", "stale", "today", "timezone", "detail_query"})
    if isinstance(payload.get("preparation"), dict):
        result["preparation"] = _scalars(payload["preparation"], {"status", "prepared_at", "evaluated_on", "reason"})
    if view == "profile":
        return {**result, **project_profile(payload)}
    if view in {"active", "week"}:
        return {**result, **_record(payload)}
    if view == "outlook":
        result["outlook"] = _plan(payload.get("outlook"))
    if view in {"overview", "drafts"}:
        if "profile" in payload:
            result["profile"] = project_profile(payload["profile"])
        if "overview_active" in payload:
            result["overview_active"] = _record(payload["overview_active"]) if isinstance(payload["overview_active"], dict) else None
        if "outlook" in payload:
            result["outlook"] = project_management(payload["outlook"], "outlook") if isinstance(payload["outlook"], dict) else None
        if "drafts" in payload:
            result["drafts"] = _rows(payload["drafts"], _record) if isinstance(payload["drafts"], list) else project_management(payload["drafts"], "drafts")
    if view == "checks":
        result.update(_scalars(payload, {"next_before"}))
        result["checks"] = _rows(payload.get("checks"), lambda row: _scalars(row, {"id", "sequence", "checked_revision", "operation", "recorded_at", "reason"}))
    # Job status has no physiological payload; retain only the operational data.
    if view in {"state", "generation-jobs"}:
        result.update(_scalars(payload, {"status", "job_id", "operation", "requested_at", "started_at", "completed_at", "error_code", "message", "result_revision"}))
        if isinstance(payload.get("background_update"), dict):
            result["background_update"] = _scalars(payload["background_update"], {"status", "requested_at", "started_at", "completed_at", "error_code"})
        if "job" in payload:
            result["job"] = _scalars(payload["job"], {"job_id", "status", "stage", "start_date", "requested_at", "updated_at", "attempts", "result_revision", "error_code"}) if isinstance(payload["job"], dict) else None
    return result


def project_methods(payload):
    """Keep method descriptions and prescribed ranges, excluding model evidence."""
    if not isinstance(payload, dict):
        return {}
    from .assistant_context import CARD_FIELDS, _card
    result = _scalars(payload, {"availability", "reason", "view_kind", "configured", "revision", "version", "registry_version", "phase", "phase_source", "today", "timezone", "detail_query"})
    for name in ("profile", "preferences"):
        if name in payload:
            result[name] = project_profile(payload[name] if name == "profile" else {"method_library": payload[name]})
            if name == "preferences":
                result[name] = result[name].get("method_library", {})
    def resolution(row):
        item = _scalars(row, {"status", "reason", "message", "reasons", "work_unit", "work_range", "work_duration_seconds", "work_duration_range_seconds", "minimum_work_seconds", "maximum_work_seconds", "work_distance_m", "work_distance_range_m", "target_speed_kmh", "target_speed_range_kmh", "target_hr_bpm", "hr_reference_range_bpm", "hr_reference_basis", "hr_role", "speed_role", "speed_multiplier_range", "zone", "phase", "repetitions", "sets", "recovery_seconds", "recovery_after_seconds", "kind"})
        if "segments" in row:
            item["segments"] = _rows(row["segments"], resolution)
        return item
    def method(row):
        # A catalog summary omitted its recipe intentionally; absence there is
        # not evidence that the actual method has no executable prescription.
        item = _card(row) if any(key in row for key in ("prescription", "guidance", "source", "provenance", "coach_profile", "resolution")) else _scalars(row, CARD_FIELDS)
        if isinstance(row.get("resolution"), dict):
            item["resolution"] = resolution(row["resolution"])
        return item
    if "methods" in payload:
        result["methods"] = _rows(payload["methods"], method)
    if "method" in payload:
        result["method"] = method(payload["method"]) if isinstance(payload["method"], dict) else None
    return result
