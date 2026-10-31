"""Observed training is the starting point; calendar defaults are not limits."""
from datetime import date, timedelta
from .constants import COMPONENTS
from . import load_evidence

VERSION = "planning-history-v1"
MINIMUM_DAYS = 10
DEFAULT_GAP_DAYS = 10  # Visible coaching rule, configurable in planning controls.
LEGACY_AVAILABILITY = [60, 60, 60, 60, 60, 90, 0]


def recorded_zone_minutes(activity, *, unified=False, require_complete=False):
    """Recorded exposure is independent of the old HR-quality label.

    Valid HR/speed intervals establish exposure even when another interval is
    unknown. Duration-only estimates never establish it. Complete recordings
    are required separately when deciding whether another daily slot is free.
    This is planning evidence, not permission to learn from modelled HR zones.
    """
    from .unified_load import finite
    evidence = load_evidence.assess(activity, unified=unified)
    if not evidence["usable"] or require_complete and not evidence["recording_complete"]:
        return None
    if load_evidence.is_strength(activity):
        return {}
    original = {**activity, "zones": [{**row, "raw_time_min": row.get("planning_measured_time_min")}
        for row in activity.get("zones", [])]} if activity.get("planning_estimated") else activity
    return {row["zone"]: row["raw_time_min"] for row in original.get("zones", [])
            if row.get("zone") in COMPONENTS and finite(row.get("raw_time_min")) and row["raw_time_min"] >= 0}


def component_exposure_dates(source, start, end):
    """Dates with at least one recorded minute in each means and component."""
    result = {}
    for activity in source.get("activities", []):
        if not start <= activity.get("date", "") <= end:
            continue
        times = recorded_zone_minutes(activity, unified=bool(source.get("load_selection")))
        if times is None:
            continue
        zones = {z for z, minutes in times.items() if minutes >= 1.}
        if activity.get("sport") == "WeightTraining":
            zones.add("STR")
        for z in zones:
            result.setdefault((activity.get("sport"), z), set()).add(activity["date"])
    return result


def completed_day_lag(source, today):
    """Missing completed days; today's open calendar day is not a data gap.

    This checks the snapshot cutoff only. Per-component ledger coverage and
    activity quality still independently decide whether readiness is known.
    """
    end = source.get("period_end")
    return max(0, (today-timedelta(days=1)-date.fromisoformat(end)).days) if end else None


def assess(source, today, *, gap_days=DEFAULT_GAP_DAYS):
    cutoff = today-timedelta(days=28)
    sets = [{r["date"] for r in source.get("daily", []) if r["zone"] == z} for z in COMPONENTS if z != "STR"]
    # Calendar coverage is determined by the aerobic activity ledger. Missing
    # strength history is separately gated by the planner; it is not a training
    # interruption when aerobic sessions are known.
    complete = {d for d in set.intersection(*sets) if cutoff.isoformat() <= d < today.isoformat()}
    activities = [a for a in source.get("activities", []) if cutoff.isoformat() <= a["date"] < today.isoformat()]
    active = {a["date"] for a in activities if float(a.get("duration_min") or 0) > 0}
    first = min(complete | active, default=today.isoformat())
    day = date.fromisoformat(first)
    gaps, run = [], []
    def record():
        if len(run) >= gap_days:
            gaps.append({"start_date": run[0], "end_date": run[-1], "days": len(run),
                         "kind": "CONFIRMED_BREAK" if all(d in complete for d in run) else "MISSING_OR_MIXED_COVERAGE"})
    while day < today:
        key = day.isoformat()
        if key not in complete or key not in active:
            run.append(key)
        else:
            record(); run = []
        day += timedelta(days=1)
    record()
    segment_start = (date.fromisoformat(gaps[-1]["end_date"])+timedelta(days=1)).isoformat() if gaps else first
    recent = {d for d in complete if d >= segment_start}
    valid_activities = [a for a in activities if a["date"] in recent]
    reasons = []
    if len(recent) < MINIMUM_DAYS:
        reasons.append("FEWER_THAN_10_OBSERVED_DAYS")
    if gaps and len(recent) < MINIMUM_DAYS:
        reasons.append("LONG_BREAK_OR_COVERAGE_GAP")
    if not valid_activities:
        reasons.append("NO_RECENT_TRAINING")
    return {"version": VERSION, "minimum_days": MINIMUM_DAYS, "gap_threshold_days": gap_days,
            "usable": not reasons, "reasons": reasons, "gaps": gaps,
            "reference_start": segment_start, "reference_end": (today-timedelta(days=1)).isoformat(),
            "reference_days": len(recent), "covered_days": len(complete),
            "complete_dates": sorted(recent), "trimmed_before_break": bool(gaps)}


def availability_mode(profile):
    explicit = profile.get("availability_mode")
    if explicit:
        return explicit
    # Only the identified untouched template migrates automatically. Other
    # persisted day limits remain manual, including zero availability.
    return "AUTO_HISTORY" if list(profile["available_minutes"]) == LEGACY_AVAILABILITY else "MANUAL"


def availability(profile):
    mode = availability_mode(profile)
    selected = profile.get("training_days")
    if selected is None:
        selected = list(range(7)) if mode == "AUTO_HISTORY" else [i for i,v in enumerate(profile["available_minutes"]) if v > 0]
    # 360 is the existing technical per-session ceiling, not estimated free
    # time. The actual weekly budget comes exclusively from history/coach goal.
    return [360. if i in selected else 0. for i in range(7)] if mode == "AUTO_HISTORY" else [v if i in selected else 0. for i,v in enumerate(profile["available_minutes"])]


def reentry(profile, evidence):
    if (profile.get("training_return") or {}).get("mode") == "CONFIRMED_BREAK":
        return 0, "EXPLICIT_LINEAR_RETURN_OVERLAY"
    if (profile.get("training_return") or {}).get("mode") == "CONTINUED":
        return 0, "DECLARED_CONTINUING_TRAINING_REQUIRES_LOAD_DATA"
    if profile.get("reentry_days") is not None:
        return profile["reentry_days"], "EXPLICIT_COACH_CHOICE"
    if evidence.get("history_policy", {}).get("usable"):
        return 0, "CONTINUING_OBSERVED_TRAINING"
    return None, "SHORT_OR_INTERRUPTED_HISTORY"


def recovery_coverage(source, today):
    """Check the recovery window against conserved HR/speed recording coverage.

    Older quality flags cannot invalidate a covered recent window. Unidentified
    excluded/provider records still block it; they cannot be assigned a date.
    Today's observed doses also matter, although its open day is not a gap.
    """
    start = today-timedelta(days=40)
    end = today-timedelta(days=1)
    expected = {(start+timedelta(days=i)).isoformat() for i in range(max(0,(end-start).days+1))}
    per_zone = [{r["date"] for r in source.get("daily", []) if r["zone"] == z} for z in COMPONENTS[:5]]
    covered = set.intersection(*per_zone)
    missing = len(expected-covered)
    quality = source.get("quality") or {}
    counted = sum(int(quality.get(k) or 0) for k in ("limited_activities", "excluded_activities"))
    activities = source.get("activities", [])
    identified = sum(a.get("quality_status", "valid") != "valid" for a in activities)
    unidentified = max(0, counted-identified)
    relevant = [a for a in activities if not a.get("date") or start.isoformat() <= a["date"] <= today.isoformat()]
    unified = bool(source.get("load_selection"))
    recorded = unknown = 0.
    known_durations = unified and not unidentified
    partial = unidentified
    issues = []
    for activity in relevant:
        evidence = load_evidence.assess(activity, unified=unified)
        complete = evidence["complete"] and bool(activity.get("date"))
        partial += not complete
        if not complete:
            issues.append(load_evidence.issue(activity, evidence["reason"] or "ACTIVITY_DATE_MISSING"))
        if load_evidence.is_strength(activity):
            if not complete:
                known_durations = False
            continue
        ledger = evidence["ledger"] if evidence["usable"] and activity.get("date") else None
        if ledger is not None:
            recorded += ledger["recorded_seconds"]
            unknown += ledger["unknown_seconds"]
        else:
            known_durations = False
    complete = bool(expected) and not missing and not partial
    return {"complete": bool(expected) and not missing and not partial,
            "missing_days": missing, "partial_activities": partial,
            "expected_days": len(expected), "covered_days": len(expected & covered),
            "unidentified_partial_activities": unidentified,
            "evidence_version": load_evidence.VERSION, "issues": issues,
            "readiness_available": complete,
            "readiness_basis": "RECORDED_LOAD" if complete else "UNAVAILABLE", "estimated": False,
            "recorded_seconds": recorded if known_durations else None,
            "unknown_seconds": unknown if known_durations else None,
            "coverage_percent": (100.*(recorded-unknown)/recorded if recorded else 100.) if known_durations else None}
