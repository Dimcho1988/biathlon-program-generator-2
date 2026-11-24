"""On-demand evidence from one saved publication, without a planning replay."""
from fastapi import HTTPException

from biathlon.planning_schedule import day_sessions
from .management_projection import public_management


PLAN_FIELDS = frozenset({"schema_version", "status", "start_date", "end_date", "generated_at",
    "engine_version", "fingerprint", "activation_eligible", "allocation", "parameters", "warnings", "summary"})
DAY_FIELDS = frozenset({"date", "status", "period", "taper", "cycle", "explanation", "locked",
    "rejected_alternatives", "quality_reservations", "load_budget", "readiness_before", "readiness_after",
    "daily_intensity_budget", "readiness_scope", "time_limit_exhausted", "actual_minutes", "actual_sessions", "actual_activity_records"})


def _plan(value, role, field=None):
    from .assistant_context import _pick, _card, SESSION_FIELDS, BLOCK_FIELDS, SNAPSHOT_FIELDS
    if not isinstance(value, dict):
        return None
    result = {**_pick(value, PLAN_FIELDS if field is None else PLAN_FIELDS & {field}), "plan_role": role}
    for name in ("source", "input_snapshot"):
        if field in {None, name} and isinstance(value.get(name), dict):
            result[name] = _pick(value[name], SNAPSHOT_FIELDS)
    for group in ("days", "lookahead_days"):
        if field not in {None, group} or group not in value:
            continue
        result[group] = []
        for day in value[group]:
            if not isinstance(day, dict):
                continue
            item = _pick(day, DAY_FIELDS)
            item["sessions"] = []
            for index, session in enumerate(day_sessions(day)):
                if not isinstance(session, dict):
                    continue
                saved = {**_pick(session, SESSION_FIELDS | {"dose_evidence"}),
                    "session_id": f"{day.get('date')}:{index+1}"}
                saved["blocks"] = [_pick(block, BLOCK_FIELDS) for block in session.get("blocks", [])
                                   if isinstance(block, dict)]
                if isinstance(session.get("method_library"), dict):
                    saved["method_library"] = _card(session["method_library"])
                item["sessions"].append(saved)
            result[group].append(item)
    return public_management(result)


def _index(plan, name, role):
    """Advertise exact paths; never insert diagnostic evidence by default."""
    if not isinstance(plan, dict):
        return None
    from .assistant_context import _pick
    result = {**_pick(plan, {"start_date", "end_date", "generated_at", "engine_version", "fingerprint"}), "plan_role": role}
    result["sections"] = [f"{name}.{field}" for field in sorted(set(plan) & (PLAN_FIELDS | {"source", "input_snapshot"}))
                          if field not in result and field != "days"]
    for group in ("days", "lookahead_days"):
        if group not in plan:
            continue
        result[group] = []
        for index, day in enumerate(plan[group]):
            if not isinstance(day, dict):
                continue
            prefix = f"{name}.{group}.{index}"
            result[group].append({"date": day.get("date"), "section": prefix,
                "sections": [f"{prefix}.{field}" for field in sorted(set(day) & DAY_FIELDS) if field != "date"],
                "sessions": [{"session_id": f"{day.get('date')}:{slot+1}", "method_id": session.get("method_id"),
                    "sections": [f"{prefix}.sessions.{slot}.{field}" for field in ("blocks", "dose_evidence", "method_library")
                                 if field in session]} for slot, session in enumerate(day_sessions(day)) if isinstance(session, dict)]})
    return result


def read(repository, alias, query):
    # The caller checks both plan and recovery permission before settings or
    # records are read. Model-supplied selectors cannot choose another athlete.
    from .assistant_context import PlanStore
    if set(query) - {"kind", "view", "section", "revision", "offset", "limit"}:
        raise HTTPException(422, "Диагностиката чете текущия записан план; избери section и неговата revision.")
    expected = query.get("revision")
    if expected is not None and (type(expected) is not int or not 1 <= expected <= 9007199254740991):
        raise HTTPException(422, "Посочи точната положителна версия на записания план.")
    record = PlanStore(repository).current(alias)
    if record is None:
        return {"availability": "UNAVAILABLE", "reason": "NO_SAVED_PLAN"}
    if expected is not None and expected != record["revision"]:
        raise HTTPException(409, "Записаната програма е обновена. Прочети отново диагностиката и използвай новата revision.")
    payload = record["payload"]
    roles = {"plan": "CURRENT", "proposal": "CANDIDATE"}
    if not any(isinstance(payload.get(name), dict) for name in roles):
        return {"availability": "UNAVAILABLE", "reason": "NO_SAVED_PLAN", "revision": record["revision"]}
    section = query.get("section")
    if section is not None:
        if not isinstance(section, str):
            raise HTTPException(422, "Невалидна част на справката.")
        parts = section.split(".")
        # A coverage/search read must not expand every frozen session card.
        plans = {name: _plan(payload.get(name), role, parts[1] if len(parts) > 1 else None)
                 for name, role in roles.items() if name == parts[0]}
    else:
        plans = {name: _index(payload.get(name), name, role) for name, role in roles.items()}
    return {"revision": record["revision"], "recorded_at": record.get("recorded_at"),
        "plan_status": payload.get("status"), "plan_status_detail": payload.get("reason"),
        "basis": "FROZEN_SAVED_PLAN_DIAGNOSTICS",
        "detail_query": "Read only the needed section, using its exact path and this revision. "
            "allocation includes saved coverage, development and search evidence; days include rejected methods, "
            "constraints and sessions with full saved dose_evidence and method_library. "
            "These are frozen decisions, not a recalculation for current inputs. A CANDIDATE is not the current plan. "
            "An exhausted search with hard_limit_proven=false does not prove physiological impossibility.",
        **plans}
