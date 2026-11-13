"""Validate a presentation-only projection without fabricating a full plan."""
from datetime import date
from math import isfinite

from .oauth_store import PersistentStoreFailure


PUBLIC_FIELDS = ("revision", "status", "has_proposal", "engine_version", "days", "history_comparison", "outcomes")


def empty_overview():
    return {"revision": None, "stale": False, "status": None, "has_proposal": False,
            "engine_version": None, "days": [], "history_comparison": [], "outcomes": []}


def _day(value):
    if not isinstance(value, str):
        raise ValueError("date")
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("date")
    return result


def _minutes(value, *, optional=False):
    return optional and value is None or (type(value) in (int, float) and isfinite(value) and value >= 0)


def validate_overview(record):
    try:
        if (not isinstance(record, dict) or type(record.get("revision")) is not int or record["revision"] < 1
                or type(record.get("record_revision")) is not int or record["record_revision"] != record["revision"]
                or record.get("active_schema_version") != "active-plan-v2"
                or record.get("plan_type") != "object" or record.get("proposal_type") not in ("null", "object")
                or record.get("selected_schema_version") != "planning-draft-v1"
                or record.get("selected_status") not in ("DRAFT", "LIMITED_DRAFT", "BLOCKED")
                or record.get("status") not in ("ACTIVE", "PAUSED", "REVIEW_REQUIRED", "COMPLETED")
                or type(record.get("has_proposal")) is not bool
                or record["has_proposal"] != (record.get("proposal_type") == "object")
                or not isinstance(record.get("engine_version"), str)
                or record.get("stored_fingerprint") is not None and not isinstance(record["stored_fingerprint"], str)):
            raise ValueError("record")
        start, end = _day(record["start_date"]), _day(record["end_date"])
        if start > end or not isinstance(record.get("days"), list) or len(record["days"]) > 7:
            raise ValueError("days")
        for day in record["days"]:
            if (not isinstance(day, dict) or not start <= _day(day["date"]) <= end
                    or not isinstance(day.get("status"), str) or not isinstance(day.get("sessions"), list)
                    or len(day["sessions"]) > 3):
                raise ValueError("day")
            for session in day["sessions"]:
                if (not isinstance(session, dict) or not _minutes(session.get("total_minutes"))
                        or session["total_minutes"] > 1440 or not isinstance(session.get("blocks"), list)):
                    raise ValueError("session")
                for block in session["blocks"]:
                    if (not isinstance(block, dict) or not isinstance(block.get("zone"), str)
                            or not _minutes(block.get("duration_min")) or block["duration_min"] > 1440):
                        raise ValueError("block")
        if not isinstance(record.get("outcomes"), list) or not isinstance(record.get("history_comparison"), list):
            raise ValueError("history")
        for outcome in record["outcomes"]:
            _day(outcome["date"])
            if not all(_minutes(outcome[k], optional=True) for k in ("planned_minutes", "actual_minutes")):
                raise ValueError("outcome")
        for week in record["history_comparison"]:
            if (_day(week["start_date"]) > _day(week["end_date"])
                    or not _minutes(week["actual_minutes"], optional=True)
                    or type(week["covered_days"]) is not int or not 0 <= week["covered_days"] <= 7):
                raise ValueError("week")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise PersistentStoreFailure("Invalid management overview projection") from exc
    return record
