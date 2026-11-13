"""Small period reports from the same saved load history as the status page.

Aggregation is reporting only. No provider fetch, physiological model or plan
calculation runs in a conversation.
"""
from datetime import date, timedelta

from .oauth_store import PersistentStoreFailure
from .real_service import completed_work_from_load_history
from .schemas import LoadHistoryResponse


def summary(source, start, end, sport=None):
    try:
        history = LoadHistoryResponse.model_validate(source)
    except ValueError as exc:
        raise PersistentStoreFailure("Invalid assistant load history") from exc
    sports = sorted({activity.sport for activity in history.activities})
    first, last = date.fromisoformat(history.period_start), date.fromisoformat(history.period_end)
    covered_start, covered_end = max(start, first), min(end, last)
    coverage = {"requested_start": start.isoformat(), "requested_end": end.isoformat(),
        "stored_start": first.isoformat(), "stored_end": last.isoformat(),
        "full_requested_range": first <= start <= end <= last}
    common = {"start": start.isoformat(), "end": end.isoformat(),
        "requested_days": (end - start).days + 1, "period_coverage": coverage,
        "available_views": ["summary", "daily", "activities"], "available_sports": sports,
        "units": {"raw_time_min": "CLOCK_MINUTES", "raw_time_hours": "CLOCK_HOURS",
            "equivalent_time_min": "Q_EQUIVALENT_MINUTES", "equivalent_time_hours": "Q_EQUIVALENT_HOURS",
            "effective_load": "E_EFFECTIVE_LOAD"},
        "report_rule": "Period totals use all saved activities in the stated report range, not a page. Volume in hours is raw_time_min / 60. Q and E are different measures; E is never clock time. Quality counts refer only to selected activities. Date coverage does not guarantee complete intensity recording."}
    if covered_start > covered_end:
        return {**common, "availability": "UNAVAILABLE", "reason": "PERIOD_OUTSIDE_STORED_HISTORY"}
    if sport is not None:
        common["sport"] = sport
        if sport not in sports:
            return {**common, "availability": "UNAVAILABLE", "reason": "SPORT_NOT_IN_STORED_HISTORY"}
        history = history.model_copy(update={"activities": [a for a in history.activities if a.sport == sport]})
    report = completed_work_from_load_history(history, covered_start, covered_end).model_dump(mode="json", exclude={"athlete_id"})
    for row in report["zones"]:
        row["raw_time_hours"] = row["raw_time_min"] / 60
        row["equivalent_time_hours"] = row["equivalent_time_min"] / 60
    report["totals"]["activity_duration_hours"] = report["totals"]["activity_duration_min"] / 60
    report["totals"]["zoned_time_hours"] = report["totals"]["zoned_hr_time_min"] / 60
    observed = {row.date for row in history.daily}
    coverage["missing_daily_record_dates"] = [(covered_start + timedelta(days=n)).isoformat()
        for n in range((covered_end - covered_start).days + 1)
        if (covered_start + timedelta(days=n)).isoformat() not in observed]
    return {**common, "availability": "AVAILABLE", "view": "summary", "report": report,
        "missing_day_means_rest": False}
