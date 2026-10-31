"""Versioned coaching targets. Historical load describes exposure, not capacity.

Forecast sessions never update their own development reference. All defaults
below are visible starting rules, not population safety thresholds.
"""
from datetime import date, timedelta

from .constants import COMPONENTS
from .planning_history import MINIMUM_DAYS

VERSION = "training-targets-v2.1"


def development_reference(actual_rows, today, anchor, cycle_weeks):
    cycle_days = cycle_weeks * 7
    cycle_start = anchor + timedelta(days=max(0, (today - anchor).days // cycle_days) * cycle_days)
    cutoff = min(today, cycle_start)
    selected = [r for r in actual_rows if (cutoff - timedelta(days=40)).isoformat() <= r["date"] < cutoff.isoformat()]
    coverage = {z: len({r["date"] for r in selected if r["zone"] == z}) for z in COMPONENTS}
    if min(coverage.values()) < MINIMUM_DAYS:
        cutoff = today
        selected = [r for r in actual_rows if (today - timedelta(days=40)).isoformat() <= r["date"] < today.isoformat()]
    weekly = {}
    for z in COMPONENTS:
        values = [r["effective_load"] for r in selected if r["zone"] == z]
        weekly[z] = 7 * sum(values) / len(values) if values else 0.
    return {"weekly": weekly, "cutoff": cutoff.isoformat(), "cycle_start": cycle_start.isoformat(),
            "basis": "ACTUAL_ONLY_CYCLE_REFERENCE", "version": VERSION}


def cycle_factor(week, length, progression_percent, *, development=True):
    if week == length - 1:
        return .78
    if not development or length <= 2:
        return 1.
    return 1. + progression_percent / 100. * week / max(1, length - 2)


def component_targets(reference, profile, accents, week, length, period, taper, limited=False, taper_factor=.5):
    manual = profile.get("component_targets_weekly", {})
    result = {}
    for z in COMPONENTS:
        developing = z in accents and period in {"GENERAL_PREPARATION", "SPECIAL_PREPARATION"} and not limited
        factor = cycle_factor(week, length, profile.get("progression_percent", 5), development=developing)
        if period in {"RE_ENTRY", "TRANSITION"}:
            factor = min(factor, .6 if period == "TRANSITION" else .8)
        if taper:
            factor *= taper_factor
        base = manual.get(z, reference["weekly"][z])
        result[z] = {"target": base * factor, "reference": base, "factor": factor,
                     "basis": "COACH_COMPONENT_GOAL" if z in manual else "MESOCYCLE_DEVELOPMENT_GOAL" if developing else "MESOCYCLE_MAINTENANCE_GOAL",
                     "reference_cutoff": reference["cutoff"], "development": developing}
    return result


def specificity(method, race_minutes, period):
    """Keep race duration separate from capacity and total session duration."""
    specific = period in {"SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"}
    if not race_minutes or not specific or method["zone"] not in {"Z3", "Z4", "Z5"}:
        return {"focus": "DURATION_AND_INTENSITY", "work_cap_min": None, "race_duration_min": race_minutes}
    if method["zone"] == "Z3":
        # Smooth transition between speed emphasis and durability. The 55-min
        # ceiling for short events is the user's proposed coaching rule.
        blend = min(1., max(0., (race_minutes - 50.) / 20.))
        cap = 55. + blend * max(0., min(90., .6 * race_minutes) - 55.)
    else:
        cap = race_minutes * (1.4 if race_minutes < 20 else 1.2 if race_minutes < 60 else .8)
    return {"focus": "INTENSITY_WITH_DURATION_CAP" if race_minutes < 60 else "DURABILITY_WITH_INTENSITY_CONTROL",
            "work_cap_min": cap, "race_duration_min": race_minutes,
            "rule_origin": "LIBRARY_V0_7_AND_COACH_STARTING_RULE_NOT_UNIVERSAL_NORM"}
