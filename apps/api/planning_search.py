"""Bounded alternative schedules, replayed from the same real inputs.

An unsuccessful search is not proof of physiological impossibility. Complete
engine replays preserve calendar locks, actual work, whole structures, causal
Recovery and all Q/E and time gates. Ties retain the first stable schedule.
"""
from datetime import date
from time import monotonic

from biathlon import planning_allocation


def reservation_methods(plan):
    """Reconcile guesses against complete, actually selected future methods."""
    return {d["date"]:[{k:s[k] for k in ("method_id", "sport", "zone", "slot", "purpose", "is_key_session") if k in s}
                       for s in d.get("sessions", []) if s.get("method_id") and s.get("sport")]
            for d in [*plan["days"], *plan.get("lookahead_days", [])] if not d.get("locked")}


def score(plan, accents):
    """Reduce every local component gap before using accent tie-breakers.

    A 100% Z1 cannot compensate for a 20% Z3. Above-target work also has a
    cost, so 110% is an acceptance bound rather than a new optimization goal.
    Actual excess is irreversible and cannot justify any extra planned work.
    """
    allocation = plan.get("allocation") or {}
    rows = planning_allocation.objective_rows(allocation)
    accent_credit = [min(1., ((row.get("actual") or 0.)+row.get("planned", 0.))/row["target"])
                     for z, row in rows if z in accents and row["target"] > 0
                     and row.get("actual", 0.) is not None and row.get("planned", 0.) is not None]
    preparation_cost = sum(
        sum(item.get("additional_q", {}).values()) / max(1., allocation.get("components", {}).get("Z1", {}).get("target", 0.))
        for item in allocation.get("continuity", {}).get("sessions", [])
        if item.get("mode") == "QUALITY_PREPARATION_RESERVE")
    sessions = [s for d in [*plan.get("days", []), *plan.get("lookahead_days", [])] for s in d.get("sessions", [])]
    return (*planning_allocation.gap_score(rows), round(sum(accent_credit), 5),
            -round(preparation_cost, 5), -len(sessions))


def improve(baseline, profile, replay, *, seconds_remaining=150., estimated_trial_seconds=0.):
    deadline = monotonic() + seconds_remaining
    allocation = baseline.get("allocation")
    if not allocation:
        return baseline
    accents = {z for d in baseline["days"] for z, row in d.get("load_budget", {}).get("components", {}).items()
               if (row.get("goal_evidence") or {}).get("is_accent")}
    accents.update(z for d in baseline["days"] for z in (d.get("cycle") or {}).get("accents", []))
    accents.update(s["zone"] for d in baseline["days"] for s in d.get("sessions", [])
                   if s.get("dose_evidence", {}).get("selection", {}).get("accent"))
    controls = profile.get("planning_controls") or {}
    accents.update(controls.get("accents", []))
    # Accents only break ties after all component gaps have been compared.
    accents = accents or {z for z in ("Z3", "Z4", "Z5", "STR") if allocation["components"].get(z, {}).get("target", 0.) > 0}
    unmet = planning_allocation.needs_search(allocation)
    trials, refits, best = 0, 0, baseline
    budget_exhausted = False
    def time_available():
        nonlocal budget_exhausted
        if monotonic()+1.1*estimated_trial_seconds >= deadline:
            budget_exhausted = True
            return False
        return True
    if unmet:
        easy = {d["date"]: sum(s["main_work_minutes"] for s in d.get("sessions", []) if s["zone"] in {"Z1", "Z2"})
                for d in baseline["days"] if not d.get("locked")}
        # Explore the optional Z1 finishes and the balancing heuristic too.
        # Those are scheduling choices, not physiological constraints. Every
        # alternative replays the full engine with the same Q/E/dose gates.
        policies = [
            {"optional_easy_fraction": 0., "balance_complete_doses": False, "component_gap_first": True},
            {"optional_easy_fraction": .5, "balance_complete_doses": False, "component_gap_first": True},
            {"optional_easy_fraction": 1., "balance_complete_doses": False, "component_gap_first": True},
            {"optional_easy_fraction": 0., "balance_complete_doses": False},
            {"optional_easy_fraction": .5, "easy_caps": {d:w*.85 for d,w in easy.items() if w > 0}},
            {"optional_easy_fraction": 0., "balance_complete_doses": False, "prefer_standalone": True},
        ]
        for policy in policies:
            if not planning_allocation.needs_search(best["allocation"]):
                break
            if not time_available():
                break
            began = monotonic()
            candidate = replay(policy)
            estimated_trial_seconds = max(estimated_trial_seconds, monotonic()-began)
            trials += 1
            if score(candidate, accents) > score(best, accents):
                best = candidate
        # Re-run causally with the selected future methods after exploring
        # the scheduling heuristics. A changed reservation never edits blocks in place:
        # every new repetition and every later dose passes the full engine.
        if planning_allocation.needs_search(best["allocation"]) and any(s.get("method_id") for d in baseline["days"] for s in d.get("sessions", [])):
            for _ in range(2):
                if not time_available():
                    break
                began = monotonic()
                candidate = replay({**((best.get("parameters") or {}).get("allocation_policy") or {}),
                    "reservation_methods":reservation_methods(best)})
                estimated_trial_seconds = max(estimated_trial_seconds, monotonic()-began)
                trials += 1
                refits += 1
                if score(candidate, accents) <= score(best, accents):
                    break
                best = candidate
        if planning_allocation.needs_search(best["allocation"]) and time_available():
            # Cap only early Z1 work; keep the Z2 accent intact and leave later
            # empty training days uncapped. This is another causal trial, not
            # an instruction to fill rest days or invent short fragments.
            easy = {d["date"]:sum(s["main_work_minutes"] for s in d.get("sessions", []) if s["zone"] == "Z1")
                    for d in best["days"] if not d.get("locked")}
            first_easy = min((d for d, work in easy.items() if work > 0), default=None)
            slots = controls.get("sessions_by_day") or [1]*7
            later_empty = first_easy and any(
                d["date"] > first_easy and not d.get("locked") and not d.get("sessions")
                and slots[date.fromisoformat(d["date"]).weekday()] > 0 for d in best["days"])
            if later_empty:
                candidate = replay({**((best.get("parameters") or {}).get("allocation_policy") or {}),
                    "reservation_methods":reservation_methods(best),
                    "easy_caps":{d:w*.75 for d,w in easy.items() if w > 0},
                    "easy_cap_zones":["Z1"], "spread_recovery":True})
                trials += 1
                if score(candidate, accents) > score(best, accents):
                    best = candidate
    best["allocation"]["search"] = {"version": "bounded-causal-replay-v5-component-targets", "alternatives": trials,
        "reservation_replays":refits,
        "improved": best is not baseline, "priority": ["WORST_COMPONENT_RANGE_GAP", "SUM_COMPONENT_RANGE_GAPS", "DISTANCE_TO_100_PERCENT", "ACCENT", "PREPARATION_EXCESS", "FEWER_SESSIONS", "STABILITY"],
        "conclusion": ("TARGETS_WITHIN_TOLERANCE" if planning_allocation.acceptance(best["allocation"])["within_tolerance"]
            and best["allocation"].get("has_unallocated_load", any((row.get("remaining") or 0) > 1. for _, row in planning_allocation.objective_rows(best["allocation"])))
            else "TARGETS_COVERED" if planning_allocation.acceptance(best["allocation"])["within_tolerance"]
            else "SEARCH_BUDGET_EXHAUSTED" if budget_exhausted else "CHECKED_VARIANTS_EXHAUSTED"),
        "hard_limit_proven": False}
    return best
