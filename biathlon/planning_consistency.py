"""Reconcile direct-Q intent with adjacent-zone E and the 7/40 envelope.

    The curve's expert duration is not a weekly budget. These are weekly Q
    objectives. Actual composed sessions still use canonical dose-dependent
    spill and daily Recovery; a uniform-week projection cannot authorize them.
"""
from .constants import COMPONENTS
from .component_load import calculate_component_load

VERSION = "coherent-q-e-objectives-v2-adjacent-tmax"


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
    def projection(scale):
        q = {z: requested[z]*(scale if z in automatic else 1.)/7 for z in COMPONENTS}
        load = calculate_component_load(q, zone_tmax_minutes, capacity_sources=capacity_sources)
        return {z: load["effective"][z]*7 for z in COMPONENTS}
    demand = projection(1.)
    budgets = {}
    manual = profile.get("component_targets_weekly", {})
    unloading = taper or state["kind"] in {"RE_ENTRY", "RECOVERY"}
    for z,g in goals.items():
        base = reference[z]
        # Existing coach ceiling; preserve explicit goals and unloading.
        ceiling = max(0.,7*(2*(base["c40"]+base["b50"])-base["b50"]))
        budgets[z] = g["target"] if z in manual or unloading or z == "STR" else min(ceiling,max(g["target"],demand[z]))
    lo,hi = (1.,1.) if all(demand[z] <= budgets[z]+1e-9 for z in COMPONENTS) else (0.,1.)
    for _ in range(0 if lo == hi else 28):
        mid = (lo+hi)/2
        values = projection(mid)
        if all(values[z] <= budgets[z]+1e-9 for z in COMPONENTS):
            lo = mid
        else:
            hi = mid
    scale = 1. if lo > 1-1e-8 else lo
    coherent = projection(scale)
    for z,g in goals.items():
        base = reference[z]
        original_e = g["target"]
        desired_q = g.get("target_weekly_q")
        if z in automatic:
            g["desired_weekly_q"] = desired_q
            g["target_weekly_q"] *= scale
            if g.get("progression"):
                g["progression"].update(desired_weekly_q=desired_q,target_weekly_q=g["target_weekly_q"])
        g["target"] = budgets[z]
        g["target_index"] = (base["b50"]+budgets[z]/7)/(base["b50"]+base["c40"])
        g["factor"] = budgets[z]/max(1e-9,g["reference"])
        g["consistency"] = {"version":VERSION,"desired_weekly_q":desired_q,
            "feasible_weekly_q":g.get("target_weekly_q"),"original_effective_target":original_e,
            "projected_weekly_effective":coherent[z],"effective_budget":budgets[z],
            "q_scale":scale,"binding":scale < 1.,"projection":"UNIFORM_WEEK_ONE_SESSION_DAILY_WITH_ADJACENT_TMAX_SPILL",
            "daily_dose_check_required":True}
    return goals
