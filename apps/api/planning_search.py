"""Bounded alternative schedules, replayed from the same real inputs.

An unsuccessful search is not proof of physiological impossibility. Complete
engine replays preserve calendar locks, actual work, whole structures, causal
Recovery and all Q/E and time gates. Ties retain the first stable schedule.
"""
from time import monotonic


def reservation_methods(plan):
    """Reconcile guesses against complete, actually selected future methods."""
    return {d["date"]:[{k:s[k] for k in ("method_id", "sport", "zone", "slot", "purpose", "is_key_session") if k in s}
                       for s in d.get("sessions", []) if s.get("method_id") and s.get("sport")]
            for d in [*plan["days"], *plan.get("lookahead_days", [])] if not d.get("locked")}


def score(plan, accents):
    rows = (plan.get("allocation") or {}).get("components", {})
    segments = (plan.get("allocation") or {}).get("segments") or []
    def coverage(z):
        if segments:
            parts = [s["components"][z] for s in segments if z in s["components"]]
            # An overshoot in the previous cycle cannot fill the next one.
            return sum(min(r["target"], (r.get("actual") or 0.)+r["planned"]) for r in parts) / max(1.,sum(r["target"] for r in parts))
        row = rows[z]
        return min(1., (row.get("actual") or 0.) / max(1., row["target"]) + row["planned"] / max(1., row["target"]))
    support = [z for z in rows if z not in accents and rows[z]["target"] > 0]
    # A preparation exception rescues an otherwise blocked quality. Prefer an
    # equally covered plan that distributes the original allowance instead.
    # Post-target maintenance is deliberate and is not penalized as excess.
    preparation_cost = sum(
        sum(item.get("additional_q", {}).values()) / max(1., rows.get("Z1", {}).get("target", 0.))
        for item in (plan.get("allocation") or {}).get("continuity", {}).get("sessions", [])
        if item.get("mode") == "QUALITY_PREPARATION_RESERVE")
    return (round(sum(coverage(z) for z in accents if z in rows), 5),
            round(min((coverage(z) for z in support), default=1.), 5),
            round(sum(coverage(z) for z in rows)-preparation_cost, 5),
            sum(bool(d.get("sessions")) for d in plan.get("days", [])))


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
    # Key quality is the first priority where the selected accent is unmet.
    accents = accents or {z for z in ("Z3", "Z4", "Z5", "STR") if allocation["components"][z]["target"] > 0}
    objective_rows = [r for segment in allocation.get("segments", []) for r in segment["components"].values()] or list(allocation["components"].values())
    unmet = any((row.get("remaining") or 0) > 1. for row in objective_rows)
    trials, refits, best = 0, 0, baseline
    if unmet:
        easy = {d["date"]: sum(s["main_work_minutes"] for s in d.get("sessions", []) if s["zone"] in {"Z1", "Z2"})
                for d in baseline["days"] if not d.get("locked")}
        # Explore the optional Z1 finishes and the balancing heuristic too.
        # Those are scheduling choices, not physiological constraints. Every
        # alternative replays the full engine with the same Q/E/dose gates.
        policies = [
            {"optional_easy_fraction": 0., "balance_complete_doses": False},
            {"optional_easy_fraction": .5, "easy_caps": {d:w*.85 for d,w in easy.items() if w > 0}},
            {"optional_easy_fraction": 0., "balance_complete_doses": False, "prefer_standalone": True},
        ]
        # Re-run causally with the selected future methods before exploring
        # generic heuristics. A changed reservation never edits blocks in place:
        # every new repetition and every later dose passes the full engine.
        if any(s.get("method_id") for d in baseline["days"] for s in d.get("sessions", [])):
            for _ in range(2):
                if monotonic()+1.5*estimated_trial_seconds >= deadline:
                    break
                began = monotonic()
                candidate = replay({"reservation_methods":reservation_methods(best)})
                estimated_trial_seconds = max(estimated_trial_seconds, monotonic()-began)
                trials += 1
                refits += 1
                if score(candidate, accents) <= score(best, accents):
                    break
                best = candidate
        for policy in policies:
            if monotonic()+1.5*estimated_trial_seconds >= deadline:
                break
            began = monotonic()
            candidate = replay({**policy, **({"reservation_methods":reservation_methods(best)} if refits else {})})
            estimated_trial_seconds = max(estimated_trial_seconds, monotonic()-began)
            trials += 1
            if score(candidate, accents) > score(best, accents):
                best = candidate
        if monotonic()+1.5*estimated_trial_seconds < deadline:
            # Cap only early Z1 work; keep the Z2 accent intact and leave later
            # empty training days uncapped. This is another causal trial, not
            # an instruction to fill rest days or invent short fragments.
            easy = {d["date"]:sum(s["main_work_minutes"] for s in d.get("sessions", []) if s["zone"] == "Z1")
                    for d in best["days"] if not d.get("locked")}
            if any(easy.values()):
                candidate = replay({**((best.get("parameters") or {}).get("allocation_policy") or {}),
                    "reservation_methods":reservation_methods(best),
                    "easy_caps":{d:w*.75 for d,w in easy.items() if w > 0},
                    "easy_cap_zones":["Z1"], "spread_recovery":True})
                trials += 1
                if score(candidate, accents) > score(best, accents):
                    best = candidate
    best["allocation"]["search"] = {"version": "bounded-causal-replay-v4-continuity", "alternatives": trials,
        "reservation_replays":refits,
        "improved": best is not baseline, "priority": ["ACCENT", "SUPPORT_MINIMUM", "COVERAGE_MINUS_PREPARATION_EXCESS", "TRAINING_DAYS", "STABILITY"],
        "conclusion": "SEARCH_BUDGET_EXHAUSTED" if best["allocation"].get("has_unallocated_load",
            any((row.get("remaining") or 0) > 1. for row in best["allocation"]["components"].values())) else "TARGETS_COVERED",
        "hard_limit_proven": False}
    return best
