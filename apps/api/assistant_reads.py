"""Scoped assistant adapters for the application's existing read surfaces.

Only saved data and worker-owned views are read here. Query parameters cannot
select an athlete, an HTTP route, a table, or a calculation implementation.
"""
from copy import deepcopy
from datetime import timedelta
from math import isfinite
import re

from fastapi import HTTPException

from . import prepared_views, prepared_learning
from .management_projection import public_management
from .oauth_store import PersistentStoreFailure

KINDS = frozenset({"settings", "activities", "activity", "speed", "trainability", "learning", "response", "management", "methods"})
PLAN_KINDS = frozenset({"management", "methods"})
PHASES = frozenset({"GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION", "RE_ENTRY", "TRANSITION"})
PARAMETERS = frozenset({"activity_ref", "sport", "view", "days", "phase", "offset", "limit", "section", "duration_s", "distance_m", "speed_kmh", "hr_bpm", "start_s", "revision", "plan_kind", "test_mode"})


def _page(payload, query):
    """Keep large reads navigable and expose every omission with an exact path."""
    offset, limit = query.get("offset", 0), query.get("limit", 30)
    if type(offset) is not int or not 0 <= offset <= 10000 or type(limit) is not int or not 1 <= limit <= 100:
        raise HTTPException(422, "Невалидна страница на справката.")
    section = query.get("section")
    selected = payload
    if section is not None:
        if not isinstance(section, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+)){0,7}", section):
            raise HTTPException(422, "Невалидна част на справката.")
        for key in section.split("."):
            if isinstance(selected, dict) and key in selected:
                selected = selected[key]
            elif isinstance(selected, list) and key.isdigit() and int(key) < len(selected):
                selected = selected[int(key)]
            else:
                raise HTTPException(422, "Тази част липсва в справката. Използвай наличните полета.")
    pages = []

    def visit(value, path, root=False):
        if isinstance(value, dict):
            return {key: visit(item, f"{path}.{key}" if path else key) for key, item in value.items()}
        if isinstance(value, list):
            # Small structured vectors (e.g. zone bounds) are never sliced by
            # the offset of a different long time series.
            paged = len(value) > limit or root
            first = offset if paged else 0
            rows = value[first:first + limit] if paged else value
            if paged:
                pages.append({"section": path, "total": len(value), "offset": first,
                    "returned": len(rows), "next_offset": first + len(rows) if first + len(rows) < len(value) else None})
            return [visit(item, f"{path}.{index}" if path else str(index)) for index, item in enumerate(rows, first)]
        return deepcopy(value)

    result = {"data": visit(selected, section or "", root=isinstance(selected, list)),
        "available_sections": sorted(payload) if isinstance(payload, dict) else [],
        "pagination": pages,
        "pagination_rule": "Lists are paged, not aggregated. Use section and next_offset to read more; numeric path parts are original list indexes. Totals describe the full saved view. Do not combine pages if source revision changes."}
    if section:
        result["section"] = section
    return result


def _prepared(repository, alias, kind, parameters=None):
    try:
        return prepared_views.read(repository, alias, kind, parameters or {})
    except HTTPException as exc:
        if exc.status_code == 503:
            return {"availability": "PENDING", "reason": "PREPARED_VIEW_PENDING", "view_kind": kind}
        raise


def _period(query, today, parse_date, default=14, maximum=90):
    end = parse_date(query.get("end"), today)
    start = parse_date(query.get("start"), end - timedelta(days=default - 1))
    if start > end or end > today or (end - start).days >= maximum:
        raise HTTPException(422, f"Избери минал или текущ период до {maximum} дни.")
    return start, end


def _parameters(query, today, parse_date):
    """Canonical cache keys match the HTTP route, including omitted defaults."""
    parameters = {}
    if query.get("sport") is not None:
        sport = query["sport"]
        if not isinstance(sport, str) or not sport.strip() or len(sport) > 80:
            raise HTTPException(422, "Невалиден вид спорт.")
        parameters["sport"] = sport
    if query.get("start") is not None or query.get("end") is not None:
        start, end = _period(query, today, parse_date, default=30)
        parameters.update(period_start=start.isoformat(), period_end=end.isoformat())
    return parameters


def _activity(repository, alias, query):
    from .routes.activities import _activity_view_payload
    key = query.get("activity_ref") or query.get("key")
    if not isinstance(key, str) or not re.fullmatch(r"act_[a-f0-9]{32}", key):
        raise HTTPException(422, "Посочи активността от календарната справка.")
    row = repository.active_activity_view(alias, key)
    if row is None:
        return {"availability": "UNAVAILABLE", "reason": "ACTIVITY_NOT_FOUND"}
    if not isinstance(row, dict):
        raise PersistentStoreFailure("Invalid assistant activity view")
    payload = _activity_view_payload(row, key)
    view = query.get("view") or "overview"
    if view not in {"overview", "series", "shadow"}:
        raise HTTPException(422, "Избери overview, series или shadow за активността.")
    common = {key: value for key, value in payload.items() if key not in {"series", "shadow"}}
    if view == "series":
        source = deepcopy(payload.get("series") or {})
        start = query.get("start_s", 0)
        duration = query.get("duration_s")
        if type(start) not in {int, float} or not isfinite(start) or not 0 <= start <= 172800:
            raise HTTPException(422, "Невалидно начало на участъка.")
        if duration is not None and (type(duration) not in {int, float} or not isfinite(duration) or not 0 < duration <= 172800):
            raise HTTPException(422, "Невалидна продължителност на участъка.")
        source["series"] = [item for item in source.get("series", []) if isinstance(item, dict)
            and type(item.get("elapsed_s")) in {int, float} and item["elapsed_s"] >= start
            and (duration is None or item["elapsed_s"] <= start + duration)]
        common["series"] = source
    elif view == "shadow":
        common["shadow"] = payload.get("shadow")
    else:
        shadow = payload.get("shadow") or {}
        common["analysis"] = {name: deepcopy(shadow[name]) for name in
            ("schema_version", "model_version", "summary", "zone_summary", "trainability_index", "vflat_summary", "quality", "warnings") if name in shadow}
        common["available_views"] = ["overview", "series", "shadow"]
    return common


def _activities(repository, alias, query, today, parse_date):
    from .activity_catalog import activity_calendar_payload
    start, end = _period(query, today, parse_date)
    envelope = repository.active_activity_calendar(alias, start, end)
    if envelope is None:
        return {"availability": "UNAVAILABLE", "reason": "NO_ACTIVE_ACTIVITY_GENERATION"}
    if not isinstance(envelope, dict) or not isinstance(envelope.get("activities"), list):
        raise PersistentStoreFailure("Invalid assistant activity calendar")
    rows = envelope["activities"]
    if any(not isinstance(row, dict) for row in rows):
        raise PersistentStoreFailure("Invalid assistant activity rows")
    rows = [row for row in rows if start.isoformat() <= str(row.get("local_date", "")) <= end.isoformat()
        and (not query.get("sport") or row.get("sport") == query["sport"])]
    snapshot = envelope.get("snapshot_payload") or {}
    wellness = [row for row in snapshot.get("wellness_calendar", []) if isinstance(row, dict)
        and start.isoformat() <= str(row.get("date", "")) <= end.isoformat()]
    return activity_calendar_payload(athlete_alias=alias, period_start=start, period_end=end, rows=rows,
        shadow_zones={row["activity_ref"]: row.get("hrmod_zone_summary") or [] for row in rows}, wellness_days=wellness,
        generation_metadata={name: envelope.get(name) for name in ("generation_id", "revision", "analysis_as_of", "activated_at")})


def _management(repository, alias, access, query, today, parse_date, actor):
    view = query.get("view") or "overview"
    if view not in {"overview", "week", "profile", "outlook", "active", "drafts", "draft-metadata", "state", "checks", "speed-comparison", "generation-jobs"}:
        raise HTTPException(422, "Неподдържан изглед на програмата.")
    if view == "generation-jobs":
        if access.get("can_edit_plan") is not True or not actor:
            raise HTTPException(403, "Нужно е право за редакция на плана.")
        from .planning_jobs import status
        from uuid import UUID
        try:
            job = UUID(query["key"]) if query.get("key") else None
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(422, "Невалидна заявка за подготовка.") from exc
        return status(repository, alias, actor, job)
    if view == "speed-comparison":
        if access.get("can_view_recovery") is not True:
            raise HTTPException(403, "Нямаш достъп до скоростния отчет.")
        plan_kind, revision = query.get("plan_kind"), query.get("revision")
        if plan_kind not in {"DRAFT", "ACTIVE"} or type(revision) is not int or not 1 <= revision <= 9007199254740991:
            raise HTTPException(422, "Посочи вида и точната версия на програмата.")
        parameters = {"kind": plan_kind, "revision": revision}
        if plan_kind == "DRAFT":
            if not query.get("date"):
                raise HTTPException(422, "Посочи началната дата на проекта.")
            parameters["start_date"] = parse_date(query["date"], today).isoformat()
        elif query.get("date"):
            raise HTTPException(422, "Активната програма се избира по версия.")
        return _prepared(repository, alias, "management-speed-comparison", parameters)
    if view in {"drafts", "draft-metadata"}:
        from .management_store import ManagementStore
        from .management_service import history
        start = parse_date(query["date"], today) if query.get("date") else None
        revision = query.get("revision")
        if view == "draft-metadata":
            if start is None:
                raise HTTPException(422, "Посочи началната дата на проекта.")
            return ManagementStore(repository).draft_metadata(alias, start)
        if revision is not None and (start is None or type(revision) is not int or not 1 <= revision <= 9007199254740991):
            raise HTTPException(422, "Посочи началната дата и версията на проекта.")
        return public_management(history(repository, alias, start_date=start, **({"revision": revision} if revision else {})))
    if view == "checks":
        from .management_plan_store import PlanStore
        before = query.get("key")
        if before is not None and (not isinstance(before, str) or not before.isdigit() or not 1 <= int(before) <= 9007199254740991):
            raise HTTPException(422, "Невалидна страница с проверки.")
        return PlanStore(repository).checks(alias, before=int(before) if before else None, limit=min(query.get("limit", 30), 50))
    if view in {"week", "active"}:
        from .assistant_context import PlanStore, _session, _pick, SNAPSHOT_FIELDS
        from biathlon.planning_schedule import day_sessions
        record = PlanStore(repository).current(alias)
        if record is None:
            return {"availability": "UNAVAILABLE", "reason": "NO_APPROVED_PLAN"}
        payload = record["payload"]
        result = {"revision": record["revision"], "plan_status": payload.get("status"), "recorded_at": record.get("recorded_at")}
        for name in ("plan", "proposal"):
            plan = payload.get(name)
            if not isinstance(plan, dict):
                result[name] = None
                continue
            result[name] = {**_pick(plan, {"start_date", "end_date", "generated_at", "fingerprint", "engine_version"}),
                "input_snapshot": _pick(plan.get("input_snapshot"), SNAPSHOT_FIELDS),
                "is_approved": name == "plan", "days": []}
            for day in plan.get("days", []):
                if not isinstance(day, dict):
                    continue
                result[name]["days"].append({"date": day.get("date"), "status": day.get("status"), "sessions": [
                    _pick(_session(session, f"{day.get('date')}:{index + 1}", access),
                        {"session_id", "method_id", "title", "sport", "zone", "purpose", "total_minutes", "main_work_minutes", "double_threshold"})
                    for index, session in enumerate(day_sessions(day)) if isinstance(session, dict)]})
        result["detail_query"] = "Use day with the selected date for full approved session blocks and execution guidance. proposal is not an approved prescription."
        return result
    prepared_kind = "management-view" if view == "overview" else f"management-{view}"
    parameters = {"view": "overview", "projection": "overview-v1"} if view == "overview" else {}
    return public_management(_prepared(repository, alias, prepared_kind, parameters))


def read(repository, alias, access, query, settings, today, parse_date, *, actor=None):
    kind = query["kind"]
    if kind == "settings":
        from .routes.settings import _settings_response
        view = query.get("view") or "profile"
        if view == "profile":
            payload = _settings_response(settings).model_dump(mode="json")
        elif view == "recovery":
            from .model_service import ModelStore
            payload = ModelStore(repository).config(alias)
        elif view == "accents":
            if access.get("can_view_plan") is not True:
                raise HTTPException(403, "Нямаш достъп до профила за планиране.")
            from .routes.settings import _mesocycle_accent_response
            payload = _mesocycle_accent_response(repository.athlete_mesocycle_accent_preferences(alias)).model_dump(mode="json")
        else:
            raise HTTPException(422, "Избери profile, recovery или accents за настройките.")
    elif kind == "activities":
        payload = _activities(repository, alias, query, today, parse_date)
    elif kind == "activity":
        payload = _activity(repository, alias, query)
    elif kind == "management":
        payload = _management(repository, alias, access, query, today, parse_date, actor)
    elif kind == "learning":
        days = query.get("days", 30)
        if type(days) is not int or days not in {14, 30, 60, 90, 366}:
            raise HTTPException(422, "Избери 14, 30, 60, 90 или 366 дни за самообучението.")
        end = parse_date(query.get("end"), today)
        if end > today:
            raise HTTPException(422, "Периодът на самообучението не може да бъде в бъдещето.")
        try:
            payload = prepared_learning.read_view(repository, alias, days=days, **({"end": end} if query.get("end") else {}))
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            payload = {"availability": "PENDING", "reason": "PREPARED_LEARNING_PENDING"}
    elif kind == "methods":
        phase = query.get("phase")
        if phase is not None and phase not in PHASES:
            raise HTTPException(422, "Невалиден период на подготовката.")
        payload = _prepared(repository, alias, "management-methods", {"phase": phase} if phase else {})
        if payload.get("availability") != "PENDING":
            identifier = query.get("method_id")
            rows = payload.get("methods") or []
            if identifier:
                selected = next((row for row in rows if row.get("id") == identifier), None)
                payload = {name: value for name, value in payload.items() if name != "methods"}
                payload.update(method=selected, availability="AVAILABLE" if selected else "UNAVAILABLE",
                    reason=None if selected else "METHOD_NOT_FOUND")
            elif not query.get("section"):
                payload = {name: value for name, value in payload.items() if name != "methods"}
                payload["methods"] = [{name: row[name] for name in ("id", "title", "family", "sports", "periods", "registry_kind", "activation_status", "non_executable_reason") if name in row} for row in rows]
                payload["detail_query"] = "Use method_id for the complete current card and its resolution; method reads a frozen prescribed session."
    elif kind == "speed":
        view = query.get("view") or "overview"
        if view not in {"overview", "curve", "tests", "zones", "history", "load", "preview"}:
            raise HTTPException(422, "Неподдържан скоростен изглед.")
        if view == "preview":
            key = query.get("activity_ref")
            if not isinstance(key, str) or not re.fullmatch(r"act_[a-f0-9]{32}", key):
                raise HTTPException(422, "Посочи активността от календарната справка.")
            start, duration = query.get("start_s"), query.get("duration_s")
            mode = query.get("test_mode") or "STRICT"
            if mode not in {"STRICT", "EXPLORATORY"} or (start is None) != (duration is None):
                raise HTTPException(422, "Невалиден режим или участък за тест.")
            if start is not None and (type(start) is not int or not 0 <= start <= 172800 or type(duration) is not int or not 11 <= duration <= 43516):
                raise HTTPException(422, "Невалиден участък за тест.")
            parameters = {"activity_ref": key}
            if start is not None:
                parameters.update(start_s=start, duration_s=duration)
            if mode != "STRICT":
                parameters["test_mode"] = mode
            payload = _prepared(repository, alias, "speed-preview", parameters)
            return payload if payload.get("availability") == "PENDING" else {"availability": "AVAILABLE", "source": "SAVED_APPLICATION_VIEW", **_page(payload, query)}
        parameters = _parameters(query, today, parse_date)
        numbers = {name: query[name] for name in ("duration_s", "distance_m", "speed_kmh", "hr_bpm") if query.get(name) is not None}
        if any(type(value) not in {int, float} or not isfinite(value) or value <= 0 for value in numbers.values()) or len(numbers) > 1:
            raise HTTPException(422, "Посочи една положителна стойност за скоростната прогноза.")
        if view not in {"history", "load"}:
            if "period_start" in parameters:
                raise HTTPException(422, "Периодът е приложим към скоростната история и натоварване.")
            parameters.update(numbers)
        elif numbers:
            raise HTTPException(422, "Скоростната прогноза използва изглед overview.")
        if view == "history":
            parameters = {name: value for name, value in parameters.items() if name == "sport"}
        payload = _prepared(repository, alias, {"history": "speed-history", "load": "speed-load"}.get(view, "speed"), parameters)
        if view == "history" and (query.get("start") or query.get("end")) and payload.get("availability") != "PENDING":
            first, last = _period(query, today, parse_date, default=30)
            payload = deepcopy(payload)
            rows = [row for row in payload.get("activities", []) if isinstance(row, dict)
                and first.isoformat() <= str(row.get("date", "")) <= last.isoformat()]
            # A period filter may select stored rows but cannot relabel full-window
            # aggregate totals as if they described the selected dates.
            aggregates = {key: payload.pop(key) for key in ("zones", "classified_minutes", "recorded_minutes",
                "rest_minutes", "accounted_minutes", "unaccounted_minutes", "estimated_descent_minutes") if key in payload}
            payload.update(activities=rows, requested_period={"start": first.isoformat(), "end": last.isoformat()},
                full_window_summary={"start": payload.get("start_date"), "end": payload.get("end_date"), **aggregates},
                summary_scope="FULL_SOURCE_WINDOW_NOT_SELECTED_DATES")
        if view in {"overview", "tests", "zones"} and not query.get("section") and payload.get("availability") != "PENDING":
            omit = {"points", "activities"}
            if view != "tests":
                omit.add("tests")
            payload = {name: deepcopy(value) for name, value in payload.items() if name not in omit}
            # Both the individual curve and the dosing blend have chart grids.
            if isinstance(payload.get("dosing_model"), dict):
                payload["dosing_model"].pop("points", None)
            payload["available_views"] = ["overview", "curve", "tests", "zones", "history", "load"]
    elif kind in {"trainability", "response"}:
        parameters = _parameters(query, today, parse_date)
        parameters.pop("sport", None)
        payload = _prepared(repository, alias, kind, parameters)
        if kind == "trainability" and query.get("sport") and isinstance(payload.get("activities"), list):
            payload = {**payload, "activities": [row for row in payload["activities"] if row.get("sport") == query["sport"]]}
    else:
        raise HTTPException(422, "Неподдържана справка.")
    if access.get("can_view_recovery") is not True:
        from .assistant_read_projection import project_management, project_methods
        if kind == "management":
            payload = project_management(payload, query.get("view") or "overview")
        elif kind == "methods":
            payload = project_methods(payload)
    if payload.get("availability") in {"PENDING", "UNAVAILABLE"}:
        return payload
    return {"availability": "AVAILABLE", "source": "SAVED_APPLICATION_VIEW",
        **{key: payload[key] for key in ("as_of", "revision", "source_revision", "source_generation_id", "generation_id") if key in payload},
        **_page(payload, query)}
