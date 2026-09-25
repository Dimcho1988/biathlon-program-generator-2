"""Deterministic coaching policy, not a physiological readiness model.

Keep loading focus for a whole anchored mesocycle. Recovery support is a
relative priority within a smaller budget, never permission for a new block.
"""
from datetime import date, timedelta

from .constants import COMPONENTS, fresh_parameters

VERSION = "mesocycle-focus-v1"
PREPARATION = {"GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION"}
RECOVERY_TOTAL_FACTOR = .78
RECOVERY_LOADED_FACTOR = .65
RECOVERY_SUPPORT_FACTOR = .90


def ordered(profile, period, serial, fallback):
    """Initial coach templates; duration bands are not metabolic measurements."""
    duration = profile.get("race_duration_min")
    race = (["Z4", "Z5"] if duration <= 8 else ["Z3", "Z4"] if duration < 60 else ["Z2", "Z3"]) if duration else ["Z3", "Z4"]
    if period == "GENERAL_PREPARATION":
        templates = [["Z1", "Z3", "STR", "Z2"], ["Z2", "STR", "Z3", "Z1"], ["Z1", "STR", "Z3", "Z2"]]
    elif period == "SPECIAL_PREPARATION":
        support = ["Z3", "STR", "Z4", "Z2"] if duration and duration <= 8 else ["Z2", "STR", "Z3", "Z1"] if not duration or duration < 60 else ["Z1", "STR", "Z2", "Z4"]
        templates = [[*race, "STR", "Z1", "Z2"], support]
    elif period in {"PRECOMPETITION", "COMPETITION"}:
        # Retain the principal race component, rotate its supporting quality.
        templates = [[*race, "STR", "Z1"], [race[0], "STR", "Z1", race[1]]]
    else:
        templates = [list(fallback)]
    return list(dict.fromkeys(z for z in templates[serial % len(templates)]
                             if z != "STR" or profile.get("strength_enabled")))


def calendar_focus(profile, day, period, fallback, periodization=None):
    controls = profile["planning_controls"]
    anchor = date.fromisoformat(controls.get("mesocycle_anchor") or profile["program_start"])
    days = 7 * len(controls["wave"])
    serial = (day-anchor).days // days
    start = anchor + timedelta(days=serial*days)
    phase = period
    phase_start = anchor
    if periodization and period in PREPARATION:
        at_start = next((p for p in periodization["phases"] if p["start_date"] <= start.isoformat() <= p["end_date"]), None)
        current = next((p for p in periodization["phases"] if p["start_date"] <= day.isoformat() <= p["end_date"]), None)
        selected = at_start if at_start and at_start["kind"] in PREPARATION else current
        if selected:
            phase, phase_start = selected["kind"], date.fromisoformat(selected["start_date"])
    first_cycle = max(0, ((phase_start-anchor).days + days-1)//days)
    order = ordered(profile, phase, max(0, serial-first_cycle), fallback)
    return order, {"mesocycle_id": start.isoformat(), "mesocycle_start": start.isoformat(),
                   "mesocycle_end": (start+timedelta(days=days-1)).isoformat(),
                   "focus_period": phase, "focus_version": VERSION}


def recovery_support(state, profile, rows, today, *, limited=False, taper=False):
    """Use only observed 7/40; future support remains explicitly conditional."""
    if state is None or state["kind"] != "RECOVERY":
        return state
    state = dict(state)
    loaded = state["mesocycle_accents"]
    candidates = []
    if not limited and not taper and profile["planning_controls"]["accent_mode"] != "MANUAL":
        for z in state["support_candidates"]:
            recent = [r for r in rows if r["zone"] == z and
                      (today-timedelta(days=40)).isoformat() <= r["date"] < today.isoformat()]
            week = [r for r in recent if r["date"] >= (today-timedelta(days=7)).isoformat()]
            mean = sum(r["effective_load"] for r in recent)/len(recent) if recent else 0.
            short = sum(r["effective_load"] for r in week)/7
            if len(recent) >= 10 and len({r["date"] for r in week}) == 7 and mean > 0 and short < mean:
                history = [r["effective_load"] for r in rows if r["zone"] == z and
                           (today-timedelta(days=50)).isoformat() <= r["date"] < today.isoformat()]
                base = max(fresh_parameters()["base_loads"][z], .5*sum(history)/len(history))
                candidates.append(((base+short)/(base+mean), z))
    candidates.sort(key=lambda pair: (pair[0], state["support_candidates"].index(pair[1])))
    state.update(accents=[z for _, z in candidates[:1]], recovering_components=list(loaded),
                 focus_role="RECOVERY_SUPPORT", support_basis="OBSERVED_7_40_BELOW_MAINTENANCE",
                 support_as_of=today.isoformat(), support_requires_daily_readiness=True,
                 reason="Разтоварване на водещите компоненти; най-много един по-слабо натоварен компонент с поддържаща доза. Общият товар остава намален.")
    return state


def cap_recovery(goals, profile, state, actual_base):
    """Final cap after progression/manual targets, in canonical E including spill.

    Aerobic E is capped as a sum; STR retains a separate cap. The policy does
    not promise an immediate fall in observed 7/40, nor assume independent zones.
    """
    if state is None or state["kind"] != "RECOVERY":
        return goals
    total_factor = min(RECOVERY_TOTAL_FACTOR, state["volume_factor"])
    for z, goal in goals.items():
        factor = (RECOVERY_LOADED_FACTOR if z in state["mesocycle_accents"] else
                  RECOVERY_SUPPORT_FACTOR if z in state["accents"] else total_factor)
        factor = min(factor, total_factor) if z not in state["accents"] or total_factor < RECOVERY_TOTAL_FACTOR else factor
        base = actual_base[z]
        ceiling = 7*base["c40"]*factor
        goal["target"] = min(goal["target"], ceiling)
        goal["recovery_ceiling_effective"] = ceiling
        goal["development"] = False
    aerobic = [z for z in COMPONENTS if z != "STR"]
    ceiling = total_factor * sum(7*actual_base[z]["c40"] for z in aerobic)
    total = sum(goals[z]["target"] for z in aerobic)
    scale = min(1., ceiling/total) if total > 0 else 1.
    for z, goal in goals.items():
        if z != "STR":
            goal["target"] *= scale
        base = actual_base[z]
        goal.update(factor=goal["target"]/max(1e-9, goal["reference"]),
                    target_index=(base["b50"]+goal["target"]/7)/(base["b50"]+base["c40"]),
                    recovery_total_factor=total_factor, recovery_policy=VERSION)
    return goals
