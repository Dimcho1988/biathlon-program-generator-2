"""Observed training is the starting point; calendar defaults are not limits."""
from datetime import date, timedelta
from .constants import COMPONENTS

VERSION = "planning-history-v1"
MINIMUM_DAYS = 10
DEFAULT_GAP_DAYS = 10  # Visible coaching rule, configurable in planning controls.
LEGACY_AVAILABILITY = [60, 60, 60, 60, 60, 90, 0]


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
    if profile.get("reentry_days") is not None:
        return profile["reentry_days"], "EXPLICIT_COACH_CHOICE"
    if evidence.get("history_policy", {}).get("usable"):
        return 0, "CONTINUING_OBSERVED_TRAINING"
    return None, "SHORT_OR_INTERRUPTED_HISTORY"
