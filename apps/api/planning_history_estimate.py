"""Transient planning estimates for recorded activities with incomplete HR.

The canonical ledger is immutable. Only missing minutes of identifiable,
duration-covered activities are estimated; missing calendar/provider records
remain unknown. Estimates never become measured training or learning evidence.
"""
from copy import deepcopy
from datetime import date, timedelta
import math

from biathlon import load_progression
from biathlon.constants import COMPONENTS
from biathlon.component_load import calculate_component_load

VERSION = "planning-history-estimate-v3-activity-adjacent-tmax"
POLICY = "RECORDED_DURATION_EXPERT_Q_WITH_RECOVERY"
ZONES = tuple(z for z in COMPONENTS if z != "STR")


def _number(value):
    return (float(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0 else None)


def normalized_calendar(calendar):
    """Accept the light pinned catalog as well as already-rendered calendar rows."""
    result = []
    for item in calendar:
        row = {**(item.get("catalog_payload") or {}), **{k: v for k, v in item.items() if k != "catalog_payload"}}
        duration = _number(row.get("duration_min"))
        if duration is None:
            duration = _number((row.get("canonical_summary") or {}).get("duration_min"))
        if duration is None:
            seconds = next((_number(row.get(key)) for key in ("moving_time_s", "recording_time_s", "elapsed_time_s")
                            if _number(row.get(key)) is not None), None)
            duration = seconds/60 if seconds is not None else None
        result.append({**row, "duration_min": duration,
                       "latest_shadow_run_key": row.get("latest_shadow_run_key") or row.get("shadow_run_key")})
    return result


def prepare(source, calendar, profile, today, *, speed_estimates=None,
            zone_tmax_minutes=None, capacity_sources=None, capacity_contexts_by_sport=None):
    """Return an isolated planning ledger and explicit coverage/provenance.

    Expert Q proportions use existing bounds/level settings. For an unmeasured
    minute the zone-upper-edge Q convention is used, not a fabricated HR value.
    Known partial Q is kept, and only its missing-duration contribution is added.
    """
    if (source.get("planning_history") or {}).get("estimated"):
        raise ValueError("Planning estimates cannot become new observed inputs")
    result = deepcopy(source)
    quality = source.get("quality") or {}
    activities = {a["activity_ref"]: deepcopy(a) for a in source.get("activities", [])}
    catalog = {a["activity_ref"]: a for a in normalized_calendar(calendar) if a.get("activity_ref")}
    candidates, unresolved = [], []
    for ref in sorted(set(activities) | set(catalog)):
        original, item = activities.get(ref), catalog.get(ref, {})
        day = (original or {}).get("date") or item.get("local_date") or item.get("date")
        if not day or day > today.isoformat() or day < source.get("period_start", day):
            continue
        status = (original or {}).get("quality_status") or item.get("quality_status", "valid")
        if status == "valid":
            continue
        duration = _number((original or {}).get("duration_min", item.get("duration_min")))
        coverage = _number((original or {}).get("hr_coverage_percent", item.get("hr_coverage_percent")))
        reason = str(item.get("quality_reason") or "").lower()
        # A failed API request/unknown activity is not a missing-HR recording.
        hr_reason = not reason or any(term in reason for term in ("hr", "пулс", "heart rate", "heartrate"))
        if status == "provider_missing" or item.get("quality_status") == "provider_missing" or duration is None or coverage is None or coverage > 100 or not hr_reason:
            unresolved.append(ref)
            continue
        sport = (original or {}).get("sport") or item.get("sport")
        if not sport or sport in {"Активност", "Unknown", "WeightTraining"}:
            unresolved.append(ref)
            continue
        known_time = sum(_number(z.get("raw_time_min")) or 0. for z in (original or {}).get("zones", []))
        # An excluded recording has no canonical contribution at all. Its
        # nominal HR coverage is not a measured Q ledger; account for its entire
        # duration, letting a timestamp-supported speed estimate cover a subset.
        missing = duration if original is None else min(duration * (1. - coverage / 100.), max(0., duration - known_time))
        if missing <= 0:
            unresolved.append(ref)
            continue
        activity = original or {"activity_ref": ref, "date": day, "sport": sport,
                                "duration_min": duration, "quality_status": status,
                                "hr_coverage_percent": coverage, "zones": []}
        activities[ref] = activity
        candidates.append((activity, missing))
    identified = len(candidates) + len(unresolved)
    counted = int(quality.get("limited_activities") or 0) + int(quality.get("excluded_activities") or 0)
    if identified < counted:
        unresolved.append("UNIDENTIFIED_INCOMPLETE_ACTIVITIES")
    covered = {(r["date"], r["zone"]) for r in source.get("daily", [])}
    covered.update((r["date"], "STR") for r in source.get("strength", {}).get("daily", []))
    begin = max(date.fromisoformat(source.get("period_start", today.isoformat())), today-timedelta(days=40))
    # Snapshot period_end is inclusive; inspect its final completed day too.
    # Today's open day is excluded from the historical readiness check.
    end = min(date.fromisoformat(source.get("period_end", today.isoformat()))+timedelta(days=1), today)
    missing_days = [(begin+timedelta(days=i)).isoformat() for i in range(max(0, (end-begin).days))
                    if any(((begin+timedelta(days=i)).isoformat(), z) not in covered for z in COMPONENTS)]
    diagnostics = {"version": VERSION, "policy": POLICY, "estimated": bool(candidates),
                   "supported": not unresolved and not missing_days, "unresolved_activity_refs": unresolved,
                   "missing_calendar_days": missing_days,
                   "estimated_activity_count": len(candidates),
                   "estimated_minutes": sum(v for _, v in candidates),
                   "basis": "RECORDED_ACTIVITY_DURATION_AND_EXPERT_Q_PROPORTIONS" if candidates else "MEASURED_HR",
                   "q_convention": "ZONE_UPPER_EDGE_EQUIVALENT_MINUTES",
                   "is_measured_zone_distribution": False if candidates else True,
                   "activities": []}
    if not candidates:
        result["planning_history"] = diagnostics
        return result, diagnostics
    result["activities"] = list(activities.values())
    # The existing expert positioning rule sees total recorded time, including
    # identifiable excluded activities, but never uses estimated zone samples.
    prior_profile = {**profile, "load_progression": {**(profile.get("load_progression") or {}), "enabled": True}}
    positions, basis = load_progression.expert_positions(prior_profile, result, [], today)
    priors = {z: lo + (hi-lo)*positions[z] for z, (lo, hi) in load_progression.WEEKLY_Q_BOUNDS.items()}
    total = sum(priors.values())
    diagnostics.update(expert_reference_q=priors, expert_basis=basis)
    diagnostics["speed_estimated_activity_count"] = 0
    additions = {}
    for activity, missing in candidates:
        speed = (speed_estimates or {}).get(activity["activity_ref"]) or {}
        speed_minutes = _number(speed.get("covered_missing_minutes")) or 0.
        speed_zones = {r["zone"]: r for r in speed.get("zones", []) if r.get("zone") in ZONES}
        valid_speed = (0 < speed_minutes <= missing+1e-6 and speed.get("provenance", {}).get("load") == "ESTIMATED_NOT_MEASURED_HR"
                       and all(_number(r.get("raw_time_min")) is not None and _number(r.get("equivalent_time_min")) is not None for r in speed_zones.values())
                       and abs(sum(r["raw_time_min"] for r in speed_zones.values())-speed_minutes) < 1e-6)
        if not valid_speed:
            speed_minutes, speed_zones = 0., {}
        else:
            diagnostics["speed_estimated_activity_count"] += 1
        remainder = max(0., missing-speed_minutes)
        estimate = {z: remainder*priors[z]/total + speed_zones.get(z, {}).get("equivalent_time_min", 0.) for z in ZONES}
        estimated_time = {z: remainder*priors[z]/total + speed_zones.get(z, {}).get("raw_time_min", 0.) for z in ZONES}
        day = activity["date"]
        target = additions.setdefault(day, {z: 0. for z in COMPONENTS})
        measured = {v["zone"]: v for v in activity.get("zones", [])}
        measured_q = {z: _number(measured.get(z, {}).get("equivalent_time_min")) or 0.
                      for z in COMPONENTS}
        context = (capacity_contexts_by_sport or {}).get(activity["sport"], {})
        capacities = context.get("minutes", zone_tmax_minutes)
        sources = context.get("sources", capacity_sources)
        # A partial activity is one training session. Its missing contribution
        # can move that session across a dose threshold, but cannot combine
        # with another session on the same day to manufacture spillover.
        before = calculate_component_load(measured_q, capacities, capacity_sources=sources)
        after = calculate_component_load(
            {z: measured_q[z] + estimate.get(z, 0.) for z in COMPONENTS},
            capacities, capacity_sources=sources)
        for z in COMPONENTS:
            target[z] += after["effective"][z] - before["effective"][z]
        activity["zones"] = []
        for z in ZONES:
            previous = measured.get(z, {})
            activity["zones"].append({**previous, "zone": z,
                "raw_time_min": (_number(previous.get("raw_time_min")) or 0.) + estimated_time[z],
                "equivalent_time_min": (_number(previous.get("equivalent_time_min")) or 0.) + estimate[z],
                "effective_load": after["effective"][z],
                "planning_estimated_effective_load": after["effective"][z] - before["effective"][z],
                "planning_measured_effective_load": before["effective"][z],
                "planning_estimated_q": estimate[z], "planning_measured_q": previous.get("equivalent_time_min")})
        activity["planning_estimated"] = True
        activity["component_load"] = after
        diagnostics["activities"].append({"activity_ref": activity["activity_ref"], "date": day,
                                         "missing_minutes": missing, "estimated_q": estimate,
                                         "speed_covered_minutes": speed_minutes,
                                         "expert_covered_minutes": remainder,
                                         "speed_provenance": speed.get("provenance") if valid_speed else None})
    rows = [{"date": r["date"], "zone": r["zone"], "effective_load": r["effective_load"]}
            for r in result.get("daily", [])]
    rows += [{"date": r["date"], "zone": "STR", "effective_load": r["effective_load"]}
             for r in result.get("strength", {}).get("daily", [])]
    for day, added_e in sorted(additions.items()):
        # Preserve measured E and add only each activity's marginal estimate.
        for z in COMPONENTS:
            delta = max(0., float(added_e[z]))
            match = next((r for r in rows if r["date"] == day and r["zone"] == z), None)
            # Calendar holes stay holes; duration alone does not certify all
            # activities on an otherwise unobserved date.
            if match is None:
                diagnostics["supported"] = False
                continue
            match["effective_load"] += delta
            container = result.get("strength", {}).get("daily", []) if z == "STR" else result.get("daily", [])
            for r in container:
                if r["date"] == day and (z == "STR" or r["zone"] == z):
                    r["effective_load"] += delta
                    r["planning_estimated_effective_load"] = delta
    if result.get("component_load_model"):
        # The canonical fingerprint describes measured activity Q. Completing
        # that Q invalidates the derived ledger's identity; preserve its source
        # provenance without presenting the old fingerprint as current.
        model = result["component_load_model"]
        measured_fingerprint = model.pop("fingerprint", None)
        if measured_fingerprint is not None:
            model["measured_source_fingerprint"] = measured_fingerprint
        model["planning_estimate_version"] = VERSION
    result["planning_history"] = diagnostics
    return result, diagnostics
