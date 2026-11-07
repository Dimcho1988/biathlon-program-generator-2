"""Transient planning estimates for recorded activities with incomplete load.

The canonical ledger is immutable. Only missing minutes of identifiable,
duration-covered activities are estimated; missing calendar/provider records
remain unknown. Estimates never become measured training or learning evidence.
"""
from copy import deepcopy
from datetime import date, timedelta
import math

from biathlon import load_progression, load_evidence
from biathlon.constants import COMPONENTS
from biathlon.component_load import calculate_component_load

VERSION = "planning-history-estimate-v9-activity-evidence"
POLICY = "RECORDED_DURATION_EXPERT_Q_WITH_RECOVERY"
UNIFIED_POLICY = "UNIFIED_RECORDED_INDICATORS_WITH_PLANNING_ESTIMATES"
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


def _unified_missing_minutes(activity):
    """Only the conserved, explicitly unknown interval can be estimated.

    HR coverage and moving duration cannot describe a ledger that already
    selected HR/speed fallback and retained the full elapsed recording.
    Known REST (including speeds below the individual floor) stays zero.
    """
    evidence = load_evidence.assess(activity, unified=True)["ledger"]
    if evidence is None or not load_evidence.supports_recording(activity.get("sport")):
        return None
    return evidence["unknown_seconds"] / 60


def prepare(source, calendar, profile, today, *, speed_estimates=None,
            zone_tmax_minutes=None, capacity_sources=None, capacity_contexts_by_sport=None,
            required_coverage_start=None):
    """Return an isolated planning ledger and explicit coverage/provenance.

    Expert Q proportions use existing bounds/level settings. For an unmeasured
    minute the zone-upper-edge Q convention is used, not a fabricated HR value.
    Known partial Q is kept, and only its missing-duration contribution is added.
    """
    if (source.get("planning_history") or {}).get("estimated"):
        raise ValueError("Planning estimates cannot become new observed inputs")
    unified = bool(source.get("load_selection"))
    result = deepcopy(source)
    quality = source.get("quality") or {}
    activities = {a["activity_ref"]: deepcopy(a) for a in source.get("activities", [])}
    catalog = {a["activity_ref"]: a for a in normalized_calendar(calendar) if a.get("activity_ref")}
    candidates, unresolved, issues = [], [], []
    ignored_older = []
    identified_complete = 0

    def unresolved_activity(ref, day, reason):
        # Recovery certifies its bounded 40-day window. Keep older observed
        # loads and supported estimates, but dated older omissions do not block
        # that window. Undated/unidentified records always remain unresolved.
        if required_coverage_start is not None and day and day < required_coverage_start.isoformat():
            ignored_older.append(ref)
        else:
            unresolved.append(ref)
            issues.append(load_evidence.issue({**catalog.get(ref, {}), **activities.get(ref, {}),
                "activity_ref": ref, "date": day}, reason))
    for ref in sorted(set(activities) | set(catalog)):
        original, item = activities.get(ref), catalog.get(ref, {})
        day = (original or {}).get("date") or item.get("local_date") or item.get("date")
        if not day:
            unresolved_activity(ref, day, "ACTIVITY_DATE_MISSING")
            continue
        if day > today.isoformat() or day < source.get("period_start", day):
            continue
        status = (original or {}).get("quality_status") or item.get("quality_status", "valid")
        evidence = load_evidence.assess(original or item, unified=unified)
        selected_recording = evidence["ledger"] is not None
        requires_recording = load_evidence.requires_recording(original or {}, unified=unified)
        missing = _unified_missing_minutes(original or {}) if requires_recording else None
        provider_missing = status == "provider_missing" or item.get("quality_status") == "provider_missing"
        if evidence["basis"] == "STRENGTH" and evidence["complete"] and not provider_missing:
            continue
        if evidence["reason"] == "ACTIVITY_Q_INCOMPLETE":
            unresolved_activity(ref, day, evidence["reason"])
            continue
        if selected_recording and missing == 0 and not provider_missing:
            identified_complete += status != "valid"
            continue
        if evidence["complete"] and not requires_recording and not provider_missing:
            continue
        duration = _number((original or {}).get("duration_min", item.get("duration_min")))
        coverage = _number((original or {}).get("hr_coverage_percent", item.get("hr_coverage_percent")))
        reason = str(item.get("quality_reason") or "").lower()
        # A failed API request/unknown activity is not a missing-sensor interval.
        hr_reason = not reason or any(term in reason for term in ("hr", "пулс", "heart rate", "heartrate"))
        if (provider_missing
                or duration is None or (not requires_recording and (coverage is None or coverage > 100 or not hr_reason))):
            unresolved_activity(ref, day, "PROVIDER_RECORD_MISSING" if provider_missing else "RECORDING_DURATION_MISSING" if duration is None else "LEGACY_HR_INCOMPLETE")
            continue
        sport = (original or {}).get("sport") or item.get("sport")
        if not sport or sport in {"Активност", "Unknown", "WeightTraining"}:
            unresolved_activity(ref, day, "STRENGTH_LOAD_INCOMPLETE" if sport == "WeightTraining" else "ACTIVITY_SPORT_MISSING")
            continue
        known_time = sum(_number(z.get("raw_time_min")) or 0. for z in (original or {}).get("zones", []))
        # An excluded recording has no canonical contribution at all. Its
        # nominal HR coverage is not a measured Q ledger; account for its entire
        # duration, letting a timestamp-supported speed estimate cover a subset.
        missing = (missing if requires_recording else
                   duration if original is None else min(duration * (1. - coverage / 100.), max(0., duration - known_time)))
        if missing is None or missing <= 0:
            unresolved_activity(ref, day, evidence["reason"] or "INVALID_RECORDING_LEDGER")
            continue
        activity = original or {"activity_ref": ref, "date": day, "sport": sport,
                                "duration_min": duration, "quality_status": status,
                                "hr_coverage_percent": coverage, "zones": []}
        activities[ref] = activity
        candidates.append((activity, missing))
    identified = len(candidates) + len(unresolved) + identified_complete + len(ignored_older)
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
    diagnostics = {"version": VERSION, "policy": UNIFIED_POLICY if unified else POLICY, "estimated": bool(candidates),
                   "supported": not unresolved and not missing_days, "unresolved_activity_refs": unresolved,
                   "missing_calendar_days": missing_days,
                   "estimated_activity_count": len(candidates),
                   "estimated_minutes": sum(v for _, v in candidates),
                   "basis": "RECORDED_ACTIVITY_DURATION_AND_EXPERT_Q_PROPORTIONS" if candidates else "MEASURED_HR",
                   "q_convention": "COMMON_ZONE_REFERENCE" if unified else "ZONE_UPPER_EDGE_EQUIVALENT_MINUTES",
                   "is_measured_zone_distribution": False if candidates else True,
                   "activities": []}
    diagnostics.update(evidence_version=load_evidence.VERSION, issues=issues)
    if required_coverage_start is not None:
        diagnostics.update(required_coverage_start=required_coverage_start.isoformat(),
                           older_unresolved_activity_count=len(ignored_older))
    diagnostics["unresolved_activity_count"] = len(unresolved)
    if unified:
        from .load_metadata import selection_metadata
        try:
            coverage = selection_metadata(source.get("activities", ()))
        except (TypeError, ValueError, OverflowError):
            # Invalid metadata remains unresolved above. Do not present a
            # partial aggregate or replace invalid durations with zero.
            coverage = None
        if coverage:
            diagnostics["load_coverage"] = {k: coverage[k] for k in (
                "activity_count", "total_aerobic_activities", "recorded_seconds",
                "unknown_seconds", "rest_seconds", "fallback_seconds", "source_seconds")}
        diagnostics["basis"] = ("SELECTED_HR_SPEED_LEDGER_WITH_EXPERT_Q_FOR_UNKNOWN_DURATION"
                                if candidates else "SELECTED_HR_SPEED_LEDGER")
        diagnostics["measured_history_unchanged"] = True
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
        # The unified ledger already used both indicators. A legacy speed
        # estimate must never fill selected REST or count fallback twice.
        speed = {} if load_evidence.requires_recording(activity, unified=unified) else (speed_estimates or {}).get(activity["activity_ref"]) or {}
        speed_minutes = _number(speed.get("covered_missing_minutes")) or 0.
        rest_minutes = _number(speed.get("rest_missing_minutes", 0.))
        speed_zones = {r["zone"]: r for r in speed.get("zones", []) if r.get("zone") in ZONES}
        valid_speed = (0 < speed_minutes <= missing+1e-6 and speed.get("provenance", {}).get("load") == "ESTIMATED_NOT_MEASURED_HR"
                       and all(_number(r.get("raw_time_min")) is not None and _number(r.get("equivalent_time_min")) is not None for r in speed_zones.values())
                       and rest_minutes is not None and 0 <= rest_minutes <= speed_minutes
                       and abs(sum(r["raw_time_min"] for r in speed_zones.values())+rest_minutes-speed_minutes) < 1e-6)
        if not valid_speed:
            speed_minutes, speed_zones, rest_minutes = 0., {}, 0.
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
                "planning_measured_time_min": previous.get("raw_time_min"),
                "planning_estimated_q": estimate[z], "planning_measured_q": previous.get("equivalent_time_min")})
        activity["planning_estimated"] = True
        activity["component_load"] = after
        diagnostics["activities"].append({"activity_ref": activity["activity_ref"], "date": day,
                                         "missing_minutes": missing, "estimated_q": estimate,
                                         "speed_covered_minutes": speed_minutes,
                                         "speed_rest_minutes": rest_minutes,
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
