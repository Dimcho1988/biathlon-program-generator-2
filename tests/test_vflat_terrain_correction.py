from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from vflat_b65 import apply_vflat_b65
from vflat_b65.terrain_correction import DENSITY_RANGE, terrain_correction
from apps.api.activity_shadow_pipeline import activity_shadow_configuration_fingerprint, compute_activity_shadow
from intervals_inspector.stream_normalizer import NormalizerInput, normalize_stream_intervals


def frame(grade=0.0, count=901):
    return pd.DataFrame({
        "elapsed_s": np.arange(count, dtype=float), "grade_pct": grade,
        "speed_raw_mps": 5.0, "speed_mps": 5.0, "accel_mps2": 0.0,
        "block": 0, "turn_flag": False, "vflat_b65_kmh": 18.0,
    })


def detail(**changes):
    return {"type": "NordicSki", "distance": 20000, "total_elevation_gain": 400,
            "moving_time": 900, "start_date": "2026-01-01T10:00:00Z", **changes}


@pytest.mark.parametrize("sport", ["NordicSki", "Walk"])
def test_approved_20_m_per_km_example_scales_all_samples_without_changing_eligibility(sport):
    source = frame()
    baseline = apply_vflat_b65(source)
    corrected = apply_vflat_b65(source, activity_detail=detail(type=sport))
    expected = 1 / (1 + (0.8834239445414596 * 20 - 7.974288128873758) / 100)
    np.testing.assert_allclose(corrected.vflat_b65_kmh, baseline.vflat_b65_kmh * expected)
    np.testing.assert_array_equal(corrected.valid, baseline.valid)
    pd.testing.assert_series_equal(corrected.grade_actual_pct, baseline.grade_actual_pct)
    assert corrected.attrs["terrain_correction"]["applied"]
    assert corrected.attrs["terrain_correction"]["gain_per_km"] == 20


@pytest.mark.parametrize("metadata,grade,reason", [
    (detail(), 6.0, "UPHILL_ONLY"),
    (detail(type="Run"), 0.0, "SPORT_NOT_VALIDATED"),
    (detail(total_elevation_gain=None), 0.0, "TERRAIN_METADATA_MISSING"),
    (detail(total_elevation_gain=-1), 0.0, "TERRAIN_METADATA_MISSING"),
    (detail(distance=0), 0.0, "TERRAIN_METADATA_MISSING"),
    (detail(distance=float("nan")), 0.0, "TERRAIN_METADATA_MISSING"),
    (detail(total_elevation_gain=800), 0.0, "OUTSIDE_VALIDATED_RANGE"),
    (detail(total_elevation_gain=100), 0.0, "OUTSIDE_VALIDATED_RANGE"),
    (detail(total_elevation_gain=160), 0.0, "BELOW_CORRECTION_THRESHOLD"),
])
def test_no_reduction_for_unvalidated_cases(metadata, grade, reason):
    result = terrain_correction(frame(grade), metadata)
    assert result["factor"] == 1.0
    assert not result["applied"]
    assert result["reason"] == reason


def test_domain_boundaries_and_empty_samples():
    for density in DENSITY_RANGE:
        result = terrain_correction(frame(), detail(distance=1000, total_elevation_gain=density))
        assert result["reason"] != "OUTSIDE_VALIDATED_RANGE"
    assert terrain_correction(frame(count=0), detail())["factor"] == 1


def test_recording_gap_does_not_join_short_stops_or_add_terrain_time():
    source = frame(count=7)
    source["elapsed_s"] = [0, 1, 2, 100, 101, 102, 103]
    source["speed_raw_mps"] = [0, 0, 0, 0, 0, 5, 5]
    source["grade_pct"] = [6, 6, 6, 6, 6, 0, 0]
    result = terrain_correction(source, detail())
    assert result["uphill_time_share"] == pytest.approx(3 / 5)
    assert result["applied"]


def test_metadata_invalidates_cached_result_but_not_model_comparison_key():
    kw = dict(zone_bounds_bpm=(50, 137, 148, 160, 170, 180), explicit_hrmax_bpm=180)
    baseline = activity_shadow_configuration_fingerprint(**kw, activity_detail=detail())
    for changes in ({"total_elevation_gain": 300}, {"distance": 25000}, {"type": "Run"}):
        assert activity_shadow_configuration_fingerprint(**kw, activity_detail=detail(**changes)) != baseline
    assert activity_shadow_configuration_fingerprint(**kw, activity_detail=detail(name="different")) == baseline


def test_pipeline_corrects_index_speed_and_diagnostics_with_identical_hr_and_valid_time():
    count = 901
    normalized = normalize_stream_intervals(NormalizerInput(offsets=list(range(count)), metrics={
        "heartrate": [150.0] * count, "velocity_smooth": [5.0] * count,
        "gradient": [0.0] * count,
    }))
    kwargs = dict(normalized=normalized, zone_bounds_bpm=(50, 137, 148, 160, 170, 180), explicit_hrmax_bpm=180)
    _, baseline = compute_activity_shadow(detail=detail(total_elevation_gain=None), **kwargs)
    _, corrected = compute_activity_shadow(detail=detail(), **kwargs)
    factor = corrected["diagnostics"]["vflat"]["terrain_correction"]["factor"]
    a, b = baseline["trainability_index"], corrected["trainability_index"]
    assert b["comparison_key"] == a["comparison_key"]
    assert corrected["configuration_fingerprint"] != baseline["configuration_fingerprint"]
    for before, after in zip(a["zones"] + [a["general"]], b["zones"] + [b["general"]]):
        assert before["valid"] == after["valid"]
        assert before["speed_seconds"] == after["speed_seconds"]
        assert before["mean_hr_bpm"] == after["mean_hr_bpm"]
        if before["valid"]:
            assert after["index"] == pytest.approx(before["index"] / factor)
    for before, after in zip(baseline["speed_test_series"], corrected["speed_test_series"]):
        assert after["vflat_b65_kmh"] == pytest.approx(before["vflat_b65_kmh"] * factor)
    assert corrected["zone_summary"] == baseline["zone_summary"]
    assert corrected["hrmod_waves"] == baseline["hrmod_waves"]
