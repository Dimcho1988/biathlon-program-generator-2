from __future__ import annotations

from copy import deepcopy

import pytest

from apps.api.activity_shadow_pipeline import (
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
        "vflat_model_version", "hrmod_model_version", "terrain_model_version",
        "quality_flags", "exclusion_reason",
    }
    assert required <= set(derived["timeseries"][0])
    assert derived["timeseries"][0]["exclusion_reason"] == "EXPLICIT_HRMAX_MISSING"
    assert len(derived["segments_15s"]) >= 6


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
    assert derived["hrmod_model_version"] == "hrmod_mirror_area_shift_v4"
    assert any(row["hr_clean_bpm"] is not None for row in derived["timeseries"])
    assert derived["schema_version"] == "activity-shadow-derived-v2"
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
    with pytest.raises(ValueError):
        compute_activity_shadow(
            detail={"start_date": "2026-01-01T10:00:00Z"},
            normalized=_normalized(),
            zone_bounds_bpm=(50, 100, 120, 140, 160, 200),
            explicit_hrmax_bpm=190,
        )


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
