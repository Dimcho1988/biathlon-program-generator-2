"""Bounded prescription exceptions, separate from physiological rolling gates.

These are versioned coaching policies. A reserve is anchored to the original
microcycle target and charged for *all* actual and proposed excess. It is never
rebased on current execution or replenished by generating another draft.
"""
from .constants import COMPONENTS

VERSION = "planning-continuity-v1-anchored-maintenance"
PREPARATION_FRACTION = .10


def eligible(row):
    """Only measured execution can trigger post-target maintenance."""
    return (row["basis"] == "DIRECT_Q" and row.get("actual_q") is not None
            and row["target"] > 0 and row["actual_q"] >= row["target"] - .005)


def reserves(components, references, manual=()):
    """One conservative, complete maintenance-method budget per component.

    Preparation in those reference sessions belongs to the same shared Z1
    reserve. Other components cannot spend a covered component's direct Q.
    """
    selected = {z: r for z, r in references.items()
                if z not in manual and eligible(components[z])}
    q = {z: sum(r["q"][z] for r in selected.values()) for z in COMPONENTS}
    e = {z: sum(r["e"][z] for r in selected.values()) for z in COMPONENTS}
    for z in manual:
        q[z] = e[z] = 0.
    return {"q": q, "e": e, "references": selected}


def limits(components, budgets, q_remaining, reserve, *, zone, maintenance=False,
           preparation=False):
    """Candidate-specific headroom; rolling E and unknown Q remain hard gates."""
    extra_q = {z: 0. for z in COMPONENTS}
    extra_e = dict(extra_q)
    mode = None
    if maintenance and zone in reserve["references"]:
        mode = "POST_TARGET_MAINTENANCE"
        for z in {zone, "Z1"}:
            extra_q[z] = reserve["q"][z]
        extra_e = dict(reserve["e"])
    elif preparation and zone != "Z1" and (components[zone]["remaining"] or 0) > .005:
        mode = "QUALITY_PREPARATION_RESERVE"
        extra_q["Z1"] = components["Z1"]["target"] * PREPARATION_FRACTION
        extra_e["Z1"] = extra_q["Z1"]
    q_limits, e_limits, objective_limits = {}, {}, {}
    for z, row in components.items():
        known = row.get("actual_q") is not None and row.get("planned_q") is not None
        extra = extra_q[z] if known and row["basis"] == "DIRECT_Q" else 0.
        objective_limits[z] = (max(0., row["target"] + extra - row["actual"] - row["planned"])
                               if row.get("actual") is not None else None)
        if z in q_remaining:
            q_limits[z] = objective_limits[z] or 0.
        # Preserve the original rolling 7/40 envelope even when its anchored
        # prescription target is already covered. A reserve is not load-free.
        e_limits[z] = min(budgets[z].get("rolling_deficit_effective", budgets[z]["deficit_effective"]),
            max(0., row["target_effective"] + extra_e[z]
                - row["actual_effective"] - row["planned_effective"]))
    return {"mode": mode, "q": q_limits, "e": e_limits, "objective": objective_limits,
            "reserve_q": extra_q, "reserve_e": extra_e}


def fits(direct, effective, headroom, components):
    return (all(effective[z] <= headroom["e"][z] + .001 for z in COMPONENTS)
            and all(direct[z] <= v + .001 for z, v in headroom["q"].items())
            and all(headroom["objective"][z] is not None and
                    (direct[z] if components[z]["basis"] == "DIRECT_Q" else effective[z])
                    <= headroom["objective"][z] + .005 for z in COMPONENTS))


def evidence(direct, effective, headroom, segment):
    """Keep the target and exception visible without erasing any overshoot."""
    if not headroom.get("mode"):
        return None
    rows = segment["components"]
    extra = {z: max(0., direct[z] - (rows[z]["remaining"] or 0.)) for z in COMPONENTS}
    extra_e = {z: max(0., effective[z] - rows[z]["remaining_effective"]) for z in COMPONENTS}
    if not any(v > .005 for v in [*extra.values(), *extra_e.values()]):
        return None
    return {"version": VERSION, "mode": headroom["mode"],
            "window_start": segment["window_start"], "window_end": segment["window_end"],
            "additional_q": {z: round(v, 3) for z, v in extra.items() if v > .005},
            "additional_effective": {z: round(v, 3) for z, v in extra_e.items() if v > .005},
            "reserve_q": {z: round(v, 3) for z, v in headroom["reserve_q"].items() if v > .005},
            "basis": "COACH_POLICY_NOT_PHYSIOLOGICAL_THRESHOLD",
            "rolling_limits_preserved": True}
