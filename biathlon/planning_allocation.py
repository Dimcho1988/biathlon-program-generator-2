"""Allocate rolling component intent before selecting concrete training methods.

Quotas are a scheduling heuristic, not permission to exceed a dose or a target.
Each future window keeps its own calendar target; later load is never borrowed
into a taper day. All load vectors are canonical E, not elapsed minutes.
"""
from datetime import timedelta

from .constants import COMPONENTS

VERSION = "component-allocation-v1"


def quota(windows, opportunities, rows, day, slot):
    result = {}
    end = max(windows)
    for z in COMPONENTS:
        lower = (end - timedelta(days=6)).isoformat()
        used = sum(r["effective_load"] for r in rows if r["zone"] == z and lower <= r["date"] <= end.isoformat())
        missing = max(0., windows[end][z]["target"] - used)
        slots = sum(d <= end and (d > day or d == day and s >= slot)
                    for d, s in opportunities[z])
        # Today's rolling headroom remains a separate hard constraint in the
        # engine. The future shares reserve space for subsequent sessions.
        current = windows[day][z]["target"] - sum(
            r["effective_load"] for r in rows if r["zone"] == z
            and (day-timedelta(days=6)).isoformat() <= r["date"] <= day.isoformat())
        # Spread the end-window need, rather than trying to make up all of an
        # earlier rolling deficit in today's remaining opportunities.
        result[z] = max(0., min(current, missing / slots if slots else 0.))
    return result


def coverage(effective, allocation, accents):
    """Reward needed canonical load; cascade is accounted once per component."""
    return sum((2. if z in accents else 1.) * min(effective[z], allocation[z]) / max(1., allocation[z])
               for z in COMPONENTS if allocation[z] > 0)


def report(windows, actual, forecast, days, scheduled_slots, weekly_limit, history_minutes):
    end = max(windows)
    lower = (end-timedelta(days=6)).isoformat()
    components = {}
    for z in COMPONENTS:
        done = sum(r["effective_load"] for r in actual if r["zone"] == z and lower <= r["date"] <= end.isoformat())
        total = sum(r["effective_load"] for r in forecast if r["zone"] == z and lower <= r["date"] <= end.isoformat())
        target = windows[end][z]["target"]
        components[z] = {"target_effective": round(target, 3), "actual_effective": round(done, 3),
                         "planned_effective": round(max(0., total-done), 3),
                         "unallocated_effective": round(max(0., target-total), 3)}
    sessions = [s for d in days for s in d.get("sessions", [])]
    reasons = {}
    for d in days:
        for r in d["rejected_alternatives"]:
            if r["code"] != "LOWER_CURRENT_PRIORITY":
                reasons.setdefault(r["code"], {"code": r["code"], "reason": r["reason"], "days": set()})["days"].add(d["date"])
    constraints = [{**r, "days": sorted(r["days"])} for r in reasons.values()]
    limits = {}
    for s in sessions:
        e = s["dose_evidence"]
        work = e.get("primary_work_budget_minutes", e.get("prescribed_work_minutes", 0.))
        if work + .51 >= e.get("requested_primary_work_minutes", float("inf")):
            limits["METHOD_CAPACITY_FRACTION"] = "Достигнат е делът от индивидуалния капацитет за избраните методи."
        for limit in e.get("limits", []):
            if work + .51 >= limit["limit_minutes"]:
                limits[limit["code"]] = limit["code"]
    return {"version": VERSION, "window_start": lower, "window_end": end.isoformat(),
            "components": components, "scheduled_slots": scheduled_slots, "weekly_session_limit": weekly_limit,
            "planned_sessions": len(sessions), "history_minutes_for_period": round(history_minutes*len(days)/7, 3),
            "planned_minutes": round(sum(s["total_minutes"] for s in sessions), 3),
            "has_unallocated_load": any(v["unallocated_effective"] > 1. for v in components.values()),
            "constraints": constraints, "dose_limits": sorted(limits),
            "status": "HEURISTIC_ALLOCATION_NOT_PROOF_OF_INFEASIBILITY", "requires_catchup": False}
