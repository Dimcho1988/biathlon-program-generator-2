"""Coach policy for long-term growth. Q, E, time and 7/40 stay distinct.

References are observed complete cycles (including unloading). The policy is
replayed from those observations, never compounded by opening/regenerating a
plan. Bounds are editable coaching priors, not validated safety thresholds.
"""
from datetime import date, timedelta
from math import isfinite

from .constants import COMPONENTS
from . import planning_controls

VERSION = "load-progression-v1"
# Deliberately separate from speed-duration correction and canonical Tref.
WEEKLY_Q_BOUNDS = {"Z1": (240., 840.), "Z2": (60., 300.), "Z3": (30., 120.),
                   "Z4": (10., 40.), "Z5": (5., 30.)}
DEFAULTS = {"enabled": True, "low_volume_annual_percent": 30.,
            "upper_volume_annual_percent": 10., "ceiling_ratio": 1.3,
            "precompetition_factor": .15, "competition_factor": .05,
            "max_dose_fraction": .8, "feedback_enabled": True}


def settings(profile):
    value = profile.get("load_progression")
    return {**DEFAULTS, **value} if value and value.get("enabled", True) else None


def annual_rate(q, zone, config):
    """Linear policy knots; never clamp the athlete's actual observation."""
    if q is None or zone not in WEEKLY_Q_BOUNDS:
        return None
    low, high = WEEKLY_Q_BOUNDS[zone]
    maximum, upper = config["low_volume_annual_percent"], config["upper_volume_annual_percent"]
    ceiling = high * config["ceiling_ratio"]
    if q <= low:
        return maximum
    if q <= high:
        return maximum + (upper-maximum)*(q-low)/(high-low)
    return max(0., upper*(ceiling-q)/(ceiling-high))


def accents(profile, period, fallback):
    if not settings(profile):
        return fallback
    duration = profile.get("race_duration_min")
    if period == "GENERAL_PREPARATION":
        return ["Z1", "Z3", "STR"]
    if period in {"SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"} and duration:
        # Initial duration-based coaching categories, shared across the aerobic
        # sports. Sport-specific capacity/exposure gates remain in the planner.
        return ["Z4", "Z5"] if duration <= 8 else ["Z3", "Z4"] if duration < 60 else ["Z2", "Z3"]
    return fallback


def _valid(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value >= 0


def observed_window(source, rows, start, end):
    """Half-open window; uncovered days/unknown Q are not zero-load days."""
    keys = [(start+timedelta(days=i)).isoformat() for i in range((end-start).days)]
    effective = {(r["date"], r["zone"]): r["effective_load"] for r in rows}
    complete = [d for d in keys if all((d, z) in effective for z in COMPONENTS)]
    activities = [a for a in source.get("activities", []) if a["date"] in complete]
    strength = {r["date"]:r for r in source.get("strength", {}).get("daily", [])}
    components = {}
    for z in COMPONENTS:
        q = t = 0.
        known = z != "STR"
        for activity in activities:
            if activity.get("sport") == "WeightTraining":
                continue
            zone = next((v for v in activity.get("zones", []) if v["zone"] == z), {})
            values = [zone.get("equivalent_time_min"), zone.get("raw_time_min")]
            if not all(_valid(v) for v in values):
                known = False
            else:
                q += values[0]
                t += values[1]
        if z == "STR":
            known = all(_valid(strength.get(d, {}).get(k)) for d in complete for k in ("real_time_min", "equivalent_time_min"))
            if known:
                q = sum(strength[d]["equivalent_time_min"] for d in complete)
                t = sum(strength[d]["real_time_min"] for d in complete)
        n = len(complete)
        components[z] = {"weekly_q": q*7/n if n and known else None,
                         "weekly_minutes": t*7/n if n and known else None,
                         "weekly_effective": sum(effective[(d,z)] for d in complete)*7/n if n else None}
    return {"start_date": start.isoformat(), "end_date": (end-timedelta(days=1)).isoformat(),
            "covered_days": len(complete), "expected_days": len(keys),
            "complete": bool(keys) and len(complete) == len(keys), "components": components}


def context(profile, source, rows, today, adaptation=None):
    config = settings(profile)
    controls = profile.get("planning_controls")
    if not config or not controls:
        return None
    days = 7*len(controls["wave"])
    anchor = date.fromisoformat(controls.get("mesocycle_anchor") or profile["program_start"])
    cycle_start = anchor + timedelta(days=((today-anchor).days//days)*days)
    current = observed_window(source, rows, cycle_start-timedelta(days=days), cycle_start)
    previous = observed_window(source, rows, cycle_start-timedelta(days=2*days), cycle_start-timedelta(days=days))
    baseline = current if current["complete"] else observed_window(source, rows, today-timedelta(days=40), today)
    quality = source.get("quality") or {}
    known = baseline["complete"] and not (quality.get("limited_activities") or quality.get("excluded_activities"))
    components = {}
    for z in COMPONENTS:
        b = baseline["components"][z]
        rate = annual_rate(b["weekly_q"], z, config) if known else None
        a, p = current["components"][z], previous["components"][z]
        growth = {key: (100*(a[key]/p[key]-1) if current["complete"] and previous["complete"] and
                          a[key] is not None and p[key] is not None and p[key] > 0 else None)
                  for key in ("weekly_q", "weekly_minutes", "weekly_effective")}
        components[z] = {**b, "annual_rate_percent": rate, "expert_q_bounds": WEEKLY_Q_BOUNDS.get(z),
                         "observed_cycle_growth_percent": growth}
    qtotal = sum(components[z]["weekly_q"] or 0 for z in WEEKLY_Q_BOUNDS)
    complete_q = all(components[z]["annual_rate_percent"] is not None for z in WEEKLY_Q_BOUNDS)
    total_rate = (sum(c["weekly_q"]*c["annual_rate_percent"] for z,c in components.items() if z in WEEKLY_Q_BOUNDS)/qtotal
                  if complete_q and qtotal > 0 else None)
    effective_total = sum(components[z]["weekly_effective"] or 0 for z in WEEKLY_Q_BOUNDS)
    e_weighted_rate = (sum((components[z]["weekly_effective"] or 0)*(components[z]["annual_rate_percent"] or 0)
                          for z in WEEKLY_Q_BOUNDS)/effective_total if effective_total else 0.)
    overall_scale = min(1., total_rate/e_weighted_rate) if total_rate is not None and e_weighted_rate > 0 else 1.
    for c in components.values():
        c["governed_annual_rate_percent"] = c["annual_rate_percent"]*overall_scale if c["annual_rate_percent"] is not None else None
    return {"version": VERSION, "config": config, "basis": "COMPLETED_CYCLE" if current["complete"] else "ACTUAL_40_DAY_REFERENCE",
            "as_of": today.isoformat(), "cycle_start": cycle_start.isoformat(), "cycle_days": days,
            "reference": baseline, "previous_cycle": previous, "completed_cycle": current,
            "components": components, "overall_annual_rate_percent": total_rate,
            "overall_basis": "Q_WEIGHTED_AEROBIC_COMPONENTS_STR_SEPARATE", "adaptation": adaptation,
            "recovery_is_learning_input": False, "requires_catchup": False,
            "reference_is_clamped": False, "percentages_are_coaching_parameters": True}


def phase_factor(period, taper, config):
    if taper:
        return 0.
    return {"GENERAL_PREPARATION": 1., "SPECIAL_PREPARATION": 1.,
            "PRECOMPETITION": config["precompetition_factor"],
            "COMPETITION": config["competition_factor"]}.get(period, 0.)


def projected_cycle_bases(context, periodization, end, profile=None):
    """Conditional outlook only. Never insert projected loads into history.

    Rates are recalculated as projected Q approaches its ceiling. Growth is
    accrued only in eligible calendar phases, with no annual catch-up in prep.
    """
    if not context:
        return {}
    day = date.fromisoformat(context["cycle_start"])
    factors = {z:1. for z in COMPONENTS}
    result = {day.isoformat():dict(factors)}
    config = context["config"]
    adaptation = context.get("adaptation") or {}
    while day <= end:
        phase = next((p["kind"] for p in periodization["phases"] if p["start_date"] <= day.isoformat() <= p["end_date"]), None)
        taper = any(p["start_date"] <= day.isoformat() <= p["end_date"] for p in periodization.get("taper_windows", []))
        multiplier = phase_factor(phase, taper, config)
        state = planning_controls.resolve(profile, day, phase, accents(profile, phase, ["Z1"])) if profile else None
        for z,c in context["components"].items():
            rate = annual_rate(c["weekly_q"]*factors[z], z, config) if c["weekly_q"] is not None and c["annual_rate_percent"] is not None else 0.
            rate = min(rate or 0., c["governed_annual_rate_percent"] or 0.)
            learned = min(adaptation.get("global", {}).get("growth_factor", 1.), adaptation.get("components", {}).get(z, {}).get("growth_factor", 1.))
            selected = state is None or z in state["accents"]
            factors[z] *= (1+(rate or 0.)*multiplier*learned*selected/100)**(1/365.25)
        day += timedelta(days=1)
        if (day-date.fromisoformat(context["cycle_start"])).days % context["cycle_days"] == 0:
            result[day.isoformat()] = dict(factors)
    return result


def apply(goals, profile, state, context, day, period, taper, limited, taper_factor, actual_base):
    if context is None or state is None:
        return goals
    config, controls = context["config"], profile["planning_controls"]
    phase = phase_factor(period, taper, config)
    adaptation = context.get("adaptation") or {}
    weights = adaptation.get("components", {})
    for z, goal in goals.items():
        observed = context["components"][z]
        annual = observed["governed_annual_rate_percent"]
        projection_factor = context.get("projected_baseline_factors", {}).get(z, 1.)
        baseline = observed["weekly_effective"]
        if baseline is not None:
            baseline *= projection_factor
        feedback = weights.get(z, adaptation.get("global", {}))
        learning = min(adaptation.get("global", {}).get("growth_factor", 1.), feedback.get("growth_factor", 1.))
        if projection_factor != 1. and annual is not None:
            annual = min(annual, annual_rate(observed["weekly_q"]*projection_factor, z, config) or 0.)
        rate = (annual or 0.)*phase*learning if not limited and z in state["accents"] else 0.
        # Project one cycle from its observed or explicitly projected baseline.
        # Future bases remain outlook-only; they never create catch-up debt.
        growth_factor = (1+rate/100)**(context["cycle_days"]/365.25)
        automatic = not state["explicit"] and z not in profile.get("component_targets_weekly", {})
        target = goal["target"]
        if automatic and baseline is not None and not limited and period in {"GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"}:
            base = actual_base[z]
            accent_index = state["target_index"] if z in state["accents"] else state["maintenance_index"]
            shape = []
            for i,wave in enumerate(controls["wave"]):
                index = min(2., accent_index*wave)
                if i == len(controls["wave"])-1:
                    index = min(index, .9)
                shape.append(max(0., 7*(index*(base["b50"]+base["c40"])-base["b50"])))
            mean = sum(shape)/len(shape)
            # Normalize the whole wave in E, not the arithmetic mean of R.
            target = baseline*growth_factor*shape[state["week"]-1]/mean if mean > 0 else 0.
            target *= taper_factor
            goal["basis"] = "ACTUAL_CYCLE_GROWTH_WITH_7_40_WAVE"
        # A completed poor-response block reduces only the affected component
        # (or the whole plan for general evidence); never alter Recovery itself.
        dose_factor = min(adaptation.get("global", {}).get("load_factor", 1.), feedback.get("load_factor", 1.))
        target *= dose_factor
        base = actual_base[z]
        ceiling = max(0., 7*(2*(base["b50"]+base["c40"])-base["b50"]))
        target = min(target, ceiling)
        goal.update(target=target, factor=target/max(1e-9, goal["reference"]),
                    target_index=(base["b50"]+target/7)/(base["b50"]+base["c40"]),
                    progression={"annual_policy_percent": annual, "phase_factor": phase,
                                 "effective_annual_percent": rate, "cycle_growth_percent": 100*(growth_factor-1),
                                 "reference_weekly_q": observed["weekly_q"], "reference_weekly_effective": baseline,
                                 "feedback_growth_factor": learning, "feedback_load_factor": dose_factor,
                                 "manual_override": not automatic, "index_ceiling": 2.,
                                 "projected_baseline_factor": projection_factor,
                                 "outlook_is_conditional": True})
    return goals


def history(source, rows, today, weeks=8):
    result = []
    for i in range(weeks, 0, -1):
        end = today-timedelta(days=7*(i-1))
        window = observed_window(source, rows, end-timedelta(days=7), end)
        ref = planning_controls.reference(rows, end)
        for z,c in window["components"].items():
            base = ref[z]
            c["index_7_40"] = ((base["b50"]+c["weekly_effective"]/7)/(base["b50"]+base["c40"])
                                if window["complete"] and base["known"] else None)
            if not window["complete"]:
                c.update(weekly_q=None, weekly_minutes=None, weekly_effective=None)
        result.append(window)
    return result
