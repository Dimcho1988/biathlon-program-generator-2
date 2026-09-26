"""Allocate rolling component intent before selecting concrete training methods.

Quotas are a scheduling heuristic, not permission to exceed a dose or a target.
Each future window keeps its own calendar target; later load is never borrowed
into a taper day. Direct Q objectives and canonical E limits are distinct.
"""
from datetime import timedelta

from .constants import COMPONENTS

VERSION = "component-allocation-v2-period-objectives"


def objectives(windows, actual, forecast, source, days):
    """Integrate the same daily targets as the long-term outlook over this draft.

    Q is used only when the entire period has a direct-Q target. Manual and
    legacy E targets retain their own units; cascade never fills a Q objective.
    Actual work outside the draft is relevant to rolling gates, not coverage.
    """
    start, end = min(windows).isoformat(), max(windows).isoformat()
    sessions = [s for d in days if start <= d['date'] <= end
                for s in d.get('sessions', ([d['session']] if d.get('session') else []))]
    components = {}
    for z in COMPONENTS:
        q_targets = [g[z]['target'] if z == 'STR' else g[z].get('target_weekly_q') for g in windows.values()]
        q_known = all(v is not None for v in q_targets)
        done_e = sum(r['effective_load'] for r in actual if r['zone'] == z and start <= r['date'] <= end)
        total_e = sum(r['effective_load'] for r in forecast if r['zone'] == z and start <= r['date'] <= end)
        planned_q = sum(s['direct_equivalent_minutes'][z] for s in sessions)
        q_values = [next((r.get('equivalent_time_min') for r in a.get('zones', []) if r['zone'] == z), None)
                    for a in source.get('activities', []) if start <= a['date'] <= end and a.get('sport') != 'WeightTraining']
        done_q = done_e if z == 'STR' else sum(v for v in q_values if v is not None)
        actual_q_known = z == 'STR' or all(v is not None for v in q_values)
        target_e = sum(g[z]['target'] for g in windows.values()) / 7
        target_q = sum(q_targets) / 7 if q_known else None
        target = target_q if q_known else target_e
        done, planned = (done_q, planned_q) if q_known else (done_e, max(0., total_e-done_e))
        remaining = max(0., target-done-planned)
        components[z] = dict(basis='DIRECT_Q' if q_known else 'CANONICAL_E', target=target,
            actual=done if not q_known or actual_q_known else None, planned=planned,
            remaining=remaining if not q_known or actual_q_known else None,
            target_q=target_q, actual_q=done_q if actual_q_known else None, planned_q=planned_q,
            target_effective=target_e, actual_effective=done_e, planned_effective=max(0., total_e-done_e),
            unallocated_effective=max(0., target_e-total_e))
    return components


def objective_load(direct, effective, objectives):
    return {z: direct[z] if objectives[z]['basis'] == 'DIRECT_Q' else effective[z] for z in COMPONENTS}


def dose_share(remaining, minimum, maximum, opportunities):
    """Balance complete doses over the sessions actually needed for this method.

    Unlike division by every free slot, this cannot create subminimum fragments.
    None means this method cannot cover the remainder with repeated legal doses;
    another method may still do so. It is not a feasibility proof for the plan.
    """
    from math import ceil
    if maximum <= 0 or remaining < minimum:
        return None
    needed = max(1, ceil(remaining / maximum))
    if needed > opportunities or remaining / needed < minimum:
        return None
    return remaining / needed


def quota(windows, opportunities, rows, day, slot, *, objectives=None):
    if objectives is not None:
        # One opportunity can carry one method, not a share of every component.
        # Offer the remaining objective to complete doses; the engine retains
        # today's rolling Q/E, Recovery, capacity and time gates independently.
        return {z: (objectives[z]['remaining'] or 0.) if (day, slot) in opportunities[z] else 0.
                for z in COMPONENTS}
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


def report(windows, actual, forecast, days, scheduled_slots, weekly_limit, history_minutes, *, source=None):
    end = max(windows)
    lower = min(windows).isoformat()
    components = {z: {k: round(v, 3) if isinstance(v, (float, int)) else v for k, v in row.items()}
                  for z, row in objectives(windows, actual, forecast, source or {}, days).items()}
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
            "has_unallocated_load": any(v['remaining'] is None or v['remaining'] > 1. for v in components.values()),
            "constraints": constraints, "dose_limits": sorted(limits),
            "status": "PERIOD_OBJECTIVES_WITH_SEPARATE_ROLLING_GATES", "requires_catchup": False}
