from __future__ import annotations

from copy import deepcopy
import json

import pytest

from apps.api.activity_shadow_pipeline import (
    ActivityShadowStageError,
    activity_shadow_configuration_fingerprint,
    build_immutable_activity_input,
    compute_activity_shadow,
)
from intervals_inspector.stream_normalizer import (
    NormalizerInput,
    normalize_stream_intervals,
)


def _normalized(count: int = 100):
    offsets = list(range(count))
    metrics = {
        "heartrate": [120.0 + min(index, 30) * 2.0 if index < 35 else 180.0 - min(index - 35, 40) * 1.5 for index in offsets],
        "velocity_smooth": [5.0 + index * 0.001 for index in offsets],
        "gradient": [-4.0 if 45 <= index <= 55 else 6.0 for index in offsets],
        "altitude": [100.0 + index * 0.3 for index in offsets],
        "distance": [index * 5.0 for index in offsets],
    }
    return normalize_stream_intervals(
        NormalizerInput(offsets=offsets, metrics=metrics)
    )


def test_duration_gate_invalidates_cache_without_splitting_comparable_activities():
    outputs = []
    for duration in (419, 420):
        _, derived = compute_activity_shadow(
            detail={"start_date": "2026-01-01T10:00:00Z", "moving_time": duration},
            normalized=_normalized(), zone_bounds_bpm=(50, 137, 147, 158, 170, 178),
            explicit_hrmax_bpm=178,
        )
        outputs.append(derived)
    short, long = outputs
    assert short["configuration_fingerprint"] != long["configuration_fingerprint"]
    assert short["trainability_index"]["comparison_key"] == long["trainability_index"]["comparison_key"]
    assert short["trainability_index"]["general"]["invalid_reason"] == "ACTIVITY_BELOW_7MIN"
    assert long["trainability_index"]["general"]["invalid_reason"] != "ACTIVITY_BELOW_7MIN"
    for key in ("timeseries", "zone_summary", "hrmod_waves", "segments_15s"):
        assert short[key] == long[key]


def test_index_version_invalidates_cache_and_comparison_key(monkeypatch):
    from apps.api import activity_shadow_pipeline as pipeline

    kwargs = dict(zone_bounds_bpm=(50, 137, 147, 158, 170, 178), explicit_hrmax_bpm=178)
    current = activity_shadow_configuration_fingerprint(**kwargs, activity_duration_s=900)
    current_comparison = activity_shadow_configuration_fingerprint(**kwargs)
    monkeypatch.setattr(pipeline, "TRAINABILITY_MODEL_VERSION", "trainability_rank_v1")
    assert activity_shadow_configuration_fingerprint(**kwargs, activity_duration_s=900) != current
    assert activity_shadow_configuration_fingerprint(**kwargs) != current_comparison


def test_vflat_v4_version_invalidates_old_cached_results(monkeypatch):
    from apps.api import activity_shadow_pipeline as pipeline

    kwargs = dict(zone_bounds_bpm=(50, 137, 147, 158, 170, 178), explicit_hrmax_bpm=178)
    current = activity_shadow_configuration_fingerprint(**kwargs)
    monkeypatch.setattr(pipeline, "VFLAT_MODEL_VERSION", "vflat_b65_dynamic_v3_uphill150")
    monkeypatch.setattr(pipeline, "VFLAT_CONFIG_VERSION", "vflat_b65_config_v3_uphill150")
    assert activity_shadow_configuration_fingerprint(**kwargs) != current


def test_vflat_preparation_does_not_smooth_across_recording_gaps():
    from apps.api.activity_shadow_pipeline import _model_inputs
    import numpy as np

    offsets = list(range(40)) + list(range(100, 140))
    normalized = normalize_stream_intervals(NormalizerInput(offsets=offsets, metrics={
        "heartrate": [145.0] * 80,
        "velocity_smooth": [3.0] * 40 + [8.0] * 40,
        "altitude": [100.0] * 40 + [500.0] * 40,
        "distance": [i * 3.0 for i in range(40)] + [1000.0 + i * 8.0 for i in range(40)],
    }))
    _, _, frame = _model_inputs({"start_date": "2026-01-01T10:00:00Z"}, normalized)
    active = frame[frame.block >= 0]
    assert active.block.nunique() == 2
    np.testing.assert_allclose(active.accel_mps2, 0.0, atol=1e-12)
    np.testing.assert_allclose(active.grade_pct, 0.0, atol=1e-8)


def test_vflat_v4_calculated_grade_reaches_payload_and_segments():
    offsets = list(range(90))
    normalized = normalize_stream_intervals(NormalizerInput(offsets=offsets, metrics={
        "heartrate": [145.0] * 90, "velocity_smooth": [5.0] * 90,
        "gradient": [-6.0] * 30 + [10.0] * 60,
    }))
    _, derived = compute_activity_shadow(
        detail={"start_date": "2026-01-01T10:00:00Z", "moving_time": 900},
        normalized=normalized, zone_bounds_bpm=(50, 137, 147, 158, 170, 178), explicit_hrmax_bpm=178,
    )
    row = derived["timeseries"][30]
    assert row["grade_vflat_actual_pct"] == 10.0
    assert row["grade_vflat_effective_pct"] == pytest.approx(-0.2)
    assert row["grade_vflat_stationary_pct"] == pytest.approx(-0.2)
    assert derived["segments_15s"][2]["grade_vflat_effective_pct"] < 10.0
    assert derived["vflat_model_version"] == "vflat_b65_dynamic_v4_uphill120_memory170"


def test_immutable_input_is_minimal_and_original_normalized_data_is_unchanged() -> None:
    detail = {"start_date": "2026-01-01T10:00:00Z", "name": "private name"}
    normalized = _normalized()
    before = deepcopy(normalized)
    stored = build_immutable_activity_input(detail, normalized)
    assert normalized == before
    assert set(stored) == {
        "schema_version", "normalization_version", "samples", "input_hash"
    }
    assert set(stored["samples"][0]) == {
        "timestamp", "elapsed_s", "hr_raw_bpm", "speed_raw_kmh",
        "grade_raw_pct", "altitude_m", "cumulative_distance_m", "quality_flags",
    }
    assert "name" not in str(stored)
    assert "latlng" not in str(stored)


def test_shadow_results_have_parallel_fields_and_missing_hrmax_fails_closed() -> None:
    detail = {"start_date": "2026-01-01T10:00:00Z"}
    normalized = _normalized()
    immutable, derived = compute_activity_shadow(
        detail=detail,
        normalized=normalized,
        zone_bounds_bpm=(50, 100, 120, 140, 160, 190),
        explicit_hrmax_bpm=None,
    )
    assert len(immutable["samples"]) == 100
    assert derived["experimental"] is True
    assert derived["affects_canonical_load"] is False
    assert derived["diagnostics"]["hrmod"]["flags"] == ["EXPLICIT_HRMAX_MISSING"]
    required = {
        "speed_raw_kmh", "vflat_b65_kmh", "vflat_delta_kmh", "hr_raw_bpm",
        "hr_clean_bpm", "hrmod_candidate_bpm", "hrmod_final_bpm",
        "hrmod_delta_bpm", "grade_raw_pct", "grade_smoothed_pct",
        "sprint_str_flag", "sprint_str_reference_kmh", "sprint_str_rise_kmh",
        "vflat_model_version", "hrmod_model_version", "terrain_model_version",
        "quality_flags", "exclusion_reason",
    }
    assert required <= set(derived["timeseries"][0])
    assert derived["timeseries"][0]["exclusion_reason"] == "EXPLICIT_HRMAX_MISSING"
    assert len(derived["segments_15s"]) >= 6
    assert derived["sprint_str_model_version"] == "vflat_sprint_str_v1"
    assert derived["sprint_str_summary"]["affects_canonical_load"] is False
    assert derived["sprint_str_summary"]["double_counts_hr_zones"] is False


def test_hr_only_shadow_normalizes_unavailable_vflat_numbers_to_null() -> None:
    normalized = normalize_stream_intervals(
        NormalizerInput(
            offsets=list(range(61)),
            metrics={"heartrate": [145.0] * 61},
        )
    )

    _, derived = compute_activity_shadow(
        detail={"start_date": "2026-01-01T10:00:00Z"},
        normalized=normalized,
        zone_bounds_bpm=(50, 100, 120, 140, 160, 190),
        explicit_hrmax_bpm=200,
    )

    assert all(row["vflat_b65_kmh"] is None for row in derived["timeseries"])
    assert all(row["grade_smoothed_pct"] is None for row in derived["timeseries"])
    assert all(row["vflat_b65_kmh"] is None for row in derived["segments_15s"])
    assert all(row["grade_smoothed_pct"] is None for row in derived["segments_15s"])
    json.dumps(derived, allow_nan=False)


@pytest.mark.parametrize(
    ("heart_rate", "reason"),
    [
        (None, "HRMOD_NO_USABLE_HR"),
        (20.0, "HRMOD_NO_USABLE_HR"),
    ],
)
def test_unsuitable_hr_excludes_only_hrmod_diagnostics(
    heart_rate: float | None, reason: str
) -> None:
    normalized = normalize_stream_intervals(
        NormalizerInput(
            offsets=list(range(61)),
            metrics={
                "heartrate": [heart_rate] * 61,
                "velocity_smooth": [5.0] * 61,
            },
        )
    )

    _, derived = compute_activity_shadow(
        detail={"start_date": "2026-01-01T10:00:00Z"},
        normalized=normalized,
        zone_bounds_bpm=(100, 120, 140, 160, 180, 190),
        explicit_hrmax_bpm=200,
    )

    assert derived["diagnostics"]["hrmod"]["flags"] == [reason]
    assert all(row["exclusion_reason"] == reason for row in derived["timeseries"])
    assert any(row["vflat_model_version"] for row in derived["timeseries"])
    assert derived["affects_canonical_load"] is False


def test_explicit_hrmax_enables_hrmod_without_changing_immutable_input() -> None:
    detail = {"start_date": "2026-01-01T10:00:00Z"}
    normalized = _normalized()
    immutable_without, _ = compute_activity_shadow(
        detail=detail,
        normalized=normalized,
        zone_bounds_bpm=(50, 100, 120, 140, 160, 190),
        explicit_hrmax_bpm=None,
    )
    immutable_with, derived = compute_activity_shadow(
        detail=detail,
        normalized=normalized,
        zone_bounds_bpm=(50, 100, 120, 140, 160, 190),
        explicit_hrmax_bpm=200,
    )
    assert immutable_with == immutable_without
    assert derived["hrmod_model_version"] == "hrmod_mirror_area_shift_v9"


def test_hr_below_z1_does_not_exclude_the_whole_activity() -> None:
    normalized = normalize_stream_intervals(
        NormalizerInput(
            offsets=list(range(61)),
            metrics={
                "heartrate": [75.0] * 20 + [145.0] * 21 + [100.0] * 20,
                "velocity_smooth": [5.0] * 61,
            },
        )
    )

    _, derived = compute_activity_shadow(
        detail={"start_date": "2026-01-01T10:00:00Z"},
        normalized=normalized,
        zone_bounds_bpm=(80, 100, 120, 140, 160, 180),
        explicit_hrmax_bpm=180,
    )

    assert derived["diagnostics"]["hrmod"]["flags"] != [
        "HRMOD_HR_OUTSIDE_PROFILE"
    ]
    assert any(row["hr_clean_bpm"] == 75.0 for row in derived["timeseries"])
    assert any(row["hr_clean_bpm"] is not None for row in derived["timeseries"])
    assert derived["schema_version"] == "activity-shadow-derived-v3"
    assert len(derived["zone_summary"]) == 5
    assert {
        "raw_seconds",
        "clean_seconds",
        "hrmod_candidate_seconds",
        "hrmod_final_seconds",
        "final_minus_clean_seconds",
    } <= set(derived["zone_summary"][0])


def test_shadow_configuration_changes_for_zones_or_hrmax() -> None:
    baseline = activity_shadow_configuration_fingerprint(
        (50, 100, 120, 140, 160, 190), 200
    )
    assert activity_shadow_configuration_fingerprint(
        (50, 101, 120, 140, 160, 190), 200
    ) != baseline
    assert activity_shadow_configuration_fingerprint(
        (50, 100, 120, 140, 160, 190), 201
    ) != baseline


def test_explicit_hrmax_must_not_be_inferred_from_z5() -> None:
    with pytest.raises(ActivityShadowStageError) as caught:
        compute_activity_shadow(
            detail={"start_date": "2026-01-01T10:00:00Z"},
            normalized=_normalized(),
            zone_bounds_bpm=(50, 100, 120, 140, 160, 200),
            explicit_hrmax_bpm=190,
        )
    assert caught.value.safe_stage == "PROFILE"


def test_hrmod_keeps_original_irregular_timestamps_and_gap_flags() -> None:
    offsets = [0, 1, 2, 4, 7, 20, 21, 22]
    normalized = normalize_stream_intervals(
        NormalizerInput(
            offsets=offsets,
            metrics={
                "heartrate": [120, 121, 122, 124, 126, 130, 131, 132],
                "velocity_smooth": [5.0] * len(offsets),
                "gradient": [0.0] * len(offsets),
            },
        )
    )
    immutable, derived = compute_activity_shadow(
        detail={"start_date": "2026-01-01T10:00:00Z"},
        normalized=normalized,
        zone_bounds_bpm=(50, 100, 120, 140, 160, 190),
        explicit_hrmax_bpm=200,
    )
    assert [row["elapsed_s"] for row in derived["timeseries"]] == offsets
    assert len(immutable["samples"]) == len(offsets)
    assert any(row["quality_flags"] for row in derived["timeseries"])


def test_speed_test_series_is_full_one_hz_and_does_not_require_hr():
    from apps.api.model_service import segment_measurement
    normalized = normalize_stream_intervals(NormalizerInput(
        offsets=list(range(0, 721, 5)),
        metrics={"velocity_smooth": [6.0]*145, "gradient": [0.0]*145},
    ))
    _, derived = compute_activity_shadow(
        detail={"start_date": "2026-01-01T10:00:00Z"}, normalized=normalized,
        zone_bounds_bpm=(80,100,120,140,160,180), explicit_hrmax_bpm=None,
    )
    assert len(derived["timeseries"]) == 145
    assert len(derived["speed_test_series"]) == 721
    measured = segment_measurement(derived, 0, 720)
    assert measured["coverage_percent"] == 100
    assert measured["speed_kmh"] == pytest.approx(21.6)


@pytest.mark.parametrize('old_version', ['v7', 'v8'])
def test_hrmod_v9_invalidates_previous_cache_and_index_comparison(monkeypatch, old_version):
    from apps.api import activity_shadow_pipeline as pipeline

    kwargs = dict(zone_bounds_bpm=(80, 137, 148, 160, 170, 180), explicit_hrmax_bpm=180)
    current = activity_shadow_configuration_fingerprint(**kwargs, activity_duration_s=900)
    comparison = activity_shadow_configuration_fingerprint(**kwargs)
    monkeypatch.setattr(pipeline, 'HRMOD_MODEL_VERSION', f'hrmod_mirror_area_shift_{old_version}')
    monkeypatch.setattr(pipeline, 'HRMOD_CONFIG_VERSION', f'hrmod_config_{old_version}')
    assert activity_shadow_configuration_fingerprint(**kwargs, activity_duration_s=900) != current
    assert activity_shadow_configuration_fingerprint(**kwargs) != comparison
