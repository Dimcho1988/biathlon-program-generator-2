"""Rebuild selected direct Q from pinned immutable HR/Vflat samples.

The snapshot's persisted E is retained until component_load_projection verifies
its original daily ledger. New Q is not added to legacy Q. Calibration uses
only earlier training evidence and real maximal tests within fourteen subsequent
days, separately from all derived accounting values.
"""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json

from biathlon import speed_equivalence, unified_load, load_evidence
from .load_model_policy import identity as load_policy_identity
from .speed_load_cache import SpeedLoadCache

VERSION = unified_load.VERSION
_cache = SpeedLoadCache(max_entries=12, ttl_seconds=300)


def reconstruct_history(repository, alias, source, *, speed_inputs=None):
    settings_reader = getattr(repository, "athlete_settings", None)
    samples_reader = getattr(repository, "activity_speed_history_samples", None)
    if not callable(settings_reader) or not callable(samples_reader) or not source:
        return source
    settings = settings_reader(alias)
    if settings is None:
        return source
    from . import model_service
    inputs = speed_inputs or model_service.speed_view_inputs(repository, alias)
    calendar = inputs["calendar"]
    original_identity = (source.get("load_selection") or {}).get("snapshot_identity") or {
        "generation": calendar.get("generation_id"), "revision": calendar.get("revision"),
        "period": [source.get("period_start"), source.get("period_end")],
        "activities": [(a.get("activity_ref"), a.get("date"), a.get("sport"), a.get("duration_min")) for a in source.get("activities", ())]}
    identity = {"version": VERSION, "equivalence": speed_equivalence.VERSION,
                "alias": alias, "namespace": getattr(repository, "speed_load_cache_namespace", None),
                "settings": {"bounds": settings.zone_bounds_bpm, "max": settings.hrmax_bpm,
                             "leading": settings.leading_indicator},
                "calendar": calendar, "initial_profile": inputs.get("initial_profile"),
                "source": original_identity, "tests": inputs["entries"],
                "profile_policy": model_service.HISTORICAL_SPEED_POLICY,
                "selection_policy": load_policy_identity(),
                "evaluation_day": inputs["today"]}
    key = sha256(json.dumps(identity, sort_keys=True, allow_nan=False, default=str,
                           separators=(",", ":")).encode()).hexdigest()
    if (source.get("load_selection") or {}).get("fingerprint") == key:
        return source
    return _cache.get_or_compute(key, lambda: _reconstruct(repository, alias, source, inputs, settings, key, original_identity))


def _reconstruct(repository, alias, source, inputs, settings, key, original_identity):
    from . import model_service
    out = deepcopy(source)
    calendar = {a["activity_ref"]: a for a in inputs["calendar"].get("activities", ())}
    activities = list(out.get("activities", ()))
    known = {a["activity_ref"] for a in activities}
    for ref, activity in calendar.items():
        if (ref not in known and source["period_start"] <= activity.get("local_date", "") <= source["period_end"]
                and load_evidence.supports_recording(activity.get("sport")) and activity.get("latest_shadow_run_key")):
            activities.append({"activity_ref": ref, "date": activity["local_date"], "sport": activity["sport"],
                "duration_min": (activity.get("elapsed_time_s") or activity.get("moving_time_s") or 0.)/60.,
                "strength_time_min": 0., "quality_status": "limited", "hr_coverage_percent": 0.,
                "zones": [{"zone": z, "raw_time_min": 0., "equivalent_time_min": 0., "effective_load": 0.,
                           "mean_effective_hr_bpm": None, "average_minute_value_percent": None}
                          for z in speed_equivalence.ZONES]})
    selected = [a for a in activities if load_evidence.supports_recording(a.get("sport"))
                and not load_evidence.is_strength(a) and a.get("quality_status") != "provider_missing"
                and calendar.get(a["activity_ref"], {}).get("quality_status") != "provider_missing"
                and calendar.get(a["activity_ref"], {}).get("latest_shadow_run_key")]
    models = {}
    records = []
    for offset in range(0, len(selected), 10):
        batch = selected[offset:offset+10]
        keys = tuple(calendar[a["activity_ref"]]["latest_shadow_run_key"] for a in batch)
        samples = repository.activity_speed_history_samples(alias, keys)
        for activity in batch:
            ref = activity["activity_ref"]
            row = samples.get(calendar[ref]["latest_shadow_run_key"])
            if not row or row.get("activity_ref") != ref:
                raise ValueError("Pinned load activity mismatch")
            recording_seconds = (max(calendar[ref].get("elapsed_time_s") or 0.,
                                     calendar[ref].get("moving_time_s") or 0.)
                or (activity.get("load_source") or {}).get("recorded_seconds")
                or (activity.get("duration_min") or 0.)*60)
            if not unified_load.finite(recording_seconds) or recording_seconds <= 0:
                # A surviving sample does not certify the duration of a
                # missing recording. Leave this activity unresolved.
                continue
            day = date.fromisoformat(activity["date"])
            identity = (activity["sport"], day)
            if identity not in models:
                view = (model_service.speed_view(repository, alias, activity["sport"],
                    _inputs=model_service.historical_speed_inputs(inputs, day), _capacity_only=True)
                    if speed_equivalence.supports_sport(activity["sport"]) else {"sport": activity["sport"]})
                from .component_load_context import context_from_speed_view
                models[identity] = context_from_speed_view(view)
            ledger = unified_load.integrate(row["shadow_payload"], bounds=settings.zone_bounds_bpm,
                sport=activity["sport"], parameters=models[identity]["comparison_parameters"], leading_indicator=settings.leading_indicator,
                duration_s=recording_seconds)
            ledger["capacity_context"] = {k: models[identity][k] for k in ("minutes", "sources", "references", "fingerprint")}
            zones = {z["zone"]: z for z in ledger["zones"]}
            for zone in activity["zones"]:
                zone.update(raw_time_min=zones[zone["zone"]]["raw_time_min"],
                            equivalent_time_min=zones[zone["zone"]]["equivalent_time_min"])
                # Existing HR-derived summary means do not describe speed fallback.
                zone["mean_effective_hr_bpm"] = None
                zone["average_minute_value_percent"] = None
            evidence = {k: v for k,v in ledger.items() if k != "zones"}
            activity["load_source"] = evidence
            activity["quality_status"] = "limited" if ledger["unknown_seconds"] > 1e-6 else "valid"
            records.append({"activity_ref": ref, **evidence})
    out["activities"] = sorted(activities, key=lambda a: (a["date"], a["activity_ref"]))
    out["load_selection"] = {"version": VERSION, "fingerprint": key,
        "snapshot_identity": original_identity,
        "leading_indicator": settings.leading_indicator, "equivalence_version": speed_equivalence.VERSION,
        "profile_policy": model_service.HISTORICAL_SPEED_POLICY,
        "selection_policy": load_policy_identity(),
        "unknown_seconds": sum(r["unknown_seconds"] for r in records),
        "fallback_seconds": sum(r["fallback_seconds"] for r in records),
        "reconstructed_activities": len(records)}
    out["equivalence_version"] = speed_equivalence.VERSION
    out["zone_bounds_bpm"] = list(settings.zone_bounds_bpm)
    out["hrmax_bpm"] = settings.hrmax_bpm
    quality = out.get("quality") or {}
    quality["limited_activities"] = sum(a.get("quality_status") == "limited" for a in activities)
    # Recovered speed-only activities no longer remain counted as excluded.
    recovered = len(activities)-len(source.get("activities", ()))
    quality["excluded_activities"] = max(0, quality.get("excluded_activities", 0)-recovered)
    out["quality"] = quality
    return out
