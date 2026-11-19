"""Date-scoped HR calibration over immutable recordings, without provider I/O."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import logging
from time import perf_counter

from . import activity_shadow_pipeline as shadow_model
from .memory_budget import serialized_model_work
from .request_metrics import StoreMetrics, current_metrics, timed_stage

logger = logging.getLogger(__name__)


@contextmanager
def _calibration_metrics():
    """One bounded timing record, with no athlete identifiers or sample data."""
    metrics = StoreMetrics()
    token = current_metrics.set(metrics)
    started = perf_counter()
    try:
        yield
    finally:
        current_metrics.reset(token)
        logger.info("hr_calibration_timings elapsed_seconds=%.3f store_seconds=%.3f "
                    "store_calls=%d store_bytes=%d decode_seconds=%.3f timings=%s",
                    perf_counter()-started, metrics.seconds, metrics.calls, metrics.bytes,
                    metrics.shadow_decode_seconds, metrics.details())


def _timed_recordings(repository, alias, selected):
    recordings = iter(repository.iter_hr_calibration_recordings(alias, selected))
    while True:
        with timed_stage("hr_read"):
            try:
                item = next(recordings)
            except StopIteration:
                return
        yield item


class StoredCalibrationUnavailable(ValueError):
    """An older recording needs the ordinary refresh to establish its inputs."""


def recalibrate_shadow(immutable, previous, *, detail, settings, model_upgrade=False):
    """Reuse measured speed/terrain; evaluate HRmod and paired TI exactly once."""
    samples = immutable.get("samples") or []
    old_rows = previous.get("timeseries") or []
    speed_rows = previous.get("speed_test_series")
    if (not samples or len(samples) != len(old_rows) or not isinstance(speed_rows, list)
            or immutable.get("input_hash") != previous.get("input_hash")
            or previous.get("hrmod_model_version") not in (
                {shadow_model.HRMOD_MODEL_VERSION, "hrmod_mirror_area_shift_v9"}
                if model_upgrade else {shadow_model.HRMOD_MODEL_VERSION})
            or previous.get("hrmod_config_version") != shadow_model.HRMOD_CONFIG_VERSION
            or previous.get("terrain_model_version") != shadow_model.TERRAIN_MODEL_VERSION):
        raise StoredCalibrationUnavailable("Stored HR calibration inputs are incomplete or outdated")
    # The stored sensor clock remains authoritative even if catalog timezone
    # normalization represented a naive provider timestamp differently.
    start = datetime.fromisoformat(samples[0]["timestamp"])-timedelta(seconds=samples[0]["elapsed_s"])
    hr_samples = tuple(shadow_model.HRInputSample(
        timestamp=datetime.fromisoformat(row["timestamp"]), heart_rate_bpm=row["hr_raw_bpm"],
        quality_flags=tuple(row.get("quality_flags", ()))) for row in samples)
    channels = {"speed_mps": "speed_raw_kmh", "altitude_m": "altitude_m",
                "distance_m": "cumulative_distance_m", "grade": "grade_raw_pct"}
    references = shadow_model.ReferenceChannels(samples=tuple(shadow_model.ReferenceSample(
        timestamp=sample.timestamp, elapsed_s=row["elapsed_s"],
        dt_s=0. if i == 0 else row["elapsed_s"]-samples[i-1]["elapsed_s"],
        speed_mps=row["speed_raw_kmh"]/3.6 if row.get("speed_raw_kmh") is not None else None,
        altitude_m=row.get("altitude_m"), distance_m=row.get("cumulative_distance_m"),
        grade=row.get("grade_raw_pct")) for i, (row, sample) in enumerate(zip(samples, hr_samples, strict=True))),
        available_channels=tuple(name for name, key in channels.items() if any(r.get(key) is not None for r in samples)))
    offset = shadow_model.reference_offset(detail.get("type"))
    profile = shadow_model._profile([b-offset for b in settings.zone_bounds_bpm],
                                   settings.hrmax_bpm-offset if settings.hrmax_bpm else None)
    if profile is None:
        raise StoredCalibrationUnavailable("Explicit HRmax is required for local recalibration")
    hr = shadow_model.run_hrmod_v4_shadow(hr_samples=hr_samples, athlete_profile=profile,
                                         reference_channels=references)
    speed = {row["elapsed_s"]: row for row in speed_rows}
    # Independent speed arrays remain immutable and can be shared. Copy only
    # the HR rows and diagnostic mappings that this recalculation replaces.
    out = {**previous, "timeseries": [dict(row) for row in old_rows],
           "diagnostics": {**previous["diagnostics"], "vflat": dict(previous["diagnostics"]["vflat"])}}
    numeric = ("hr_clean_bpm", "hrmod_candidate_bpm", "hrmod_final_bpm", "hrmod_delta_bpm", "added_bpm", "removed_bpm")
    for i, (raw, row, h) in enumerate(zip(samples, out["timeseries"], hr["timeseries"], strict=True)):
        if raw["elapsed_s"] != row["elapsed_s"] or raw["timestamp"] != row["timestamp"]:
            raise StoredCalibrationUnavailable("Stored HR samples are misaligned")
        for key in numeric:
            row[key] = shadow_model._plain_number(h.get(key))
        for key in ("receiver_flag", "donor_flag"):
            row[key] = h.get(key, False)
        row["wave_id"] = h.get("wave_id")
        row["model_flags"] = h.get("model_flags", [])
        row["hr_raw_bpm"] = h.get("hr_raw_bpm", raw["hr_raw_bpm"])
        row["grade_raw_pct"] = h.get("grade_raw_pct", references.samples[i].grade)
        row["grade_smoothed_pct"] = h.get("grade_smoothed_pct", row.get("grade_smoothed_pct"))
        # Quality flags describe the unchanged observations. HR-dependent flags
        # are in model_flags; speed exclusions come from the independent series.
        row["exclusion_reason"] = (h.get("exclusion_reason") or
            (hr.get("exclusion_reason") if hr.get("status") == "excluded" else None) or
            speed.get(raw["elapsed_s"], {}).get("exclusion_reason"))
    duration = shadow_model._activity_duration_seconds(detail, strength_activity=False)
    hr = shadow_model.compensate(out["timeseries"], hr, bounds=settings.zone_bounds_bpm,
        sport=detail.get("type"), duration_s=duration, hrmax_bpm=settings.hrmax_bpm)
    out.update(hrmod_model_version=hr["model_version"], hrmod_config_version=hr["config_version"])
    out["configuration_fingerprint"] = shadow_model.activity_shadow_configuration_fingerprint(
        settings.zone_bounds_bpm, settings.hrmax_bpm, activity_duration_s=duration, activity_detail=detail)
    paired_speed = [{**r, "timestamp": (start+timedelta(seconds=r["elapsed_s"])).isoformat(),
                     "grade_raw_pct": r.get("grade_smoothed_pct")} for r in speed_rows]
    out["trainability_index"] = shadow_model.compute_trainability(out["timeseries"], paired_speed,
        zone_bounds_bpm=settings.zone_bounds_bpm, hrmax_bpm=settings.hrmax_bpm,
        hr_reference_offset_bpm=offset, activity_duration_s=duration or sum(r["dt_s"] for r in speed_rows),
        comparison_key=shadow_model.activity_shadow_configuration_fingerprint(
            settings.zone_bounds_bpm, settings.hrmax_bpm, sport=detail.get("type")),
        source_versions={"vflat": out["vflat_model_version"], "hrmod": shadow_model.HRMOD_MODEL_VERSION})
    out.update(hrmod_waves=hr.get("waves", []), zone_summary=hr.get("zones", []),
               segments_15s=shadow_model._segments_15s(out["timeseries"]))
    out["diagnostics"]["hrmod"] = hr.get("diagnostics", {})
    if offset:
        out["diagnostics"]["vflat"]["hr_policy"] = shadow_model.sport_hr_policy(
            detail.get("type"), settings.zone_bounds_bpm, settings.hrmax_bpm)
    out["hashes"] = {"hr_input_hash": hr.get("hr_input_hash"), "terrain_input_hash": hr.get("terrain_input_hash"),
                     "hrmod_final_result_hash": hr.get("final_result_hash")}
    out.pop("result_hash", None)
    out["result_hash"] = shadow_model._canonical_hash(out)
    return out


def _detail(row):
    return {"type": row.get("sport") or row.get("activity_type"),
        "sub_type": row.get("activity_sub_type"), "start_date": row.get("start_at_utc") or row.get("start_local"),
        "start_date_local": row.get("start_local"), "moving_time": row.get("moving_time_s"),
        "elapsed_time": row.get("elapsed_time_s"), "icu_recording_time": row.get("recording_time_s"),
        "distance": row.get("distance_m"), "total_elevation_gain": row.get("elevation_gain_m")}


def _calibration_metadata(row):
    # Match a full refresh's measured metadata. Previously calculated duration
    # and load must not enter the initial model through a catalog summary.
    derived = {"canonical_summary", "canonical_training_load", "latest_shadow_run_key",
               "latest_canonical_run_key", "input_key", "quality_reason", "hr_coverage_percent"}
    if row.get("quality_status") != "provider_missing":
        derived.add("quality_status")
    return {k: v for k, v in row.items() if k not in derived}


@_calibration_metrics()
@serialized_model_work
def refresh_hr_calibration(repository, *, athlete_alias, athlete_settings, base_generation_id, model_upgrade=False):
    """Replace the affected suffix; carry earlier loads and wellness unchanged."""
    from dataclasses import replace
    from biathlon import unified_load, load_evidence
    from biathlon.component_load import VERSION as COMPONENT_VERSION
    from . import model_service
    from .component_load_context import context_from_speed_view, ZONES
    from .component_load_projection import project_history, project_legacy_recovery
    from .hr_settings_history import at
    from .refresh_speed_evidence import RefreshSpeedEvidence
    from .load_model_policy import identity as load_policy_identity
    from .schemas import AthleteSnapshot

    started = perf_counter()
    snapshot = repository.latest(athlete_alias)
    source = (snapshot or {}).get("load_history") or {}
    first = source.get("period_start") if model_upgrade else athlete_settings.hr_recompute_from
    if (not first or not source.get("activities") or first < source.get("period_start", "9999")
            or not source.get("hr_calibration_history") or not source.get("hr_settings_revision")):
        raise StoredCalibrationUnavailable("Stored history does not cover the requested correction")
    if ((source.get("component_load_model") or {}).get("version") != COMPONENT_VERSION
            or (source.get("load_selection") or {}).get("selection_policy") != load_policy_identity()):
        raise StoredCalibrationUnavailable("Stored history uses an older load policy")
    with timed_stage("hr_inputs"):
        inputs = model_service.speed_view_inputs(repository, athlete_alias)
    if inputs["calendar"].get("generation_id") != base_generation_id:
        raise StoredCalibrationUnavailable("Pinned calibration generation changed")
    end = date.fromisoformat(source["period_end"])
    catalog = deepcopy(inputs["calendar"]["activities"])
    by_ref = {r["activity_ref"]: r for r in catalog}
    old_settings = replace(athlete_settings, hr_history=tuple(source["hr_calibration_history"]))
    changed = deepcopy(source)
    activities = {r["activity_ref"]: r for r in changed["activities"]}
    affected = sorted((r for r in catalog if first <= r["local_date"] <= source["period_end"]),
                      key=lambda r: (r["local_date"], r.get("start_at_utc") or "", r["activity_ref"]))
    selected = [r for r in affected if load_evidence.supports_recording(r.get("sport"))
                and r.get("quality_status") != "provider_missing"]
    if any(not r.get("latest_shadow_run_key") or not r.get("input_key")
           or r["activity_ref"] not in activities for r in selected):
        raise StoredCalibrationUnavailable("An affected activity has no complete immutable recording")
    expected = {}
    for row in affected:
        expected[row["local_date"]] = expected.get(row["local_date"], 0)+1
    evidence = RefreshSpeedEvidence(inputs, date.fromisoformat(first), end, expected_activity_dates=expected)
    evidence.catalog = {ref: _calibration_metadata(row) for ref, row in evidence.catalog.items()}
    # Strength is independent of pulse, but still contributes coverage/duration.
    for row in affected:
        if row not in selected:
            evidence.record(_calibration_metadata(row))
    contexts = {}
    report = getattr(repository, "report_sync_progress", lambda *args: None)
    report("ACTIVITIES", 0, len(selected))
    updated_hashes = {}
    for n, (row, immutable, previous) in enumerate(_timed_recordings(repository, athlete_alias, selected), 1):
        ref, day = row["activity_ref"], date.fromisoformat(row["local_date"])
        detail, dated = _detail(row), at(athlete_settings, day)
        old = at(old_settings, day)
        previous_fingerprint = shadow_model.activity_shadow_configuration_fingerprint(
            old.zone_bounds_bpm, old.hrmax_bpm, activity_detail=detail,
            hrmod_model_version=previous.get("hrmod_model_version") if model_upgrade else None,
            activity_duration_s=shadow_model._activity_duration_seconds(detail, strength_activity=False))
        if previous.get("configuration_fingerprint") != previous_fingerprint:
            raise StoredCalibrationUnavailable("Stored recording uses a different model or metadata")
        with timed_stage("hr_model"):
            derived = recalibrate_shadow(immutable, previous, detail=detail, settings=dated, model_upgrade=model_upgrade)
        with timed_stage("hr_publish"):
            run_key = repository.publish_recalibrated_shadow(athlete_alias=athlete_alias, activity_ref=ref,
                input_key=row["input_key"], derived_payload=derived)
        target = by_ref[ref]
        target["latest_shadow_run_key"] = run_key
        updated_hashes[ref] = immutable["input_hash"]
        identity = (row["sport"], day)
        if identity not in contexts:
            with timed_stage("hr_capacity"):
                historical = model_service.historical_speed_inputs(evidence.at(day-timedelta(days=1)), day,
                                                                   evaluation_day=inputs["today"])
                view = model_service.speed_view(repository, athlete_alias, row["sport"],
                                               _inputs=historical, _capacity_only=True)
                contexts[identity] = context_from_speed_view(view)
        context = contexts[identity]
        duration = max(detail.get("elapsed_time") or 0., detail.get("moving_time") or 0.)
        with timed_stage("hr_load"):
            ledger = unified_load.integrate(derived, bounds=dated.zone_bounds_bpm, sport=row["sport"],
                parameters=context.get("comparison_parameters"), leading_indicator=dated.leading_indicator, duration_s=duration)
        ledger["capacity_context"] = {k: context[k] for k in ("minutes", "sources", "references", "fingerprint")}
        activity = activities[ref]
        for zone, q in zip(activity["zones"], ledger["zones"], strict=True):
            if zone["zone"] != q["zone"]:
                raise StoredCalibrationUnavailable("Activity zones are not ordered")
            # Keep the previous E until project_history verifies the daily ledger.
            zone.update(raw_time_min=q["raw_time_min"], equivalent_time_min=q["equivalent_time_min"],
                        mean_effective_hr_bpm=None, average_minute_value_percent=None)
        activity["load_source"] = {k: v for k, v in ledger.items() if k != "zones"}
        activity["quality_status"] = "limited" if ledger["unknown_seconds"] > 1e-6 else "valid"
        target["quality_status"] = activity["quality_status"]
        evidence.record(_calibration_metadata(target), derived)
        report("ACTIVITIES", n, len(selected))
        del derived, previous, immutable
    report("MODELS")
    current_inputs = evidence.at(end)
    sports = sorted({a["sport"] for a in changed["activities"]})
    current_contexts = {sport: context_from_speed_view(model_service.speed_view(repository, athlete_alias, sport,
        _inputs=current_inputs, _capacity_only=True)) for sport in sports}
    # Old activities must retain their original dated capacity, even where the
    # newly calibrated current curve differs. Never apply today's Tmax backwards.
    for activity in changed["activities"]:
        if activity["date"] < first and not load_evidence.is_strength(activity) and not (
                activity.get("load_source") or {}).get("capacity_context"):
            raise StoredCalibrationUnavailable("Earlier activity lacks dated capacity provenance")
    changed.pop("component_load_model", None)
    with timed_stage("hr_projection"):
        projected = project_history(changed, current_contexts)
    # The prefix is an immutable seed, including its original Tref provenance.
    prefix = {(r["date"], r["zone"]): r for r in source["daily"] if r["date"] < first}
    projected["daily"] = [deepcopy(prefix.get((r["date"], r["zone"]), r)) for r in projected["daily"]]
    projected["activities"] = [deepcopy(next(a for a in source["activities"] if a["activity_ref"] == r["activity_ref"]))
                               if r["date"] < first else r for r in projected["activities"]]
    projected.update(hr_settings_revision=athlete_settings.hr_settings_revision,
        hr_calibration_history=list(athlete_settings.hr_history), hrmax_bpm=athlete_settings.hrmax_bpm,
        zone_bounds_bpm=list(athlete_settings.zone_bounds_bpm))
    projected["load_selection"] = {"version": unified_load.VERSION, "selection_policy": load_policy_identity(),
        "profile_policy": model_service.HISTORICAL_SPEED_POLICY, "calibration_eligible": False,
        "unknown_seconds": sum((a.get("load_source") or {}).get("unknown_seconds", 0.) for a in projected["activities"]),
        "fallback_seconds": sum((a.get("load_source") or {}).get("fallback_seconds", 0.) for a in projected["activities"]),
        "basis": "STORED_RECORDING_MODEL_UPGRADE" if model_upgrade else "STORED_RECORDING_HR_CALIBRATION",
        "hrmod_model_version": shadow_model.HRMOD_MODEL_VERSION, "recomputed_from": first,
        "reconstructed_activities": len(selected), "leading_indicator": athlete_settings.leading_indicator}
    projected["quality"]["limited_activities"] = sum(a["quality_status"] == "limited" for a in projected["activities"])
    from .load_metadata import coverage_warnings, with_load_metadata
    projected["quality"]["warnings"] = coverage_warnings(projected["quality"]["warnings"], projected["quality"]["limited_activities"])
    result = deepcopy(snapshot)
    result["load_history"] = projected
    status = result["training_status"]
    status["model"] = with_load_metadata(status["model"], projected["activities"])
    status["data_quality"]["warnings"] = coverage_warnings(status["data_quality"]["warnings"], projected["quality"]["limited_activities"])
    summaries = {r["zone"]: r for r in projected["zones"]}
    latest = max(projected["activities"], key=lambda a: (a["date"], a["activity_ref"]))
    latest_zones = {r["zone"]: r for r in latest["zones"]}
    for row in status["zones"]:
        row.update(status_7_40=summaries[row["zone"]]["status_7_40"], tref_min=summaries[row["zone"]]["tref_min"],
                   raw_time_min=latest_zones[row["zone"]]["raw_time_min"],
                   equivalent_time_min=latest_zones[row["zone"]]["equivalent_time_min"])
    result = project_legacy_recovery(result)
    # Canonical calendar and snapshot are published together in a new generation.
    daily = {(r["date"], r["zone"]): r for r in projected["daily"]}
    for activity in projected["activities"]:
        if activity["date"] < first:
            continue
        row = by_ref[activity["activity_ref"]]
        canonical = deepcopy(row["canonical_summary"])
        canonical["zones"] = [{"zone": z["zone"], "raw_time_s": z["raw_time_min"]*60.,
            "equivalent_time_s": z["equivalent_time_min"]*60., "effective_load": z["effective_load"]} for z in activity["zones"]]
        canonical["tref_used_min"].update({z: daily[(activity["date"], z)]["tref_used_min"] for z in ZONES})
        if activity.get("load_source"):
            canonical["intensity_provenance"] = {key: activity["load_source"].get(key) for key in
                ("speed_role", "initial_heuristic", "calibration_source")}
        source_hash = updated_hashes.get(activity["activity_ref"]) or row.get("latest_canonical_run_key")
        scientific_hash = shadow_model._canonical_hash({"schema_version": "hr-recalibration-canonical-v1",
            "source_input_hash": source_hash, "hr_revision": athlete_settings.hr_settings_revision,
            "capacity": (activity.get("load_source") or {}).get("capacity_context"), "summary": canonical})
        with timed_stage("hr_canonical"):
            row["latest_canonical_run_key"] = repository.publish_canonical_activity_result(
                athlete_alias=athlete_alias, activity_ref=activity["activity_ref"],
                scientific_input_hash=scientific_hash, result_payload=canonical)
        row["canonical_summary"] = canonical
        row["canonical_training_load"] = activity.get("strength_time_min") or sum(z["effective_load"] for z in activity["zones"])
    # Validate the same public snapshot contract as a full provider refresh.
    result = AthleteSnapshot.model_validate(result).model_dump(mode="json")
    repository.upsert_activity_catalog(athlete_alias, catalog)
    repository.replace(athlete_alias, result)
    logger.info("hr_calibration_local_complete activities=%d retained=%d elapsed_seconds=%.3f",
                len(selected), len(catalog)-len(selected), perf_counter()-started)
