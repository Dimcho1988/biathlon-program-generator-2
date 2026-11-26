"""Bounded alternative schedules, replayed from the same real inputs.

An unsuccessful search is not proof of physiological impossibility. Complete
engine replays preserve calendar locks, actual work, whole structures, causal
Recovery and all Q/E and time gates. Ties retain the first stable schedule.
"""
from datetime import date
import json
from time import monotonic

from biathlon import planning_allocation, planning_development

MAX_UNIQUE_REPLAYS = 12  # The former maximum across every replay stage.


def reservation_methods(plan):
    """Reconcile guesses against complete, actually selected future methods."""
    return {d["date"]:[{k:s[k] for k in ("method_id", "sport", "zone", "slot", "purpose", "is_key_session") if k in s}
                       for s in d.get("sessions", []) if s.get("method_id") and s.get("sport")]
            for d in [*plan["days"], *plan.get("lookahead_days", [])] if not d.get("locked")}


def development_shortfalls(plan):
    """Use the engine's real-dose, whole-cycle count; never infer from labels."""
    development = (plan.get("allocation") or {}).get("development") or {}
    return (development.get("minimum_shortfall", 0), development.get("target_shortfall", 0))


def needs_search(plan):
    return (planning_allocation.needs_search(plan.get("allocation") or {})
            or any(development_shortfalls(plan)))


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
    training_days = len({d["date"] for d in [*plan.get("days", []), *plan.get("lookahead_days", [])] if d.get("sessions")})
    worst_gap, total_gap, distance = planning_allocation.gap_score(rows)
    minimum_shortfall, target_shortfall = development_shortfalls(plan)
    # Within the accepted component band, real developmental exposures precede
    # a smaller numerical rounding error. Outside-band deficits still lead;
    # three sessions cannot buy missing work in another component or cycle.
    return (worst_gap, total_gap, -minimum_shortfall, -target_shortfall, distance,
            training_days, round(sum(accent_credit), 5),
            -round(preparation_cost, 5), -len(sessions))


def cycle_shortfalls(plan):
    """Repair only known active targets in cycles with a resolved training role."""
    allocation = plan.get("allocation") or {}
    roles = {r["window_start"]: r for r in (allocation.get("development") or {}).get("segments", [])}
    result = {}
    for segment in allocation.get("segments", []):
        first = segment["window_start"]
        role = roles.get(first, {})
        if not (role.get("assessed") or role.get("kind") in {"MAINTAIN", "RECOVERY"}):
            continue
        gaps = {}
        for zone, row in segment["components"].items():
            if row.get("basis") != "DIRECT_Q" or row["target"] <= 0:
                continue
            if planning_allocation.assess_component(row)["coverage_status"] == "BELOW_RANGE":
                total = row["actual"] + row["planned"] - row.get("support_reserve_q", 0.)
                gaps[zone] = (.9 * row["target"] - total) / row["target"]
        if gaps:
            result[first] = gaps
    return result


def building_reservations(plan, cycles):
    """Only reserve real developmental doses in the affected cycles."""
    windows = [(s["window_start"], s["window_end"]) for s in plan["allocation"]["segments"]
               if s["window_start"] in cycles]
    reserved = {}
    for day in [*plan["days"], *plan.get("lookahead_days", [])]:
        if day.get("locked") or not any(start <= day["date"] <= end for start, end in windows):
            continue
        sessions = []
        for session in day.get("sessions", []):
            if not planning_development.is_developmental(session):
                continue
            reservation = {k: session[k] for k in ("method_id", "sport", "zone", "slot", "purpose", "is_key_session")}
            support = (session["dose_evidence"].get("continuity") or {}).get("support_q", {})
            reservation["objective_q"] = {z: max(0., v - support.get(z, 0.))
                                          for z, v in session["direct_equivalent_minutes"].items() if v > 0}
            sessions.append(reservation)
        if sessions:
            reserved[day["date"]] = sessions
    return reserved


def covered_components(plan):
    return {(s["window_start"], s["window_end"], z)
            for s in plan["allocation"].get("segments", []) for z, row in s["components"].items()
            if planning_allocation.assess_component(row)["coverage_status"] == "WITHIN_RANGE"}


def preparation_repair_policy(plan):
    """Try a compact whole method where preparation crowds out a later zone.

    This is a search choice, not a new dose ceiling. Dates, donor components
    and minimum structures come from the candidate; no athlete-specific caps.
    A complete causal replay and the common component score decide acceptance.
    """
    days = [*plan.get("days", []), *plan.get("lookahead_days", [])]
    choices = []
    for segment in (plan.get("allocation") or {}).get("segments", []):
        rows = segment["components"]
        short = {z for z, r in rows.items() if z in {"Z2", "Z3", "Z4", "Z5"}
                 and r.get("basis") == "DIRECT_Q" and r.get("actual") is not None
                 and r.get("planned") is not None and r["actual"]+r["planned"] < .9*r["target"]}
        if not short:
            continue
        local = [d for d in days if segment["window_start"] <= d["date"] <= segment["window_end"]]
        for day in local:
            if day.get("locked") or day.get("status") == "EXISTING_ACTIVITY":
                continue
            if not any(d["date"] > day["date"] and (
                any(s.get("zone") in short for s in d.get("sessions", [])) or
                any("Z1" in r.get("blocking_components", []) for r in d.get("rejected_alternatives", []))) for d in local):
                continue
            for session in day.get("sessions", []):
                z = session.get("zone")
                row = rows.get(z, {})
                if (z not in {"Z2", "Z3", "Z4", "Z5"} or z in short or row.get("basis") != "DIRECT_Q"
                        or row.get("actual") is None or row.get("planned") is None
                        or row["actual"]+row["planned"] <= .9*row["target"]):
                    continue
                q = session.get("direct_equivalent_minutes") or {}
                minimum = (session.get("dose_evidence") or {}).get("minimum_structure_work_minutes")
                work = session.get("main_work_minutes", 0)
                if minimum is not None and 0 < minimum < work and q.get(z, 0) > 0 and q.get("Z1", 0) > 0:
                    choices.append((q["Z1"]/q[z], day["date"], z, minimum))
        if choices:
            break  # Repair the earliest incomplete cycle before later ones.
    if not choices:
        return None
    _, key, zone, minimum = max(choices)
    policy = (plan.get("parameters") or {}).get("allocation_policy") or {}
    return {**policy, "easy_caps": {**policy.get("easy_caps", {}), key: minimum},
            "easy_cap_zones": sorted(set(policy.get("easy_cap_zones", [])) | {zone})}


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
    def empty_requested_day(plan):
        slots = controls.get("sessions_by_day") or [1]*7
        available = profile.get("available_minutes") or [1]*7
        return any(not d.get("sessions") and not d.get("locked") and d.get("status", "REST") == "REST"
            and slots[date.fromisoformat(d["date"]).weekday()] > 0
            and available[date.fromisoformat(d["date"]).weekday()] > 0 for d in plan["days"])
    # Accents only break ties after all component gaps have been compared.
    accents = accents or {z for z in ("Z3", "Z4", "Z5", "STR") if allocation["components"].get(z, {}).get("target", 0.) > 0}
    unmet = needs_search(baseline)
    trials, refits, best = 0, 0, baseline
    budget_exhausted = False
    evaluated = {}
    capabilities = (baseline.get("parameters") or {}).get("allocation_policy_capabilities") or {}
    optional_easy_inactive = capabilities.get("optional_easy_fraction") is False
    def evaluate(policy):
        nonlocal estimated_trial_seconds, trials, refits, budget_exhausted
        # Every replay uses this invocation's immutable inputs. Different
        # stages may request the same policy and reservation map; reuse that
        # exact result without charging another full calculation. Do not seed
        # the baseline: its generating policy is not necessarily known here.
        # Only the full resolved catalogue can establish that this option has
        # no effect. A selected plan without MIX work does not establish it;
        # absent/unknown capabilities retain the exact-policy fallback.
        effective_policy = ({key: value for key, value in policy.items() if key != "optional_easy_fraction"}
                            if optional_easy_inactive else policy)
        key = json.dumps(effective_policy, sort_keys=True, separators=(",", ":"))
        if key not in evaluated:
            if trials >= MAX_UNIQUE_REPLAYS or monotonic()+1.1*estimated_trial_seconds >= deadline:
                budget_exhausted = True
                return None
            began = monotonic()
            evaluated[key] = replay(policy)
            estimated_trial_seconds = max(estimated_trial_seconds, monotonic()-began)
            trials += 1
            refits += int("reservation_methods" in policy)
        # Search metadata is added only after all candidate comparisons, so
        # the cached result remains unchanged throughout this search.
        return evaluated[key]
    def better(candidate, incumbent):
        if score(candidate, accents) <= score(incumbent, accents):
            return False
        old = (incumbent["allocation"].get("development") or {}).get("segments", [])
        new = {r["window_start"]: r for r in (candidate["allocation"].get("development") or {}).get("segments", [])}
        return (covered_components(incumbent) <= covered_components(candidate)
                and all(r["window_start"] in new and new[r["window_start"]]["target_shortfall"] <= r["target_shortfall"]
                        for r in old if r.get("assessed")))

    def fill_policy(core, gaps):
        return {"component_gap_first": True, "cycle_shortfalls": gaps, "building_pass": "FILL",
                "reservation_methods": building_reservations(core, gaps)}

    # The baseline already contains legal developmental placements. Refill
    # those first, even when a busy worker can afford only one full replay.
    early_gaps = cycle_shortfalls(baseline)
    early_fill = evaluate(fill_policy(baseline, early_gaps)) if early_gaps else None
    if unmet or empty_requested_day(baseline):
        easy = {d["date"]: sum(s["main_work_minutes"] for s in d.get("sessions", []) if s["zone"] in {"Z1", "Z2"})
                for d in baseline["days"] if not d.get("locked")}
        # Explore the optional Z1 finishes and the balancing heuristic too.
        # Those are scheduling choices, not physiological constraints. Every
        # alternative replays the full engine with the same Q/E/dose gates.
        policies = [
            {"optional_easy_fraction": 0., "balance_complete_doses": False, "component_gap_first": True},
            {"optional_easy_fraction": 0., "balance_complete_doses": True, "component_gap_first": True, "spread_recovery": empty_requested_day(baseline)},
            {"optional_easy_fraction": .5, "balance_complete_doses": False, "component_gap_first": True},
            {"optional_easy_fraction": 1., "balance_complete_doses": False, "component_gap_first": True},
            {"optional_easy_fraction": 0., "balance_complete_doses": False},
            {"optional_easy_fraction": .5, "easy_caps": {d:w*.85 for d,w in easy.items() if w > 0}},
            {"optional_easy_fraction": 0., "balance_complete_doses": False, "prefer_standalone": True},
        ]
        if any(development_shortfalls(baseline)):
            # Reuse the first causal alternative; do not add another full
            # replay merely to improve the session-count objective.
            policies[0]["development_first"] = True
        if not unmet:
            policies = [policies[1]]  # Only distribution needs another trial.
        for index, policy in enumerate(policies):
            if not needs_search(best) and not empty_requested_day(best):
                break
            previous_best = best
            candidate = evaluate(policy)
            if candidate is None:
                break
            if score(candidate, accents) > score(best, accents):
                best = candidate
            # Reconcile the first quality-first schedule immediately. On a
            # slower worker the budget may permit only two complete replays;
            # broad easy-work variants must not displace this causal refit.
            if (index == 0 and needs_search(best)
                and any(reservation_methods(best).values())):
                candidate = evaluate({**((best.get("parameters") or {}).get("allocation_policy") or policy),
                    "reservation_methods":reservation_methods(best)})
                if candidate is not None and score(candidate, accents) > score(best, accents):
                    best = candidate
            # A later improvement can expose a new preparation bottleneck.
            # Try its existing whole-method repair once, before another broad
            # variant. A repair never recursively schedules another repair;
            # all stages share the same time and unique-evaluation bounds.
            repair = preparation_repair_policy(best) if index == 0 or best is not previous_best else None
            if repair:
                candidate = evaluate(repair)
                if candidate is not None and score(candidate, accents) > score(best, accents):
                    best = candidate
        # Re-run causally with the selected future methods after exploring
        # the scheduling heuristics. A changed reservation never edits blocks in place:
        # every new repetition and every later dose passes the full engine.
        if needs_search(best) and any(s.get("method_id") for d in baseline["days"] for s in d.get("sessions", [])):
            for _ in range(2):
                candidate = evaluate({**((best.get("parameters") or {}).get("allocation_policy") or {}),
                    "reservation_methods":reservation_methods(best)})
                if candidate is None or score(candidate, accents) <= score(best, accents):
                    break
                best = candidate
        if needs_search(best):
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
                candidate = evaluate({**((best.get("parameters") or {}).get("allocation_policy") or {}),
                    "reservation_methods":reservation_methods(best),
                    "easy_caps":{d:w*.75 for d,w in easy.items() if w > 0},
                    "easy_cap_zones":["Z1"], "spread_recovery":True})
                if candidate is not None and score(candidate, accents) > score(best, accents):
                    best = candidate
    # Use the best complete schedule as the reference. In particular, a
    # component covered by a previous placement must not silently regress.
    reference = best
    if early_fill is not None and better(early_fill, reference):
        best = early_fill
    gaps = cycle_shortfalls(best)
    if gaps:
        policy = {"component_gap_first": True, "cycle_shortfalls": gaps}
        def refill(core):
            candidate = evaluate(fill_policy(core, gaps))
            return candidate if candidate is not None and better(candidate, best) else best

        loading = any(r["window_start"] in gaps and r.get("assessed")
                      for r in reference["allocation"]["development"]["segments"])
        if loading and trials + 2 <= MAX_UNIQUE_REPLAYS and monotonic() + 2.2 * estimated_trial_seconds < deadline:
            # Never publish the sparse provisional pass. The fill pass
            # re-evaluates every dose and recovery state in chronological order.
            core = evaluate({**policy, "building_pass": "CORE"})
            if core is not None:
                best = refill(core)
        if cycle_shortfalls(best):
            # One replay suffices when using already verified building dates.
            best = refill(reference)
    minimum_shortfall, target_shortfall = development_shortfalls(best)
    configuration_conflict = any(row.get("activity_status") == "CONFLICT"
        for _, row in planning_allocation.objective_rows(best["allocation"]))
    coverage_met = planning_allocation.acceptance(best["allocation"])["within_tolerance"]
    if configuration_conflict:
        conclusion = "CONFIGURATION_CONFLICT"
    elif coverage_met and not target_shortfall:
        conclusion = ("TARGETS_WITHIN_TOLERANCE" if best["allocation"].get("has_unallocated_load",
            any((row.get("remaining") or 0) > 1. for _, row in planning_allocation.objective_rows(best["allocation"])))
            else "TARGETS_COVERED")
    else:
        conclusion = "SEARCH_BUDGET_EXHAUSTED" if budget_exhausted else "CHECKED_VARIANTS_EXHAUSTED"
    best["allocation"]["search"] = {"version": "bounded-causal-replay-v12-building-first", "alternatives": trials,
        "reservation_replays":refits,
        "improved": best is not baseline, "priority": ["WORST_COMPONENT_RANGE_GAP", "SUM_COMPONENT_RANGE_GAPS",
            "DEVELOPMENT_MINIMUM_SHORTFALL", "DEVELOPMENT_TARGET_SHORTFALL", "DISTANCE_TO_100_PERCENT",
            "TRAINING_DAYS", "ACCENT", "PREPARATION_EXCESS", "FEWER_SESSIONS", "STABILITY"],
        "conclusion": conclusion, "coverage_within_tolerance": coverage_met,
        "development_minimum_met": minimum_shortfall == 0, "development_target_met": target_shortfall == 0,
        "configuration_conflict": configuration_conflict,
        "hard_limit_proven": False}
    return best
