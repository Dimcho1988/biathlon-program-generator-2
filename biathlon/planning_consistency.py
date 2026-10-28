"""Disclose a conditional E projection without changing direct-Q intent.

    The curve's expert duration is not a weekly budget. These are weekly Q
    objectives. Actual composed sessions still use canonical dose-dependent
    spill and daily Recovery; a uniform-week projection cannot authorize them.
"""
from .constants import COMPONENTS
from .component_load import calculate_component_load

VERSION = "q-objectives-v4-preserved-intent"


def reconcile(goals, profile, state, reference, *, limited=False, taper=False,
              zone_tmax_minutes=None, capacity_sources=None):
    if limited or not state or not (profile.get("load_progression") or {}).get("enabled", False):
        return goals
    automatic = [z for z in COMPONENTS if z != "STR" and goals[z].get("target_weekly_q") is not None
                 and not goals[z].get("progression", {}).get("manual_override")]
    if not automatic:
        return goals
    context = profile.get("_component_load_context") or {}
    if zone_tmax_minutes is None:
        zone_tmax_minutes = context.get("minutes")
    if capacity_sources is None:
        capacity_sources = context.get("sources")
    requested = {z: goals[z].get("target_weekly_q", 0.) or 0. for z in COMPONENTS}
    requested["STR"] = 0.
    load = calculate_component_load({z: requested[z]/7 for z in COMPONENTS},
                                    zone_tmax_minutes, capacity_sources=capacity_sources)
    demand = {z: load["effective"][z]*7 for z in COMPONENTS}
    for z,g in goals.items():
        desired_q = g.get("target_weekly_q")
        if z in automatic:
            g["desired_weekly_q"] = desired_q
            if g.get("progression"):
                g["progression"].update(desired_weekly_q=desired_q)
        g["consistency"] = {"version":VERSION,"desired_weekly_q":desired_q,
            "original_effective_target":g["target"],
            "requested_weekly_effective":demand[z],"projected_weekly_effective":demand[z],
            "q_scale":1.,"binding":False,"projection":"UNIFORM_WEEK_ONE_SESSION_DAILY_WITH_WEIGHTED_DOWNWARD_AND_ADJACENT_TMAX_SPILL",
            "projection_is_limit":False,"daily_dose_check_required":True}
    return goals
