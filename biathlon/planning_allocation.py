"""Distribute long-term component intent into complete training methods.

Quotas are a scheduling heuristic, not permission to exceed a dose or a target.
Each long-term microcycle segment keeps its own target; unused load is never
borrowed across its boundaries. Q is prescribed within a component-local
tolerance; E is checked by the actual 7/40 and Recovery models.
"""
from datetime import timedelta

from .constants import COMPONENTS

VERSION = "component-allocation-v6-q-prescription"
SEGMENT_VERSION = VERSION
COVERAGE_TOLERANCE = .10


def assess_component(row):
    """Acceptance around the unchanged target, separate from method capacity.

    Each component and microcycle is assessed independently. Unknown actual
    work cannot become zero, and excess in another component earns no credit.
    """
    target, actual, planned = row["target"], row.get("actual", 0.), row.get("planned", 0.)
    lower, upper = target*(1-COVERAGE_TOLERANCE), target*(1+COVERAGE_TOLERANCE)
    if actual is None or planned is None:
        percent, status = None, "UNKNOWN"
    else:
        total = actual+planned
        percent = 100*total/target if target > 0 else None
        epsilon = 1e-9*max(1., target)
        status = ("BELOW_RANGE" if total < lower-epsilon else "ABOVE_RANGE" if total > upper+epsilon
                  else "NOT_REQUIRED" if target == 0 else "WITHIN_RANGE")
    return {"coverage_percent": percent, "coverage_status": status,
            "within_tolerance": status in {"WITHIN_RANGE", "NOT_REQUIRED"},
            "lower_acceptable": lower, "upper_acceptable": upper}


def objective_rows(allocation):
    """Use local cycles when present; aggregates cannot hide a local gap."""
    segments = allocation.get("segments") or []
    return [(z, row) for s in segments for z, row in s["components"].items()] if segments else list(
        allocation.get("components", {}).items())


def acceptance(allocation):
    rows = objective_rows(allocation)
    return {"target_percent": 100., "lower_percent": 90., "upper_percent": 110.,
            "scope": "EACH_COMPONENT_IN_EACH_MICROCYCLE", "changes_load_limits": False,
            "prescription_tolerance_percent": 10.,
            "within_tolerance": bool(rows) and all(row.get("within_tolerance", assess_component(row)["within_tolerance"]) for _, row in rows)}


def gap_score(rows):
    """Larger is better: worst local gap, all range gaps, distance to 100%."""
    errors, outside = [], []
    for _, row in rows:
        actual, planned, target = row.get("actual", 0.), row.get("planned", 0.), row["target"]
        if actual is None or planned is None:
            error = 1.
            gap = 1.
        else:
            # Completed excess cannot be undone; extra proposals still cost.
            error = abs(actual+planned-max(target, actual))/(target if target > 0 else 1.)
            gap = max(0., error-COVERAGE_TOLERANCE)
        errors.append(error)
        outside.append(gap)
    return (-round(max(outside, default=0.), 5), -round(sum(outside), 5), -round(sum(errors), 5))


def needs_search(allocation):
    return any(row.get("actual") is not None and row.get("planned") is not None
               and abs(row["actual"]+row["planned"]-max(row["target"], row["actual"]))
                   > (min(1., row["target"]*.01) if row["target"] > 0 else .001)
               for _, row in objective_rows(allocation))


def prescription_headroom(row, *, maintenance_reserve=0.):
    """Whole doses approach 100% within the ordinary 110% ceiling.

    Legacy E targets keep their original units and limit. Unknown Q cannot
    authorize a proposal; execution above the limit is not a new baseline.
    A separately approved post-execution reserve can be supplied explicitly.
    """
    if row.get("actual") is None or row.get("planned") is None:
        return None
    limit = row["target"]*(1+COVERAGE_TOLERANCE if row.get("basis") == "DIRECT_Q" else 1.)
    if row.get("basis") == "DIRECT_Q" and maintenance_reserve > 0:
        limit = max(limit, row["target"]+maintenance_reserve)
    return max(0., limit-row["actual"]-row["planned"])


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
        desired_q = [g[z].get('desired_weekly_q',g[z].get('target_weekly_q')) for g in windows.values()]
        target = target_q if q_known else target_e
        done, planned = (done_q, planned_q) if q_known else (done_e, max(0., total_e-done_e))
        remaining = max(0., target-done-planned)
        components[z] = dict(basis='DIRECT_Q' if q_known else 'CANONICAL_E', target=target,
            desired_target_q=sum(desired_q)/7 if all(v is not None for v in desired_q) else target_q,
            actual=done if not q_known or actual_q_known else None, planned=planned,
            remaining=remaining if not q_known or actual_q_known else None,
            target_q=target_q, actual_q=done_q if actual_q_known else None, planned_q=planned_q,
            target_effective=target_e, actual_effective=done_e, planned_effective=max(0., total_e-done_e),
            unallocated_effective=max(0., target_e-total_e))
        components[z].update(assess_component(components[z]))
        components[z].update(prescription_remaining=prescription_headroom(components[z]),
                             effective_is_prescription_limit=not q_known)
    return components


def _segments(windows, segment_keys):
    """Keep adjacent dates with the same authoritative long-term identity.

    The engine supplies the identity used by the long-term outlook, so this
    module does not recreate periodization. A repeated identity after a date
    gap or another segment cannot join two separate allocation windows.
    """
    groups = []
    previous = None
    for day in sorted(windows):
        key = segment_keys[day]
        if (not groups or key != groups[-1]["key"]
                or previous is None or day != previous + timedelta(days=1)):
            groups.append({"key": key, "windows": {}})
        groups[-1]["windows"][day] = windows[day]
        previous = day
    return groups


def segment_objectives(windows, actual, forecast, source, days, *, segment_keys):
    """Exact ceilings for the authoritative goal windows supplied by the engine.

    The engine supplies whole anchored microcycles, including execution before
    the visible week and provisional sessions after it. Only genuine programme
    boundaries truncate a cycle. Integrating each daily level preserves phase,
    taper and wave changes without transferring load across microcycles.
    Legacy callers can still report a partial window explicitly.
    """
    result = []
    for group in _segments(windows, segment_keys):
        current = group["windows"]
        components = objectives(current, actual, forecast, source, days)
        for row in components.values():
            row["remaining_effective"] = max(0., row["target_effective"]
                                               - row["actual_effective"]
                                               - row["planned_effective"])
            row["actual_exceeds_target"] = (row["actual"] is not None
                                             and row["actual"] > row["target"] + .005)
            row["planned_exceeds_target"] = (row["actual"] is not None
                                              and row["planned"] > max(0., row["target"] - row["actual"]) + .005)
            row["actual_effective_exceeds_target"] = row["actual_effective"] > row["target_effective"] + .005
            row["planned_effective_exceeds_target"] = row["planned_effective"] > max(0., row["target_effective"] - row["actual_effective"]) + .005
        result.append({"window_start": min(current).isoformat(),
                       "window_end": max(current).isoformat(),
                       "days": len(current), "components": components})
    return result


def segment_headroom(windows, actual, forecast, source, days, day, *, segment_keys):
    """Return coverage and prescription headroom for the active microcycle.

    ``remaining`` measures the distance to 100%; ``prescription_remaining``
    permits complete doses within 110%. For DIRECT_Q, ``remaining_effective``
    is diagnostic only. Actual rolling 7/40 and Recovery checks use the real
    composed sessions, not a second microcycle E allowance.
    """
    # A hypothetical future dose needs only its own anchored cycle. Other
    # cycles cannot lend it Q and do not need their coverage recalculated.
    for group in _segments(windows, segment_keys):
        if day in group["windows"]:
            return segment_objectives(group["windows"], actual, forecast, source, days,
                                      segment_keys=segment_keys)[0]
    raise ValueError("Allocation day is outside the long-term draft windows")


def objective_load(direct, effective, objectives):
    return {z: direct[z] if objectives[z]['basis'] == 'DIRECT_Q' else effective[z] for z in COMPONENTS}


def remaining_slots(slots, day, slot, *, segment_keys=None):
    """Count capacity only where the current objective can be prescribed."""
    return sum((d > day or d == day and s >= slot)
               and (segment_keys is None or segment_keys[d] == segment_keys[day])
               for d, s in slots)


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


def coverage(effective, allocation, accents, *, targets=None):
    """Credit needed load against a fixed target when one is supplied.

    The caller supplies load in the objective's units. Remaining allocation
    still caps credit, but a nearly exhausted objective must not turn a small
    residual into the reward for a complete long-term target.
    """
    denominator = allocation if targets is None else targets
    return sum((2. if z in accents else 1.) * min(effective[z], allocation[z]) / max(1., denominator[z])
               for z in COMPONENTS if allocation[z] > 0)


def quality_priority(zone, objectives):
    """Prefer a feasible primary whose known direct objective is uncovered.

    Direct preparation counts toward Z1; canonical cascade from another zone
    does not establish direct coverage. Unknown actual Q cannot authorize a
    priority, including for legacy objectives governed by canonical E.
    """
    row = objectives[zone]
    remaining = row.get("remaining")
    if remaining is None or remaining <= 0:
        return 0
    if "actual_q" in row or "planned_q" in row:
        actual, planned = row.get("actual_q"), row.get("planned_q")
    elif row.get("basis") == "DIRECT_Q":
        actual, planned = row.get("actual"), row.get("planned")
    else:
        return 0
    return int(actual is not None and planned is not None and actual + planned < .005)


def remaining_priority(zone, objectives):
    """Known direct-Q shortfall after the first complete exposure.

    A small first dose does not finish a microcycle's component objective.
    This is only a ranking/reservation hint; full dose, Q/E and readiness
    checks still decide whether another session can be prescribed.
    """
    row = objectives[zone]
    if (row.get("basis") != "DIRECT_Q" or row.get("actual_q") is None
        or row.get("planned_q") is None or row.get("remaining") is None):
        return 0.
    target = row.get("target_q") or row.get("target") or 0.
    return min(1., max(0., row["remaining"] / target)) if target > 0 and row["remaining"] >= .5 else 0.


def strength_feasibility(segments):
    """Report structural bounds without transferring excess across cycles.

    A bound below its local target establishes a method/quota shortfall only.
    A higher bound does not establish a feasible schedule or recovered dose.
    """
    return {
        "desired_q": round(sum(row["desired_q"] for row in segments), 3),
        "attainable_upper_bound_q": round(sum(row["attainable_upper_bound_q"] for row in segments), 3),
        "target_coverage_upper_bound_q": round(sum(min(row["desired_q"], row["attainable_upper_bound_q"])
                                                  for row in segments), 3),
        "session_quota": sum(row["session_quota"] for row in segments),
        "quota_bound_below_target": any(row["quota_bound_below_target"] for row in segments),
        "basis": "CYCLE_LOCAL_ACTUAL_PLUS_WHOLE_METHOD_ROLE_BUDGETS_WITH_SESSION_QUOTA",
        "other_constraints_may_reduce": True,
        "segments": segments,
    }


def report(windows, actual, forecast, days, scheduled_slots, weekly_limit, history_minutes, *, source=None, segment_keys=None):
    end = max(windows)
    lower = min(windows).isoformat()
    components = {z: {k: round(v, 3) if isinstance(v, (float, int)) and not isinstance(v, bool) else v for k, v in row.items()}
                  for z, row in objectives(windows, actual, forecast, source or {}, days).items()}
    sessions = [s for d in days for s in d.get("sessions", [])]
    reasons = {}
    for d in days:
        for r in d["rejected_alternatives"]:
            if r["code"] != "LOWER_CURRENT_PRIORITY":
                reason = reasons.setdefault(r["code"], {"code": r["code"], "reason": r["reason"], "days": set(), "evaluated_alternatives": 0})
                reason["days"].add(d["date"])
                reason["evaluated_alternatives"] += len(r.get("method_ids") or [r["method_id"]])
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
    result = {"version": VERSION, "window_start": lower, "window_end": end.isoformat(),
            "components": components, "scheduled_slots": scheduled_slots, "weekly_session_limit": weekly_limit,
            "planned_sessions": len(sessions), "history_minutes_for_period": round(history_minutes*len(days)/7, 3),
            "planned_minutes": round(sum(s["total_minutes"] for s in sessions), 3),
            "has_unallocated_load": any(v['remaining'] is None or v['remaining'] > 1. for v in components.values()),
            "constraints": constraints, "dose_limits": sorted(limits),
            "status": "PERIOD_OBJECTIVES_WITH_SEPARATE_ROLLING_GATES", "requires_catchup": False}
    if segment_keys is not None:
        segment_reports = segment_objectives(windows, actual, forecast, source or {}, days,
                                            segment_keys=segment_keys)
        result.update(version=SEGMENT_VERSION,
                      status="LONG_TERM_SEGMENT_OBJECTIVES_WITH_PHYSIOLOGICAL_GATES",
                      segments=[{**segment, "components": {
                          z: {k: round(v, 3) if isinstance(v, (float, int)) and not isinstance(v, bool) else v
                              for k, v in row.items()}
                          for z, row in segment["components"].items()}}
                          for segment in segment_reports],
                      has_unallocated_load=any(row["remaining"] is None or row["remaining"] > 1.
                                               for segment in segment_reports
                                               for row in segment["components"].values()))
    result["acceptance"] = acceptance(result)
    return result
