"""Bounded background projections over permanent compact scientific inputs.

The controller keeps its existing operational window. Chart navigation never
loads raw streams, fits a model or changes an already approved programme.
"""
from datetime import date, timedelta

from . import recovery_policy
from .learning_view import _accounting_series, _series_row
from .oauth_store import PersistentStoreFailure
from .response_monitoring import build_history
from .response_service import ResponseStore
from .training_plan_engine import _daily_rows

VERSION = "lifetime-timeline-v1"
MAX_DISPLAY_DAYS = 366
CONTEXT_DAYS = 84


def source_from_days(records):
    """No invented rest outside the dates covered by a captured generation."""
    source = {"daily": [], "activities": [], "strength": {"daily": []},
              "quality": {"exclusions": []}, "history_uncertain_days": []}
    wellness, catalog, revisions = [], [], {}
    previous = None
    for row in records:
        day = row.get("day")
        payload = row.get("payload")
        if (not isinstance(day, str) or date.fromisoformat(day).isoformat() != day
                or (previous is not None and day <= previous) or not isinstance(payload, dict)
                or payload.get("schema_version") != "history-day-input-v1"):
            raise PersistentStoreFailure("Invalid lifetime history day")
        previous = day
        source["daily"].extend(payload.get("daily") or [])
        source["activities"].extend(payload.get("activities") or [])
        catalog.extend(payload.get("catalog") or [])
        if payload.get("strength"):
            source["strength"]["daily"].append(payload["strength"])
        if payload.get("wellness"):
            wellness.append(payload["wellness"])
        for issue in payload.get("exclusions") or []:
            source["quality"]["exclusions"].append(issue)
            if not (issue.get("date") or issue.get("local_date")):
                source["history_uncertain_days"].append(day)
        if payload.get("load_selection"):
            source["load_selection"] = payload["load_selection"]
        if payload.get("equivalence_version"):
            source["equivalence_version"] = payload["equivalence_version"]
        revisions[day] = row.get("source_revision")
    if records:
        source.update(period_start=records[0]["day"], period_end=records[-1]["day"])
    return source, wellness, catalog, revisions


def extend_report(repository, alias, report):
    reader = getattr(repository, "history_day_inputs", None)
    if not callable(reader):
        return report
    today = date.fromisoformat(report["as_of"])
    first = today-timedelta(days=MAX_DISPLAY_DAYS-1)
    records = reader(alias, first-timedelta(days=CONTEXT_DAYS), today)
    if not isinstance(records, list) or len(records) > MAX_DISPLAY_DAYS+CONTEXT_DAYS:
        raise PersistentStoreFailure("Invalid lifetime history range")
    current = {row["day"]: row for row in report["series"]}
    if records:
        source, wellness, catalog, revisions = source_from_days(records)
        entries = ResponseStore(repository).entries(alias)
        history = build_history(entries=entries, wellness=wellness, activities=catalog,
            start=date.fromisoformat(records[0]["day"]), end=today, today=today)
        evidence = recovery_policy.build_view_data(source=source, rows=_daily_rows(source, today),
            history=history, entries=entries, trainability=[], today=today)
        accounting = _accounting_series(source, catalog)
        older = {}
        for row in evidence["recovery_policy"]["series"]:
            day = row["day"]
            # Never add synthesized days across an unobserved gap in capture.
            if day < first.isoformat() or day not in revisions:
                continue
            point = _series_row(row, accounting.get(day))
            point["source_input_revision"] = revisions[day]
            point["history_model_version"] = recovery_policy.VERSION
            older[day] = point
        # The live planner's projected source owns the recent observations.
        # Archive display cannot alter its present decision or fitted profile.
        for day, row in current.items():
            row = {**row, "source_input_revision": revisions.get(day),
                   "history_model_version": recovery_policy.VERSION}
            older[day] = row
        report["series"] = [older[d] for d in sorted(older) if first.isoformat() <= d <= today.isoformat()]
    report["history_version"] = VERSION
    return report


def process_older_window(repository, model_key):
    """Fill one missing historical year in the existing worker's memory lane."""
    from uuid import uuid4
    operation = getattr(repository, "history_window", None)
    if not callable(operation):
        return False
    token = str(uuid4())
    claim = operation("CLAIM", token=token, model_key=model_key)
    if claim is None:
        return False
    alias = claim["athlete_alias"]
    try:
        end = date.fromisoformat(claim["last_day"])
        report = extend_report(repository, alias, {"as_of": (end+timedelta(days=1)).isoformat(), "series": []})
        operation("STORE", alias=alias, token=token, model_key=model_key, payload=report)
    except Exception:
        operation("FAIL", alias=alias, token=token)
        raise
    return True
