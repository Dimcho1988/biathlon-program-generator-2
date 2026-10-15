"""Bounded alternative schedules, replayed from the same real inputs.

An unsuccessful search is not proof of physiological impossibility. Complete
engine replays preserve calendar locks, actual work, whole structures, causal
Recovery and all Q/E and time gates. Ties retain the first stable schedule.
"""
from time import monotonic


def score(plan, accents):
    rows = (plan.get("allocation") or {}).get("components", {})
    def coverage(z):
        row = rows[z]
        return min(1., (row.get("actual") or 0.) / max(1., row["target"]) + row["planned"] / max(1., row["target"]))
    support = [z for z in rows if z not in accents and rows[z]["target"] > 0]
    return (round(sum(coverage(z) for z in accents if z in rows), 5),
            round(min((coverage(z) for z in support), default=1.), 5),
            round(sum(coverage(z) for z in rows), 5))


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
    unmet = [z for z in accents if (allocation["components"].get(z, {}).get("remaining") or 0) > 1.]
    trials, best = 0, baseline
    if unmet:
        easy = {d["date"]: sum(s["main_work_minutes"] for s in d.get("sessions", []) if s["zone"] in {"Z1", "Z2"})
                for d in baseline["days"] if not d.get("locked")}
        # Three deterministic alternatives bound cost independently of athlete count.
        for fraction in (.95, .85, .7):
            if monotonic()+1.5*estimated_trial_seconds >= deadline:
                break
            caps = {day: work*fraction for day, work in easy.items() if work > 0}
            if not caps:
                break
            began = monotonic()
            candidate = replay(caps)
            estimated_trial_seconds = max(estimated_trial_seconds, monotonic()-began)
            trials += 1
            if score(candidate, accents) > score(best, accents):
                best = candidate
    best["allocation"]["search"] = {"version": "bounded-causal-replay-v1", "alternatives": trials,
        "improved": best is not baseline, "priority": ["ACCENT", "SUPPORT_MINIMUM", "OVERALL", "STABILITY"],
        "conclusion": "SEARCH_BUDGET_EXHAUSTED" if any((best["allocation"]["components"].get(z, {}).get("remaining") or 0) > 1. for z in accents) else "ACCENTS_COVERED",
        "hard_limit_proven": False}
    return best
