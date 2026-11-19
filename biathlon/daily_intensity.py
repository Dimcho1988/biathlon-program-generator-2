"""Shared Z3–Z5 method-dose ledger; a coaching guard, not a fatigue model.

Only direct work spends this ledger. Warmup, recovery and cross-zone E effects
remain in the existing canonical load/recovery checks, never counted twice here.
"""
import math

VERSION = "daily-intensity-v1-shared-method-doses"
HIGH = frozenset({"Z3", "Z4", "Z5"})
MAINTENANCE_LIMIT = .6
BUILDING_LIMIT = 1.
EPS = 1e-6


def high_work(blocks):
    return [b for b in blocks if b.get("kind") == "WORK" and b.get("zone") in HIGH
            and not b.get("neuromuscular")]


def usage(blocks, references):
    total = 0.
    for block in high_work(blocks):
        reference = next((r for r in references
            if r.get("part") == block.get("combination_part")
            and block["zone"] in r["zones"]), None)
        if reference is None or not math.isfinite(reference["minutes"]) or reference["minutes"] <= 0:
            return None  # No silent zero for a missing high-zone denominator.
        total += block["duration_min"] / reference["minutes"]
    return total


def actual_usage(activities, contexts, building_fraction):
    """Unlabelled execution uses Q/Tmax at the same zone reference.

    This is an explicit conservative estimate, not a reconstructed interval
    recipe. Q already includes intensity; do not multiply it by E spill again.
    """
    total = 0.
    for activity in activities:
        capacities = (contexts.get(activity.get("sport")) or {}).get("minutes", {})
        for row in activity.get("zones", []):
            if row.get("zone") not in HIGH:
                continue
            q = row.get("equivalent_time_min")
            raw = row.get("raw_time_min") or 0.
            if q is None:
                if raw > 0:
                    return None
                continue
            if not math.isfinite(q) or q < 0:
                return None
            if q == 0:
                continue
            capacity = capacities.get(row["zone"])
            if not capacity or not math.isfinite(capacity) or capacity <= 0:
                return None
            total += q / (capacity * building_fraction)
    return total


def session_usage(session):
    if not high_work(session.get("blocks", [])):
        return 0.
    contract = session.get("dose_evidence", {}).get("daily_intensity", {})
    if contract.get("version") != VERSION:
        return None
    if contract.get("exception") == "EXPLICIT_DOUBLE_THRESHOLD":
        return .5 if session.get("double_threshold_part") else 1.
    return usage(session["blocks"], contract.get("references", []))


def saved_contract(session):
    """Upgrade known saved nominal-dose evidence without changing its blocks.

    Missing evidence remains unavailable. The engine still revalidates saved
    method dose, readiness, Q/E and explicit double-threshold configuration.
    """
    evidence = session.get("dose_evidence", {})
    current = evidence.get("daily_intensity")
    if current and current.get("version") == VERSION:
        return current
    if session.get("double_threshold"):
        return {"version": VERSION, "references": [], "building": True,
                "exception": "EXPLICIT_DOUBLE_THRESHOLD"}
    budgets = evidence.get("combined_budgets")
    capacities = evidence.get("combined_capacities")
    if budgets and (not capacities or len(budgets) != len(capacities)):
        return None
    if not budgets:
        if not evidence.get("nominal_dose_policy") or evidence.get("base_fraction") is None:
            return None
        budgets = [{"zone": session["zone"], "role": session["purpose"],
                    "full_role_fraction": evidence.get("structure_base_fraction", evidence["base_fraction"]),
                    "method_id": session["method_id"]}]
        capacities = [evidence]
    references = []
    building = False
    for i, (budget, cap) in enumerate(zip(budgets, capacities)):
        role = budget["role"]
        reduced = role in {"MAINTENANCE", "SUPPORTING", "ACTIVATION", "RECOVERY"}
        fraction = budget.get("full_role_fraction")
        capacity = cap.get("capacity_minutes")
        if not capacity or fraction is None or fraction <= 0:
            return None
        full = capacity * fraction / (.6 if reduced else 1.)
        if role == "BUILDING":
            full = max(full * cap.get("readiness_dose_factor", 1.), cap.get("whole_role_work_minutes", 0.))
        library = cap.get("method_library")
        references.append({"part": i+1 if evidence.get("combined_budgets") else None,
            "zones": sorted(HIGH) if library else [budget["zone"]], "minutes": full,
            "method_id": budget["method_id"], "role": role})
        building |= role == "BUILDING" and (bool(library) or budget["zone"] in HIGH)
    result = {"version": VERSION, "references": references, "building": building,
              "reference_source": "PRESERVED_NOMINAL_METHOD_DOSE"}
    return result if usage(session.get("blocks", []), references) is not None else None


def ledger(activities, sessions, contexts, building_fraction):
    actual = actual_usage(activities, contexts, building_fraction)
    costs = [session_usage(s) for s in sessions]
    return {"used": None if actual is None or any(v is None for v in costs) else actual + sum(costs),
            "actual": actual,
            "building": any(s.get("dose_evidence", {}).get("daily_intensity", {}).get("building")
                            and high_work(s.get("blocks", [])) for s in sessions),
            "double": any(s.get("double_threshold") for s in sessions)}


def check(blocks, contract, before):
    cost = usage(blocks, contract.get("references", []))
    if not high_work(blocks):
        return True
    if before["used"] is None or before.get("double"):
        return False
    if contract.get("exception") == "EXPLICIT_DOUBLE_THRESHOLD":
        return before["used"] <= EPS
    limit = BUILDING_LIMIT if before["building"] or contract["building"] else MAINTENANCE_LIMIT
    return cost is not None and before["used"] + cost <= limit + EPS


def advance(before, blocks, contract):
    """Advance speculative reservations with the same rule as actual choices."""
    if not high_work(blocks):
        return dict(before)
    double = contract.get("exception") == "EXPLICIT_DOUBLE_THRESHOLD"
    cost = 1. if double else usage(blocks, contract.get("references", []))
    return {**before, "used": None if before["used"] is None or cost is None else before["used"] + cost,
            "building": before["building"] or contract["building"], "double": before.get("double") or double}


def summary(before, sessions):
    costs = [session_usage(s) for s in sessions]
    used = None if before["used"] is None or any(v is None for v in costs) else before["used"] + sum(costs)
    double = before.get("double") or any(s.get("double_threshold") for s in sessions)
    building = before["building"] or any(s.get("dose_evidence", {}).get("daily_intensity", {}).get("building")
                                        and high_work(s.get("blocks", [])) for s in sessions)
    limit = BUILDING_LIMIT if building or double else MAINTENANCE_LIMIT
    return {"version": VERSION, "basis": "COACH_POLICY_NOT_VALIDATED_PHYSIOLOGICAL_MAXIMUM",
            "scope": "ALL_SPORTS_AND_SESSIONS_SAME_DAY", "zones": sorted(HIGH),
            "used_fraction": used, "limit_fraction": limit,
            "actual_fraction": before["actual"], "actual_basis": "DIRECT_Q_OVER_ZONE_REFERENCE_DEVELOPMENT_DOSE",
            "exception": "EXPLICIT_DOUBLE_THRESHOLD" if double else None}
