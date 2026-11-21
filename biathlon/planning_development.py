"""Coach developmental frequency, separate from high-intensity permission.

Frequency is an allocation objective, never permission to raise a dose, shorten
recovery or change a component goal. A resolved BUILDING label counts only when
its real work exceeds the same method's maintenance dose. Completed sessions
need a confirmed method; unlabelled high-zone exposure is not proof of purpose.
"""
from datetime import date
from math import isfinite

from . import planning_schedule, strength_methods

VERSION = "developmental-frequency-v1-whole-microcycle"
LOADING_KINDS = {"BUILD", "STRESS"}


def _positive(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0 else None


def policy(profile, cycle, *, limited=False, taper=False):
    """The saved key ceiling remains independent of this all-component aim."""
    cycle = cycle or {}
    configured = profile.get("max_key_sessions_per_week", 2)
    requested = int(configured) if isinstance(configured, (int, float)) and not isinstance(configured, bool) and isfinite(configured) else 2
    requested = max(0, requested)
    required = cycle.get("kind") in LOADING_KINDS and not (limited or taper or cycle.get("reentry_dose") or cycle.get("taper_priority"))
    return {"version": VERSION, "kind": cycle.get("kind"), "required": required,
            "minimum": 2 if required else 0, "target": max(2, requested) if required else 0,
            "requested": requested, "key_session_ceiling_unchanged": True,
            "basis": "COACH_FREQUENCY_OBJECTIVE_NOT_DOSE_PERMISSION"}


def _maintenance_minutes(evidence, *, nominal=None):
    if nominal is None:
        nominal = _positive(evidence.get("standard_method_work_minutes"))
    if nominal is None:
        capacity, fraction = _positive(evidence.get("capacity_minutes")), _positive(evidence.get("base_fraction"))
        nominal = capacity*fraction if capacity and fraction else None
    if nominal is None:
        requested, recovery = _positive(evidence.get("requested_primary_work_minutes")), _positive(evidence.get("readiness_dose_factor"))
        nominal = requested/recovery if requested and recovery else None
    if nominal is None:
        return None
    strength = evidence.get("strength_profile")
    fraction = strength_methods.reduced_fraction(strength) if strength else .6
    return nominal*fraction


def session_evidence(session):
    """Verify the resolved dose without turning a small BUILDING label into credit.

    The engine may provide its canonical maintenance reference under
    ``dose_evidence.developmental``. Work is still read from the actual blocks.
    Older frozen prescriptions use their existing method-budget evidence.
    """
    evidence = session.get("dose_evidence") or {}
    role = evidence.get("resolved_role", session.get("purpose"))
    result = {"counts": False, "basis": "NOT_BUILDING", "components": []}
    if role != "BUILDING" or session.get("purpose") != "BUILDING":
        return result
    blocks = [b for b in session.get("blocks", []) if b.get("kind") == "WORK" and _positive(b.get("duration_min"))]
    if not blocks:
        return {**result, "basis": "MISSING_WORK"}
    budgets = evidence.get("combined_budgets") or []
    if budgets:
        capacities = evidence.get("combined_capacities") or []
        for index, budget in enumerate(budgets, 1):
            if budget.get("role") != "BUILDING":
                continue
            own = capacities[index-1] if len(capacities) >= index else {}
            nominal = _positive(budget.get("nominal_work_minutes"))
            maintenance = _maintenance_minutes(own, nominal=nominal)
            work = sum(b["duration_min"] for b in blocks if b.get("combination_part") == index and b.get("zone") == budget.get("zone"))
            result["components"].append({"zone": budget.get("zone"), "work_minutes": work,
                "maintenance_work_minutes": maintenance, "counts": maintenance is not None and work > maintenance+.001})
    else:
        zone = session.get("zone")
        work = sum(b["duration_min"] for b in blocks if b.get("zone") == zone)
        canonical = evidence.get("developmental") or {}
        maintenance = (_positive(canonical.get("maintenance_work_minutes"))
                       if canonical.get("role", role) == "BUILDING" else None)
        if maintenance is None:
            maintenance = _maintenance_minutes(evidence)
        result["components"].append({"zone": zone, "work_minutes": work,
            "maintenance_work_minutes": maintenance, "counts": maintenance is not None and work > maintenance+.001})
    result["counts"] = any(c["counts"] for c in result["components"])
    result["basis"] = ("WORK_EXCEEDS_METHOD_MAINTENANCE" if result["counts"] else
                       "MISSING_METHOD_DOSE_REFERENCE" if not result["components"] or any(c["maintenance_work_minutes"] is None for c in result["components"]) else
                       "WORK_WITHIN_MAINTENANCE_DOSE")
    return result


def is_developmental(session):
    return session_evidence(session)["counts"]


def recorded_evidence(session):
    """Only independently confirmed execution roles can establish actual credit.

    ``RecordedSession.confirmed_methods`` is populated after successful matched
    dose/speed checks. Never infer development from an unlabelled five-minute
    high-zone exposure, which is intentionally a different key-session guard.
    A legacy BUILDING label may describe a dose reduced below maintenance; it
    needs the frozen dose classification before it can receive actual credit.
    """
    activities = session.activities
    confirmed = session.confirmed_methods
    methods = [confirmed[a.get("activity_ref")] for a in activities if a.get("activity_ref") in confirmed]
    building = any(m.get("resolved_role") == "BUILDING" and m.get("developmental", {}).get("counts") is True for m in methods)
    verified = all(m.get("resolved_role") in {"MAINTENANCE", "SUPPORTING", "RECOVERY", "ACTIVATION"}
                   or m.get("resolved_role") == "BUILDING" and isinstance(m.get("developmental", {}).get("counts"), bool)
                   for m in methods)
    known = building or bool(activities) and len(methods) == len(activities) and verified
    return {"counts": building, "known": known,
            "basis": "CONFIRMED_METHOD_EXECUTION" if known else "UNLABELLED_EXECUTION_ROLE_UNKNOWN"}


def count(days, recorded_sessions=(), *, start, end):
    """Count occasions once, retaining the legacy single-session day shape."""
    start, end = str(start), str(end)
    planned = [session_evidence(s) for d in days if start <= d["date"] <= end
               for s in planning_schedule.day_sessions(d)]
    actual = [recorded_evidence(s) for s in recorded_sessions if start <= s.date <= end]
    planned_count, actual_count = sum(s["counts"] for s in planned), sum(s["counts"] for s in actual)
    return {"planned": planned_count, "actual": actual_count, "total": planned_count+actual_count,
            "unknown_actual": sum(not s["known"] for s in actual),
            "unverified_building_labels": sum(s["basis"] in {"MISSING_METHOD_DOSE_REFERENCE", "MISSING_WORK"} for s in planned)}


def report(profile, segments, days, recorded_sessions=(), *, cycles=None, limited=False):
    """Assess complete authoritative cycles, including execution before today.

    ``cycles`` maps every goal-window date to its resolved state. In a genuinely
    truncated programme or a mixed loading/maintenance window, a full weekly
    minimum is not compressed into the remaining days. Unknown actual roles are
    disclosed, never manufactured as credit or asserted as physiological failure.
    """
    by_date = {d["date"]: d.get("cycle") for d in days}
    by_date.update({str(k): v for k, v in (cycles or {}).items()})
    rows = []
    for segment in segments:
        first, last = segment["window_start"], segment["window_end"]
        states = [v for k, v in by_date.items() if first <= k <= last and v]
        state = next((s for s in states if s.get("kind") in LOADING_KINDS), states[0] if states else {})
        requirement = policy(profile, state, limited=limited)
        full = (date.fromisoformat(last)-date.fromisoformat(first)).days == 6
        mixed = any(policy(profile, s, limited=limited)["required"] != requirement["required"] for s in states)
        assess = requirement["required"] and full and not mixed
        counts = count(days, recorded_sessions, start=first, end=last)
        minimum = max(0, requirement["minimum"]-counts["total"]) if assess else 0
        target = max(0, requirement["target"]-counts["total"]) if assess else 0
        status = ("NOT_ASSESSED_PARTIAL_CYCLE" if requirement["required"] and not assess else
                  "MAINTENANCE_ONLY" if not requirement["required"] else
                  "MINIMUM_NOT_MET" if minimum else "TARGET_NOT_MET" if target else "TARGET_MET")
        blockers = sorted({a["code"] for d in days if first <= d["date"] <= last
                           for a in d.get("rejected_alternatives", []) if a.get("code")}) if minimum or target else []
        rows.append({"window_start": first, "window_end": last, **requirement, **counts,
                     "assessed": assess, "minimum_shortfall": minimum, "target_shortfall": target,
                     "status": status, "observed_constraints": blockers,
                     "hard_limit_proven": False})
    minimum = sum(r["minimum_shortfall"] for r in rows)
    target = sum(r["target_shortfall"] for r in rows)
    return {"version": VERSION, "scope": "WHOLE_MICROCYCLE_ALL_COMPONENTS", "segments": rows,
            "minimum_shortfall": minimum, "target_shortfall": target, "needs_search": target > 0,
            "changes_load_limits": False, "unlabelled_actual_is_developmental": False}
