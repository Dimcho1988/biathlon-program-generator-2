from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from io import BytesIO
import json
from zipfile import ZipFile

import pytest

from hrmod_lab.hrmod_core import compute_hrmod_hr_only
from hrmod_lab.exports import build_export_bundle, build_results_zip
from hrmod_lab.reference_validation import (
    ReferenceValidationConfig,
    ReferenceZone,
    evaluate_against_reference,
)
from hrmod_lab.hrmod_service import run_hr_only_phase, run_reference_phase
from hrmod_lab.schemas import AthleteHRProfile, HRmodConfig, HRZone
from hrmod_lab.tcx_adapter import TCXSecurityError, parse_tcx


START = datetime(2026, 2, 1, tzinfo=UTC)


def _profile() -> AthleteHRProfile:
    bounds = (50.0, 100.0, 120.0, 140.0, 160.0, 200.0)
    return AthleteHRProfile(
        hrmax_bpm=200.0,
        hr_floor_bpm=50.0,
        zones=tuple(
            HRZone(f"Z{index + 1}", bounds[index], bounds[index + 1])
            for index in range(5)
        ),
    )


def _config() -> HRmodConfig:
    return HRmodConfig(
        alpha=1.0,
        rise_threshold_bpm_s=0.15,
        min_rise_bpm=5.0,
        smoothing_window_s=3.0,
        smoothing_min_points=3,
        min_sustained_rise_s=2.0,
        fall_threshold_bpm_s=0.10,
        min_sustained_fall_s=2.0,
        min_fall_bpm=3.0,
        baseline_lookback_s=12.0,
        baseline_min_points=3,
        return_sustain_s=2.0,
        neutral_trough_timeout_s=4.0,
        min_receiver_duration_s=2.0,
        min_donor_duration_s=2.0,
        max_wave_duration_s=180.0,
    )


def _response() -> list[float]:
    """A closed, intentionally obvious baseline-rise-peak-fall wave."""

    baseline_before = [100.0] * 25
    rise = [100.0 + 2.5 * index for index in range(1, 21)]
    peak = [150.0, 150.0]
    fall = [150.0 - 1.25 * index for index in range(1, 41)]
    baseline_after = [100.0] * 25
    return [*baseline_before, *rise, *peak, *fall, *baseline_after]


def _tcx_bytes(
    hr_values: list[float],
    *,
    include_reference: bool = True,
    reference_scale: float = 1.0,
    sport: str = "Other",
    include_lap: bool = True,
) -> bytes:
    points: list[str] = []
    for index, hr in enumerate(hr_values):
        timestamp = (START + timedelta(seconds=index)).isoformat().replace("+00:00", "Z")
        extension = ""
        if include_reference:
            speed = reference_scale * (2.0 + (index % 17) * 0.1)
            watts = reference_scale * (80.0 + (index % 23) * 4.0)
            grade = reference_scale * ((index % 7) - 3) / 10.0
            extension = (
                "<Extensions><ns3:TPX>"
                f"<ns3:Speed>{speed:.6f}</ns3:Speed>"
                f"<ns3:Watts>{watts:.6f}</ns3:Watts>"
                f"<ns3:Grade>{grade:.6f}</ns3:Grade>"
                "</ns3:TPX></Extensions>"
            )
        points.append(
            "<Trackpoint>"
            f"<Time>{timestamp}</Time>"
            f"<HeartRateBpm><Value>{hr:.9f}</Value></HeartRateBpm>"
            f"{extension}"
            "</Trackpoint>"
        )
    lap_open = (
        f'<Lap StartTime="{START.isoformat().replace("+00:00", "Z")}">'
        f"<TotalTimeSeconds>{len(hr_values) - 1}</TotalTimeSeconds><TriggerMethod>Manual</TriggerMethod>"
        if include_lap
        else "<Lap>"
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2" '
        'xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2">'
        f'<Activities><Activity Sport="{sport}">{lap_open}<Track>'
        + "".join(points)
        + "</Track></Lap></Activity></Activities></TrainingCenterDatabase>"
    )
    return xml.encode("utf-8")


def _core_from_tcx(payload: bytes):
    parsed = parse_tcx(payload)
    result = compute_hrmod_hr_only(
        hr_samples=parsed.hr_input_samples,
        athlete_profile=_profile(),
        config=_config(),
    )
    return parsed, result


def test_tcx_parser_handles_namespaces_duplicates_extensions_and_missing_optional_fields() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
      xmlns:ext="http://example.test/extensions">
      <Activities><Activity Sport="Running"><Lap StartTime="2026-02-01T00:00:00Z"><Track>
        <Trackpoint><Time>2026-02-01T00:00:00Z</Time><HeartRateBpm><Value>100</Value></HeartRateBpm></Trackpoint>
        <Trackpoint><Time>2026-02-01T00:00:01Z</Time><Extensions><ext:TPX><ext:HR>101</ext:HR><ext:Watts>150</ext:Watts></ext:TPX></Extensions></Trackpoint>
        <Trackpoint><Time>2026-02-01T00:00:01Z</Time><DistanceMeters>7</DistanceMeters></Trackpoint>
        <Trackpoint><Time>2026-02-01T00:00:02Z</Time><HeartRateBpm><Value>102</Value></HeartRateBpm></Trackpoint>
      </Track></Lap></Activity></Activities>
    </TrainingCenterDatabase>"""
    parsed = parse_tcx(xml)
    assert len(parsed.hr_input_samples) == 3
    assert [sample.heart_rate_bpm for sample in parsed.hr_input_samples] == [100.0, 101.0, 102.0]
    assert parsed.diagnostics.duplicate_timestamp_count == 1
    assert "DUPLICATE_TIMESTAMP" in parsed.hr_input_samples[1].quality_flags
    assert parsed.reference_channels.samples[1].power_w == 150.0
    assert parsed.reference_channels.samples[1].distance_m == 7.0
    assert parsed.reference_channels.samples[0].speed_mps is None
    assert parsed.reference_channels.sport == "Running"
    assert len(parsed.reference_channels.laps) == 1
    assert not hasattr(parsed.hr_input_samples[0], "speed_mps")
    assert not hasattr(parsed.hr_input_samples[0], "power_w")
    assert not hasattr(parsed.hr_input_samples[0], "lap")


def test_tcx_parser_rejects_dtd_and_entity_payloads() -> None:
    malicious = b"""<?xml version="1.0"?>
    <!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <TrainingCenterDatabase><Trackpoint><Time>2026-02-01T00:00:00Z</Time>
    <HeartRateBpm><Value>&xxe;</Value></HeartRateBpm></Trackpoint></TrainingCenterDatabase>"""
    with pytest.raises(TCXSecurityError):
        parse_tcx(malicious)


def test_reference_channels_cannot_change_hrmod_or_hr_input_hash() -> None:
    heart_rate = _response()
    parsed_a, result_a = _core_from_tcx(
        _tcx_bytes(heart_rate, include_reference=True, reference_scale=1.0)
    )
    parsed_b, result_b = _core_from_tcx(
        _tcx_bytes(heart_rate, include_reference=True, reference_scale=99.0)
    )
    parsed_none, result_none = _core_from_tcx(
        _tcx_bytes(heart_rate, include_reference=False, include_lap=False)
    )

    assert parsed_a.reference_channels != parsed_b.reference_channels
    assert parsed_none.reference_channels.available_channels == ()
    assert result_a.hr_input_hash == result_b.hr_input_hash == result_none.hr_input_hash
    assert [point.hrmod_bpm for point in result_a.timeseries] == [
        point.hrmod_bpm for point in result_b.timeseries
    ] == [point.hrmod_bpm for point in result_none.timeseries]
    assert result_a == result_b == result_none


def test_identical_hr_and_config_are_deterministic_across_repeated_runs() -> None:
    payload = _tcx_bytes(_response(), include_reference=True)
    _, first = _core_from_tcx(payload)
    _, second = _core_from_tcx(payload)

    assert first == second
    assert first.to_dict() == second.to_dict()
    assert first.hr_input_hash == second.hr_input_hash


def test_core_result_is_json_serializable_before_reference_join() -> None:
    _, result = _core_from_tcx(_tcx_bytes(_response(), include_reference=False))
    encoded = json.dumps(result.to_dict(), sort_keys=True, allow_nan=False)
    assert result.hr_input_hash in encoded
    assert "speed_mps" not in encoded
    assert "power_w" not in encoded


def test_reference_evaluation_is_post_hoc_and_does_not_mutate_core() -> None:
    parsed, result = _core_from_tcx(
        _tcx_bytes(_response(), include_reference=True, sport="Cross Country Skiing")
    )
    before = deepcopy(result)
    validation = evaluate_against_reference(
        hrmod_result=result,
        reference_channels=parsed.reference_channels,
    )
    assert result == before
    assert validation.hr_input_hash == result.hr_input_hash
    assert validation.model_version == result.model_version
    assert not validation.suitable_for_intensity
    assert "RAW_SKI_SPEED_CONTEXT_ONLY" in validation.flags
    assert "REFERENCE_NOT_SUITABLE_FOR_INTENSITY" in validation.flags
    assert validation.interpretation == "context_only"


def test_ski_source_cannot_be_relabelled_into_a_speed_intensity_reference() -> None:
    parsed, result = _core_from_tcx(
        _tcx_bytes(_response(), include_reference=True, sport="Cross Country Skiing")
    )
    before = result.to_dict()
    validation = evaluate_against_reference(
        hrmod_result=result,
        reference_channels=parsed.reference_channels,
        reference_config=ReferenceValidationConfig(
            sport="Treadmill Running",
            enable_controlled_treadmill_speed=True,
            treadmill_grade_verified=True,
            speed_zones=(
                ReferenceZone("Z1", 0.0, 2.5),
                ReferenceZone("Z2", 2.5, 3.0),
                ReferenceZone("Z3", 3.0, 3.5),
                ReferenceZone("Z4", 3.5, 4.0),
                ReferenceZone("Z5", 4.0, None),
            ),
        ),
    )

    assert result.to_dict() == before
    assert not validation.suitable_for_intensity
    assert validation.metrics["quantitative_channel"] is None
    assert validation.interpretation == "context_only"
    assert "RAW_SKI_SPEED_CONTEXT_ONLY" in validation.flags


def test_explicit_power_zones_enable_quantitative_reference_without_refitting() -> None:
    parsed, result = _core_from_tcx(
        _tcx_bytes(_response(), include_reference=True, sport="Cycling")
    )
    config = ReferenceValidationConfig(
        enable_quantitative_power=True,
        power_source="synthetic_fixture_meter",
        power_zones=(
            ReferenceZone("Z1", 0.0, 100.0),
            ReferenceZone("Z2", 100.0, 140.0),
            ReferenceZone("Z3", 140.0, 180.0),
            ReferenceZone("Z4", 180.0, 220.0),
            ReferenceZone("Z5", 220.0, None),
        ),
        max_lag_s=10,
    )
    validation = evaluate_against_reference(
        hrmod_result=result,
        reference_channels=parsed.reference_channels,
        reference_config=config,
    )
    assert validation.suitable_for_intensity
    assert validation.interpretation == "configured_quantitative_power"
    assert validation.metrics["quantitative_channel"] == "power_w"
    assert validation.confusion_matrices
    assert "parameter" not in " ".join(validation.metrics).lower()


def test_annotations_change_only_reference_summaries_never_hrmod() -> None:
    parsed, result = _core_from_tcx(_tcx_bytes(_response(), include_reference=True))
    before = deepcopy(result)
    first = evaluate_against_reference(
        hrmod_result=result,
        reference_channels=parsed.reference_channels,
        optional_annotations=(),
    )
    annotation = {
        "annotation_id": "protocol-1",
        "start_time": (START + timedelta(seconds=35)).isoformat(),
        "end_time": (START + timedelta(seconds=90)).isoformat(),
        "label": "Synthetic protocol window",
        "external_zone": "Z4",
    }
    second = evaluate_against_reference(
        hrmod_result=result,
        reference_channels=parsed.reference_channels,
        optional_annotations=(annotation,),
    )
    assert result == before
    assert first.hr_input_hash == second.hr_input_hash == result.hr_input_hash
    assert first.core_result_fingerprint == second.core_result_fingerprint
    assert first.aligned_timeseries != second.aligned_timeseries or (
        first.annotation_summaries != second.annotation_summaries
    )
    assert second.annotation_summaries


def test_e2e_tcx_produces_core_summaries_diagnostics_and_separate_exports() -> None:
    parsed, result = _core_from_tcx(_tcx_bytes(_response(), include_reference=True))
    validation = evaluate_against_reference(
        hrmod_result=result,
        reference_channels=parsed.reference_channels,
    )
    annotations = parsed.reference_channels.laps
    files = build_export_bundle(
        hrmod_result=result,
        validation_result=validation,
        annotations=annotations,
    )
    assert {
        "processed_hr_timeseries.csv",
        "wave_summary.csv",
        "zone_summary.csv",
        "run_configuration.json",
        "diagnostics.json",
        "reference_aligned_comparison.csv",
        "reference_validation.json",
        "annotations.csv",
        "annotations.json",
        "manifest.json",
    }.issubset(files)
    timeseries_header = files["processed_hr_timeseries.csv"].splitlines()[0].decode()
    assert {
        "raw_hr_bpm",
        "clean_hr_bpm",
        "h_detect_bpm",
        "trend_bpm_per_s",
        "wave_id",
        "wave_state",
        "local_baseline_hr_bpm",
        "receiver_flag",
        "donor_flag",
        "added_bpm",
        "removed_bpm",
        "hrmod_bpm",
        "quality_flags",
        "model_flags",
    }.issubset(set(timeseries_header.split(",")))
    assert "speed_mps" not in timeseries_header
    assert "power_w" not in timeseries_header
    wave_header = files["wave_summary.csv"].splitlines()[0].decode()
    assert {
        "wave_id",
        "status",
        "morphology",
        "morphology_reason",
        "correction_strategy",
        "rise_start_timestamp",
        "peak_timestamp",
        "tail_end_timestamp",
        "donor_available_area_bpm_s",
        "requested_area_bpm_s",
        "receiver_capacity_bpm_s",
        "moved_area_bpm_s",
        "area_balance_error_bpm_s",
        "capacity_limited",
        "skip_reason",
        "raw_zone_seconds",
        "hrmod_zone_seconds",
        "hrmod_minus_raw_zone_seconds",
    }.issubset(set(wave_header.split(",")))
    assert len(result.zone_summary) == 5
    assert result.diagnostics.total_samples == len(_response())
    assert result.diagnostics.detected_wave_count >= 1

    config_export = json.loads(files["run_configuration.json"])
    diagnostics_export = json.loads(files["diagnostics.json"])
    assert config_export["model_version"] == "hrmod_mirror_area_shift_v4"
    assert config_export["config"]["config_version"] == "hrmod_config_v4"
    assert "model_variant" not in config_export["config"]
    assert diagnostics_export["diagnostics"]["detected_wave_count"] >= 1

    archive_bytes = build_results_zip(
        hrmod_result=result,
        validation_result=validation,
        annotations=annotations,
    )
    with ZipFile(BytesIO(archive_bytes)) as archive:
        assert set(files) == set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
    assert archive_bytes == build_results_zip(
        hrmod_result=result,
        validation_result=validation,
        annotations=annotations,
    )
    assert manifest["format"] == "hrmod_lab_export_v4"
    assert manifest["core_and_reference_exports_are_separate"] is True
    assert manifest["hr_input_hash"] == result.hr_input_hash

    core_only_files = build_export_bundle(hrmod_result=result)
    assert "reference_aligned_comparison.csv" not in core_only_files
    assert "reference_validation.json" not in core_only_files


def test_terrain_exports_are_separate_and_keep_core_csv_hr_only() -> None:
    parsed, result = _core_from_tcx(_tcx_bytes(_response(), include_reference=True))
    terrain_result = {
        "model_version": result.model_version,
        "terrain_model_version": "terrain_downhill_donor_exclusion_v4",
        "hr_input_hash": result.hr_input_hash,
        "terrain_input_hash": "terrain-hash",
        "final_result_hash": "final-hash",
        "config": {"downhill_threshold_pct": -3.0},
        "diagnostics": {"terrain_rejected_wave_count": 1},
        "timeseries": [
            {
                "timestamp": START,
                "elapsed_s": 0.0,
                "raw_hr_bpm": 100.0,
                "hrmod_candidate_bpm": 102.0,
                "hrmod_final_bpm": 100.0,
                "smoothed_grade_pct": -4.0,
                "downhill_mask": True,
                "terrain_status": "terrain_confounded",
            }
        ],
        "wave_summary": [
            {
                "wave_id": 1,
                "terrain_status": "terrain_confounded",
                "terrain_rejection_reason": "sustained_downhill_overlap",
                "downhill_overlap_s": 7.0,
                "downhill_overlap_fraction": 0.25,
                "min_smoothed_grade_pct": -4.0,
                "moved_area_candidate_bpm_s": 40.0,
                "moved_area_final_bpm_s": 0.0,
            }
        ],
        "zone_summary": [
            {
                "zone_name": zone.zone_name,
                "lower_bpm": zone.lower_bpm,
                "upper_bpm": zone.upper_bpm,
                "raw_seconds": zone.raw_seconds,
                "raw_percent": zone.raw_percent,
                "hrmod_candidate_seconds": zone.hrmod_seconds,
                "hrmod_candidate_percent": zone.hrmod_percent,
                "hrmod_final_seconds": zone.raw_seconds,
                "hrmod_final_percent": zone.raw_percent,
                "final_minus_candidate_seconds": (
                    zone.raw_seconds - zone.hrmod_seconds
                ),
                "final_minus_raw_seconds": 0.0,
            }
            for zone in result.zone_summary
        ],
    }

    files = build_export_bundle(
        hrmod_result=result,
        terrain_result=terrain_result,
    )
    core_only_files = build_export_bundle(hrmod_result=result)
    assert files["zone_summary.csv"] == core_only_files["zone_summary.csv"]

    core_header = files["processed_hr_timeseries.csv"].splitlines()[0].decode()
    assert "smoothed_grade_pct" not in core_header
    assert "terrain_status" not in core_header
    terrain_header = files["terrain_gated_timeseries.csv"].splitlines()[0].decode()
    assert {
        "raw_hr_bpm",
        "hrmod_candidate_bpm",
        "hrmod_final_bpm",
        "smoothed_grade_pct",
        "downhill_mask",
        "terrain_status",
    }.issubset(set(terrain_header.split(",")))
    wave_header = files["terrain_wave_summary.csv"].splitlines()[0].decode()
    assert "moved_area_final_bpm_s" in wave_header
    terrain_zone_header = files["terrain_zone_summary.csv"].splitlines()[0].decode()
    assert {
        "raw_seconds",
        "hrmod_candidate_seconds",
        "hrmod_final_seconds",
        "final_minus_candidate_seconds",
    }.issubset(set(terrain_zone_header.split(",")))
    core_zone_header = files["zone_summary.csv"].splitlines()[0].decode()
    assert "hrmod_candidate_seconds" not in core_zone_header
    assert "hrmod_final_seconds" not in core_zone_header
    manifest = json.loads(files["manifest.json"])
    assert "terrain_zone_summary.csv" in manifest["files"]
    assert manifest["hr_input_hash"] == result.hr_input_hash
    assert manifest["terrain_input_hash"] == "terrain-hash"
    assert manifest["final_result_hash"] == "final-hash"


def test_service_keeps_computation_and_reference_evaluation_in_two_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core_run = run_hr_only_phase(
        tcx_source=_tcx_bytes(_response(), include_reference=True),
        athlete_profile=_profile(),
        hrmod_config=_config(),
    )
    before = deepcopy(core_run.hrmod_result)

    def fail_if_core_is_recomputed(**_kwargs: object) -> None:
        raise AssertionError("reference phase must not call the HR-only core")

    monkeypatch.setattr(
        "hrmod_lab.hrmod_service.compute_hrmod_hr_only", fail_if_core_is_recomputed
    )
    validated = run_reference_phase(core_run=core_run)
    assert core_run.hrmod_result == before
    assert validated.core_run is core_run
    assert validated.hrmod_result == before
    assert validated.validation_result.hr_input_hash == before.hr_input_hash

