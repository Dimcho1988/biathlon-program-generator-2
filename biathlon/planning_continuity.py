"""Bounded prescription exceptions, without changing load or Recovery models.

These are versioned coaching policies. A reserve is anchored to the original
microcycle target and charged for *all* actual and proposed excess. It is never
rebased on current execution or replenished by generating another draft.
"""
from .constants import COMPONENTS
from .planning_allocation import prescription_headroom

VERSION = "planning-continuity-v4-planned-easy-reserve"
PREPARATION_FRACTION = .10
TAPER_FRACTION = .10  # Initial coach setting, not a physiological threshold.


def eligible(row, *, planned_easy=False):
    """Known completed work, or allocated easy work, can exhaust a target."""
    return (row["basis"] == "DIRECT_Q" and row.get("actual_q") is not None
            and (not planned_easy or row.get("planned_q") is not None)
            and row["target"] > 0 and row["actual_q"] + (row.get("planned_q", 0.) if planned_easy else 0.) >= row["target"] - .005)


def reserves(components, references, manual=()):
    """One conservative, complete maintenance-method budget per component.

    Preparation in those reference sessions belongs to the same shared Z1
    reserve. Other components cannot spend a covered component's direct Q.
    """
    selected = {z: r for z, r in references.items()
                if z not in manual and (eligible(components[z]) or z in {"Z1", "Z2"}
                    and components[z]["basis"] == "DIRECT_Q" and components[z]["target"] > 0
                    and components[z].get("actual_q") is not None and components[z].get("planned_q") is not None)}
    q = {z: sum(r["q"][z] for r in selected.values()) for z in COMPONENTS}
    e = {z: sum(r["e"][z] for r in selected.values()) for z in COMPONENTS}
    for z in manual:
        q[z] = e[z] = 0.
    return {"q": q, "e": e, "references": selected,
            "easy_q": {z: sum(r["q"][z] for key, r in selected.items() if key in {"Z1", "Z2"}) if z not in manual else 0. for z in COMPONENTS},
            "planned_easy": any(z in {"Z1", "Z2"} and not eligible(components[z]) for z in selected)}


def limits(components, budgets, q_remaining, reserve, *, zone, maintenance=False,
           preparation=False):
    """Candidate-specific headroom; rolling E and unknown Q remain hard gates."""
    extra_q = {z: 0. for z in COMPONENTS}
    extra_e = dict(extra_q)
    mode = None
    if maintenance and zone in reserve["references"]:
        mode = "PLANNED_EASY_MAINTENANCE" if zone in {"Z1", "Z2"} and reserve.get("planned_easy") else "POST_TARGET_MAINTENANCE"
        for z in {zone, "Z1"}:
            extra_q[z] = reserve["q"][z]
        extra_e = dict(reserve["e"])
    elif preparation and zone != "Z1" and (components[zone]["remaining"] or 0) > .005:
        mode = "QUALITY_PREPARATION_RESERVE"
        extra_q["Z1"] = max(components["Z1"]["target"] * PREPARATION_FRACTION,
                            reserve.get("easy_q", {}).get("Z1", 0.))
        extra_e["Z1"] = extra_q["Z1"]
    q_limits, e_limits, objective_limits, index_only = {}, {}, {}, []
    for z, row in components.items():
        q_policy = budgets[z].get("effective_limit_basis") == "PROSPECTIVE_CANONICAL_7_40"
        known = row.get("actual_q") is not None and row.get("planned_q") is not None
        extra = extra_q[z] if known and row["basis"] == "DIRECT_Q" else 0.
        objective_limits[z] = (max(0., row["target"] + extra - row["actual"] - row["planned"])
                               if row.get("actual") is not None else None)
        if q_policy:
            # Preparation and taper share 110%. The separately approved
            # post-execution maintenance reserve remains one frozen dose,
            # not another percentage or a resettable target.
            objective_limits[z] = prescription_headroom(row,
                maintenance_reserve=extra if mode in {"POST_TARGET_MAINTENANCE", "PLANNED_EASY_MAINTENANCE"}
                    or preparation and reserve.get("easy_q", {}).get("Z1", 0.) > 0 else 0.)
            index_only.append(z)
        if z in q_remaining:
            q_limits[z] = objective_limits[z] or 0.
        # Preserve the original rolling 7/40 envelope even when its anchored
        # prescription target is already covered. A reserve is not load-free.
        rolling = budgets[z].get("rolling_deficit_effective", budgets[z]["deficit_effective"])
        e_limits[z] = (rolling if q_policy else min(rolling,
            max(0., row["target_effective"] + extra_e[z]
                - row["actual_effective"] - row["planned_effective"])))
    return {"mode": mode, "q": q_limits, "e": e_limits, "objective": objective_limits,
            "reserve_q": extra_q, "reserve_e": extra_e, "index_only_components": index_only,
            "shared_easy_reserve": bool(mode and reserve.get("easy_q", {}).get("Z1", 0.) > 0 and
                (preparation or zone in {"Z1", "Z2"}))}


def fits(direct, effective, headroom, components):
    return (all(effective[z] <= headroom["e"][z] + .001 for z in COMPONENTS)
            and all(direct[z] <= v + .001 for z, v in headroom["q"].items())
            and all(headroom["objective"][z] is not None and
                    (direct[z] if components[z]["basis"] == "DIRECT_Q" else effective[z])
                    <= headroom["objective"][z] + .005 for z in COMPONENTS))


def taper_limits(components, budgets, q_remaining, *, zone, light=False,
                 preparation=False, manual=()):
    """At most 10% extra in easy components, charged against original targets.

    The caller must establish supported history, taper and ordinary readiness,
    method/time eligibility. High-zone work never receives extra Q or E here.
    Both the microcycle and prospective rolling E ceilings remain bounded;
    adding a percentage to a clipped remainder would erase prior overshoot.
    """
    result = limits(components, budgets, q_remaining,
                    {"q": {}, "e": {}, "references": {}}, zone=zone)
    zones, mode = set(), None
    if light and zone in {"Z1", "Z2"}:
        zones, mode = {zone, "Z1"}, "TAPER_LIGHT_MAINTENANCE"
    elif preparation and zone not in {"Z1", "Z2"} and (components[zone]["remaining"] or 0.) > .005:
        zones, mode = {"Z1"}, "TAPER_QUALITY_PREPARATION"
    result["original_e"] = {z: budgets[z]["deficit_effective"] for z in COMPONENTS}
    result["rolling_limits"] = {}
    for z in zones - set(manual):
        row, budget = components[z], budgets[z]
        if budget.get("effective_limit_basis") == "PROSPECTIVE_CANONICAL_7_40":
            result["mode"] = mode
            continue  # The reduced taper target already owns its 10% Q band.
        if (row["basis"] != "DIRECT_Q" or row.get("actual_q") is None
                or row.get("planned_q") is None or row["target"] <= 0
                or row["target_effective"] <= 0 or budget["target_weekly_effective"] <= 0):
            continue
        extra_q, extra_e = row["target"]*TAPER_FRACTION, row["target_effective"]*TAPER_FRACTION
        q_left = max(0., row["target"] + extra_q - row["actual_q"] - row["planned_q"])
        rolling_limit = budget["target_weekly_effective"]*(1+TAPER_FRACTION)
        result["q"][z] = result["objective"][z] = q_left
        result["e"][z] = min(max(0., rolling_limit - budget["rolling_7d_effective"]),
            max(0., row["target_effective"] + extra_e - row["actual_effective"] - row["planned_effective"]))
        result["reserve_q"][z], result["reserve_e"][z] = extra_q, extra_e
        result["rolling_limits"][z] = {"original": budget["target_weekly_effective"],
            "with_reserve": rolling_limit, "used": budget["rolling_7d_effective"]}
        result["mode"] = mode
    return result


def explanation(mode):
    return {
        "POST_TARGET_MAINTENANCE": "Използван е ограничен поддържащ резерв след реално изпълнена цел.",
        "PLANNED_EASY_MAINTENANCE": "Използван е общият седмичен резерв за лека работа след разпределената цел. Допълнителният товар участва в 7/40 и Recovery; ново преизчисление не възстановява резерва.",
        "QUALITY_PREPARATION_RESERVE": "Подготовката и паузите използват общия резерв за Z1. Той се споделя с леките поддържащи задачи; допустимото отклонение до 10% не се добавя повторно.",
        "TAPER_LIGHT_MAINTENANCE": "Използван е ограничен тейпър резерв до 10% от целта за лека работа в Z1–Z2. Реалният и планираният товар изчерпват същия резерв; Recovery и дневният тейпър лимит остават в сила.",
        "TAPER_QUALITY_PREPARATION": "Използван е ограничен тейпър резерв до 10% в Z1 само за подготовката на допустим основен компонент; основната работа не получава допълнителен бюджет.",
    }[mode]


def evidence(direct, effective, headroom, segment):
    """Keep the target and exception visible without erasing any overshoot."""
    if not headroom.get("mode"):
        return None
    rows = segment["components"]
    extra = {z: max(0., direct[z] - (rows[z]["remaining"] or 0.)) for z in COMPONENTS}
    extra_e = {z: max(0., effective[z] - rows[z]["remaining_effective"])
               if z not in headroom.get("index_only_components", []) else 0. for z in COMPONENTS}
    if "original_e" in headroom:
        extra_e = {z: max(extra_e[z], effective[z] - headroom["original_e"][z])
                   if z not in headroom.get("index_only_components", []) else 0. for z in COMPONENTS}
    if not any(v > .005 for v in [*extra.values(), *extra_e.values()]):
        return None
    result = {"version": VERSION, "mode": headroom["mode"],
            "window_start": segment["window_start"], "window_end": segment["window_end"],
            "additional_q": {z: round(v, 3) for z, v in extra.items() if v > .005},
            "additional_effective": {z: round(v, 3) for z, v in extra_e.items() if v > .005},
            "reserve_q": {z: round(v, 3) for z, v in headroom["reserve_q"].items() if v > .005},
            "shared_easy_reserve": headroom.get("shared_easy_reserve", False),
            "support_q": {z: round(v, 3) for z, v in extra.items() if v > .005
                and headroom["reserve_q"].get(z, 0.) > 0 and (headroom.get("shared_easy_reserve")
                    or headroom["mode"] in {"PLANNED_EASY_MAINTENANCE", "POST_TARGET_MAINTENANCE"})},
            "basis": "COACH_POLICY_NOT_PHYSIOLOGICAL_THRESHOLD",
            "rolling_limits_preserved": not bool(headroom.get("rolling_limits"))}
    if headroom.get("rolling_limits"):
        result.update(maximum_excess_fraction=TAPER_FRACTION,
            reserve_effective={z: round(v, 3) for z, v in headroom["reserve_e"].items() if v > .005},
            rolling_limits=headroom["rolling_limits"],
            effective_excess_scope="MAX_OF_MICROCYCLE_AND_ROLLING_EXCESS_NOT_SUM")
    return result
