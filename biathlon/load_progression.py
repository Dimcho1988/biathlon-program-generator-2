"""Coach policy for long-term growth. Q, E, time and 7/40 stay distinct.

References are observed complete cycles (including unloading). The policy is
replayed from those observations, never compounded by opening/regenerating a
plan. Bounds are coaching priors, not validated safety thresholds.
"""
from datetime import date, timedelta
from copy import deepcopy
from math import isfinite
from statistics import median
from hashlib import sha256
import json
from .equivalence import EQUIVALENCE_VERSION

from .constants import COMPONENTS
from . import planning_controls

VERSION = "load-progression-v4-clamped-q"
# Deliberately separate from speed-duration correction and canonical Tref.
WEEKLY_Q_BOUNDS = {"Z1": (240., 840.), "Z2": (60., 300.), "Z3": (30., 120.),
                   "Z4": (10., 40.), "Z5": (5., 30.)}
DEFAULTS = {"enabled": True, "low_volume_annual_percent": 30.,
            "upper_volume_annual_percent": 10., "ceiling_ratio": 1.3,
            "precompetition_factor": .15, "competition_factor": .05,
            "max_dose_fraction": .8, "feedback_enabled": True, "training_level": "AUTO",
            "component_reference_positions": {}, "reference_revision": 0}


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


def reference_key(profile, physiology=None):
    config = settings(profile)
    payload = {"program_start": profile["program_start"], "sport": profile["sport"],
               "reference_revision": config["reference_revision"],
               "training_level": config["training_level"], "positions": config["component_reference_positions"],
               "equivalence": EQUIVALENCE_VERSION, "physiology": physiology}
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def expert_positions(profile, source, rows, today):
    """Transparent coaching prior, not a fitness measurement or safety norm."""
    config = settings(profile)
    evidence = planning_controls.volume_history(source, today, 0)
    volume = planning_controls.volume_basis(profile, evidence)
    hours = volume["historical_training_weekly_minutes"] / 60
    experience = profile.get("training_experience_years")
    level = config["training_level"]
    level_position = {"LOW": .15, "MEDIUM": .5, "HIGH": .85}.get(level)
    if level_position is None:
        level_position = 0. if experience is None else min(.85, max(0., experience / 10))
    volume_position = min(1., max(0., (hours - 4) / 16))
    # Large low-intensity mileage alone cannot establish high-intensity capacity.
    position = min(level_position, .6*level_position + .4*volume_position)
    positions = {z: config["component_reference_positions"].get(z, position) for z in WEEKLY_Q_BOUNDS}
    return positions, {"training_level": level, "experience_years": experience,
                       "weekly_hours": hours, "volume_source": volume["weekly_volume_source"],
                       "position_rule": "MIN_LEVEL_60_PERCENT_LEVEL_40_PERCENT_VOLUME_4_TO_20_HOURS",
                       "is_coaching_prior": True}


def reference_from_history(q, prior, zone):
    if zone not in WEEKLY_Q_BOUNDS:
        return q, "OBSERVED" if q is not None else "UNKNOWN"
    if q is None:
        return prior, "EXPERT_FALLBACK"
    low, high = WEEKLY_Q_BOUNDS[zone]
    return min(high, max(low, q)), "LOWER_BOUND" if q < low else "UPPER_BOUND" if q > high else "OBSERVED"


def context(profile, source, rows, today, adaptation=None, *, retained=None, physiology=None, periodization=None):
    config = settings(profile)
    controls = profile.get("planning_controls")
    if not config or not controls:
        return None
    days = 7*len(controls["wave"])
    anchor = date.fromisoformat(controls.get("mesocycle_anchor") or profile["program_start"])
    cycle_start = anchor + timedelta(days=((today-anchor).days//days)*days)
    current = observed_window(source, rows, cycle_start-timedelta(days=days), cycle_start)
    previous = observed_window(source, rows, cycle_start-timedelta(days=2*days), cycle_start-timedelta(days=days))
    quality = source.get("quality") or {}
    valid_units = history_matches(source, physiology)
    reliable = valid_units and not (quality.get("limited_activities") or quality.get("excluded_activities"))
    key = reference_key(profile, physiology)
    frozen = retained if retained and retained.get("key") == key and retained.get("version") in {VERSION, "load-progression-v3-stable-q"} else None
    if frozen and frozen["version"] != VERSION:
        # Re-select the reference without replacing saved measurements or dates.
        frozen = deepcopy(frozen)
        frozen["version"] = VERSION
        for z, c in frozen["components"].items():
            c["reference_q"], c["reference_selection"] = reference_from_history(c["weekly_q"], c.get("expert_reference_q"), z)
    retained_valid = frozen
    if frozen and reliable and current["complete"] and any(frozen["components"][z]["weekly_q"] is None and current["components"][z]["weekly_q"] is not None for z in WEEKLY_Q_BOUNDS):
        frozen = None  # Fill missing components from first reliable observations.
    reused = retained_valid is not None
    if frozen is None:
        windows = [observed_window(source, rows, cycle_start-timedelta(days=days*(i+1)),
                                   cycle_start-timedelta(days=days*i)) for i in range(3)]
        complete = [w for w in windows if w["complete"]]
        if not complete:
            fallback = observed_window(source, rows, today-timedelta(days=40), today)
            complete = [fallback] if fallback["complete"] else []
        positions, expert_basis = expert_positions(profile, source, rows, today)
        frozen_components = {}
        for z in COMPONENTS:
            samples = [w["components"][z] for w in complete if w["components"][z]["weekly_q"] is not None] if reliable else []
            q = median(v["weekly_q"] for v in samples) if samples else None
            effective = median(v["weekly_effective"] for v in samples) if samples else None
            low, high = WEEKLY_Q_BOUNDS.get(z, (None, None))
            prior = low + (high-low)*positions[z] if low is not None else None
            reference_q, selection = reference_from_history(q, prior, z)
            frozen_components[z] = {"weekly_q": q, "weekly_effective": effective,
                "weekly_minutes": median(v["weekly_minutes"] for v in samples) if samples else None,
                "expert_reference_q": prior, "reference_position": positions.get(z),
                "reference_q": reference_q, "reference_selection": selection,
                "source": "OBSERVED_CYCLES_AND_EXPERT" if samples else "EXPERT_ONLY" if prior is not None else "UNKNOWN",
                "observed_windows": len(samples), "established_on": today.isoformat()}
        frozen = {"version": VERSION, "key": key, "created_on": today.isoformat(),
                  "equivalence_version": EQUIVALENCE_VERSION, "expert_basis": expert_basis,
                  "windows": [{k:w[k] for k in ("start_date", "end_date", "covered_days")} for w in complete] if reliable else [],
                  "components": frozen_components}
    if retained_valid and frozen is not retained_valid:
        for z,old in retained_valid["components"].items():
            if old["weekly_q"] is not None or frozen["components"][z]["weekly_q"] is None:
                frozen["components"][z] = old
        frozen["created_on"] = retained_valid["created_on"]
        frozen["windows"] = list({w["start_date"]:w for w in [*retained_valid["windows"], *frozen["windows"]]}.values())
    components = {}
    for z in COMPONENTS:
        b = frozen["components"][z]
        a, p = current["components"][z], previous["components"][z]
        growth = {k: (100*(a[k]/p[k]-1) if reliable and current["complete"] and previous["complete"] and
                     a[k] is not None and p[k] is not None and p[k] > 0 else None)
                  for k in ("weekly_q", "weekly_minutes", "weekly_effective")}
        rate = annual_rate(b["reference_q"], z, config)
        components[z] = {**b, "annual_rate_percent": rate, "governed_annual_rate_percent": rate,
                         "expert_q_bounds": WEEKLY_Q_BOUNDS.get(z), "observed_cycle_growth_percent": growth,
                         "current_observed_q": a["weekly_q"] if reliable and current["complete"] else None}
    ctx = {"version": VERSION, "config": config, "basis": "STABLE_PREPARATION_REFERENCE",
           "as_of": today.isoformat(), "cycle_start": cycle_start.isoformat(), "cycle_days": days,
           "anchor": frozen, "anchor_reused": reused, "reference": frozen,
           "previous_cycle": previous, "completed_cycle": current, "components": components,
           "adaptation": adaptation, "recovery_is_learning_input": False, "requires_catchup": False,
           "reference_is_clamped": any(c["reference_selection"] in {"LOWER_BOUND", "UPPER_BOUND"} for c in components.values()), "percentages_are_coaching_parameters": True,
           "history_usable": reliable, "equivalence_version": EQUIVALENCE_VERSION,
           "overall_basis": "DIRECT_Q_COMPONENT_TARGETS_EFFECTIVE_LOAD_CHECKED_SEPARATELY"}
    if periodization:
        ctx["trajectory"] = trajectory(ctx, profile, periodization)
        deadline = next((p["end_date"] for p in periodization["phases"] if p["kind"] == "SPECIAL_PREPARATION" and p["end_date"] >= frozen["created_on"]), profile["program_end"])
        ctx["target_date"] = deadline
        point = ctx["trajectory"].get(deadline, {z:1. for z in COMPONENTS})
        for z,c in components.items():
            c["target_q"] = c["reference_q"]*point[z] if c["reference_q"] is not None else None
            c["attainable_q"] = c["weekly_q"]*point[z] if c["weekly_q"] is not None else None
            c["limitation"] = ("NO_RELIABLE_COMPONENT_HISTORY" if c["weekly_q"] is None else
                               "NO_OBSERVED_EXPOSURE" if c["weekly_q"] == 0 else
                               "BELOW_REFERENCE_BOUND" if c["reference_selection"] == "LOWER_BOUND" else
                               "ABOVE_REFERENCE_BOUND" if c["reference_selection"] == "UPPER_BOUND" else None)
    return ctx


def phase_factor(period, taper, config):
    if taper:
        return 0.
    return {"GENERAL_PREPARATION": 1., "SPECIAL_PREPARATION": 1.,
            "PRECOMPETITION": config["precompetition_factor"],
            "COMPETITION": config["competition_factor"]}.get(period, 0.)


def trajectory(ctx, profile, periodization):
    """Replay calendar time from the frozen reference, never from a prior forecast.

    Annual rates accrue in focus cycles only. No compression of a year of growth
    into a short preparation, no catch-up, and no growth during unloading/taper.
    """
    day = date.fromisoformat(ctx["anchor"]["created_on"])
    end = date.fromisoformat(profile["program_end"])
    factors = {z:1. for z in COMPONENTS}
    result = {}
    while day <= end:
        period = next((p["kind"] for p in periodization["phases"] if p["start_date"] <= day.isoformat() <= p["end_date"]), None)
        taper = any(p["start_date"] <= day.isoformat() <= p["end_date"] for p in periodization.get("taper_windows", []))
        state = planning_controls.resolve(profile, day, period, accents(profile, period, ["Z1"]), periodization=periodization)
        for z,c in ctx["components"].items():
            rate = annual_rate(c["reference_q"]*factors[z], z, ctx["config"]) if c["reference_q"] is not None else 0.
            feedback = ctx.get("adaptation") or {}
            learned = min(feedback.get("global", {}).get("growth_factor", 1.), feedback.get("components", {}).get(z, {}).get("growth_factor", 1.))
            selected = (state and state["kind"] in {"BUILD", "STRESS"} and day.isoformat() >= c.get("established_on", ctx["anchor"]["created_on"])
                        and not feedback.get("hold_for_reported_illness_or_pain") and z in state.get("mesocycle_accents", state["accents"]))
            factors[z] *= (1+(rate or 0.)*phase_factor(period, taper, ctx["config"])*learned*bool(selected)/100)**(1/365.25)
        result[day.isoformat()] = dict(factors)
        day += timedelta(days=1)
    return result


def apply(goals, profile, state, context, day, period, taper, limited, taper_factor, actual_base):
    if context is None or state is None:
        return goals
    config = context["config"]
    feedback = context.get("adaptation") or {}
    factors = context.get("trajectory", {}).get(day.isoformat(), {})
    # A full focus cycle preserves the mean before separate recovery constraints.
    wave = profile["planning_controls"]["wave"]
    shape = state["wave_factor"] / (sum(wave)/len(wave))
    for z, goal in goals.items():
        c = context["components"][z]
        automatic = not state["explicit"] and z not in profile.get("component_targets_weekly", {})
        focus = z in state.get("mesocycle_accents", state["accents"])
        component_shape = shape if focus else min(.9, state["wave_factor"]*.9)
        if state["kind"] == "RECOVERY":
            component_shape = min(.65, component_shape)
        factor = factors.get(z, 1.)
        # The prior is a destination, not an invented capacity. Actual zero and
        # missing observations cannot authorize an automatic high-intensity dose.
        q = c["weekly_q"]
        requested_q = q*factor*component_shape*taper_factor if q is not None else None
        learning = min(feedback.get("global", {}).get("load_factor", 1.), feedback.get("components", {}).get(z, {}).get("load_factor", 1.))
        goal["target"] *= learning
        # Q controls progression; existing E/7–40 and Recovery gates still govern
        # every composed session. Never apply a Q percentage to an E baseline.
        if automatic and z != "STR" and not limited and period in {"GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"}:
            goal["target_weekly_q"] = requested_q*learning if requested_q is not None else None
            goal["basis"] = "STABLE_Q_TARGET_WITH_7_40_GATE"
        base = actual_base[z]
        goal.update(factor=goal["target"]/max(1e-9, goal["reference"]),
                    target_index=(base["b50"]+goal["target"]/7)/(base["b50"]+base["c40"]),
                    progression={"annual_policy_percent": c["annual_rate_percent"],
                        "effective_annual_percent": (c["annual_rate_percent"] or 0.)*phase_factor(period, taper, config) if focus and not limited else 0.,
                        "reference_weekly_q": c["reference_q"], "observed_weekly_q": q,
                        "target_weekly_q": goal.get("target_weekly_q"), "expert_reference_q": c["expert_reference_q"],
                        "cumulative_growth_percent": 100*(factor-1), "manual_override": not automatic,
                        "reference_source": c["source"], "reference_created_on": context["anchor"]["created_on"],
                        "target_date": context.get("target_date"), "index_ceiling": 2.,
                        "outlook_is_conditional": True, "q_and_e_are_separate": True})
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


def remaining_q(source, proposed_days, day, goals):
    """A real rolling seven-day direct-Q budget; no forecast becomes history."""
    from .planning_schedule import day_sessions
    start, end = (day-timedelta(days=6)).isoformat(), day.isoformat()
    actual = [a for a in source.get("activities", []) if start <= a["date"] <= end and a.get("sport") != "WeightTraining"]
    proposed = [s for d in proposed_days if start <= d["date"] <= end and d.get("status") == "TRAINING" for s in day_sessions(d)]
    result = {}
    for z,g in goals.items():
        target = g.get("target_weekly_q")
        if target is None:
            continue
        values = [next((v.get("equivalent_time_min") for v in a.get("zones", []) if v["zone"] == z), None) for a in actual]
        if any(not _valid(v) for v in values):
            result[z] = 0.  # Unknown Q never authorizes filling an assumed gap.
        else:
            result[z] = max(0., target - sum(values) - sum(s.get("direct_equivalent_minutes", {}).get(z, 0.) for s in proposed))
    return result


def public_context(context):
    return {k:v for k,v in context.items() if k != "trajectory"} if context else None


def history_matches(source, physiology=None):
    # Internal tests/adapters may pass a raw history without a public schema.
    if "schema_version" not in source:
        return True
    return (source.get("equivalence_version") == EQUIVALENCE_VERSION and
            (physiology is None or source.get("zone_bounds_bpm") == physiology["bounds"] and
             source.get("hrmax_bpm") == physiology["hrmax"]))
