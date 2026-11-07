"""Direct Q evidence for reporting; never derive Q from clock time or spill E."""
from copy import deepcopy
from datetime import date, timedelta
from math import isfinite

from .constants import COMPONENTS
from .planning_schedule import day_sessions


def quantity(value):
    return float(value) if type(value) in (int, float) and isfinite(value) and value >= 0 else None


def known_sum(values):
    values = [quantity(v) for v in values]
    return round(sum(values), 3) if all(v is not None for v in values) else None


def prescribed_day(day):
    if day.get("status") == "EXISTING_ACTIVITY":
        return {z: None for z in COMPONENTS}
    return {z: known_sum((s.get("direct_equivalent_minutes") or {}).get(z) for s in day_sessions(day))
            for z in COMPONENTS}


def actual_day(source, key):
    """Coverage proves zero on rest days; unmapped recorded Q stays unknown.

    Strength uses its direct strength ledger, where Q and E are identical.
    Aerobic E is deliberately never used to reconstruct missing direct Q.
    """
    activities = [a for a in source.get("activities", []) if a.get("date") == key
                  and a.get("sport") != "WeightTraining"]
    covered = {r.get("zone") for r in source.get("daily", []) if r.get("date") == key}
    result = {z: known_sum(next((r.get("equivalent_time_min") for r in a.get("zones", [])
                                if r.get("zone") == z), None) for a in activities)
              if z in covered else None for z in COMPONENTS if z != "STR"}
    strength = [r.get("effective_load") for r in (source.get("strength") or {}).get("daily", [])
                if r.get("date") == key]
    result["STR"] = known_sum(strength) if strength else None
    return result


def execution_report(source, today, plans, previous=None, *, include_history=False):
    """Small live ledger for the exact goal windows of approved/proposed plans."""
    windows = []
    for plan in (p for p in plans if p):
        segments = (plan.get("allocation") or {}).get("segments")
        if segments:
            windows.extend(segments)
        else:
            keys = sorted(d["date"] for d in plan.get("days", []))
            start, end = plan.get("start_date"), plan.get("end_date")
            if start and end or keys:
                windows.append({"window_start": start or keys[0], "window_end": end or keys[-1]})
    keys = set()
    if include_history:
        start = max(today-timedelta(days=89), date.fromisoformat(source.get("period_start", today.isoformat())))
        keys.update((start+timedelta(days=n)).isoformat() for n in range((today-start).days+1))
    for window in windows:
        start = date.fromisoformat(window["window_start"])
        end = min(today, date.fromisoformat(window["window_end"]))
        keys.update((start + timedelta(days=n)).isoformat() for n in range((end-start).days+1))
    prior = {r["date"]: r for r in (previous or {}).get("days", [])}
    counts = {}
    for activity in source.get("activities", []):
        key = activity.get("date")
        counts[key] = counts.get(key, 0) + 1
    covered_dates = {r.get("date") for r in source.get("daily", [])}
    covered_dates.update(r.get("date") for r in (source.get("strength") or {}).get("daily", []))
    rows = []
    for key in sorted(keys):
        # Preserve older evidence only outside the imported window, never over
        # an explicit new incomplete/corrected observation within that window.
        estimates = [a for a in source.get("activities", []) if a.get("date") == key and a.get("planning_estimated")]
        estimated_q = {z: known_sum(next((r.get("planning_estimated_q", 0.) for r in a.get("zones", [])
                                         if r.get("zone") == z), 0.) for a in estimates) for z in COMPONENTS}
        row = deepcopy(prior[key]) if key in prior and key not in covered_dates and not counts.get(key) else {
            "date": key, "q": actual_day(source, key), "estimated_q": estimated_q,
            "estimated_activity_count": len(estimates), "activity_count": counts.get(key, 0)}
        row["day_complete"] = key < today.isoformat()
        rows.append(row)
    return {"schema_version": "direct-q-execution-v1", "as_of": today.isoformat(),
            "basis": "SAME_DIRECT_Q_LEDGER_AS_PLANNING_WITH_EXPLICIT_ESTIMATES",
            "source_period_end": source.get("period_end"), "days": rows}
