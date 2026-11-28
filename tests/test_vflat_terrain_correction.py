from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from vflat_b65 import apply_vflat_b65
from vflat_b65.terrain_correction import terrain_correction
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
@pytest.mark.parametrize("density", [0, 6.17754, 20, 29.3548439988, 29.355, 50])
def test_session_density_never_rescales_sample_speeds_or_eligibility(sport, density):
    source = frame()
    baseline = apply_vflat_b65(source)
    corrected = apply_vflat_b65(source, activity_detail=detail(type=sport, distance=1000, total_elevation_gain=density))
    np.testing.assert_allclose(corrected.vflat_b65_kmh, baseline.vflat_b65_kmh)
    np.testing.assert_array_equal(corrected.valid, baseline.valid)
    assert corrected.attrs["terrain_correction"]["factor"] == 1
    assert not corrected.attrs["terrain_correction"]["applied"]
    assert corrected.attrs["terrain_correction"]["gain_per_km"] == density


def test_uphill_correction_still_increases_speed_and_downhill_can_reduce_it():
    uphill = apply_vflat_b65(frame(6), activity_detail=detail())
    downhill = apply_vflat_b65(frame(-6), activity_detail=detail())
    assert uphill.vflat_b65_kmh.mean() > 18
    assert downhill.vflat_b65_kmh.mean() < 18


@pytest.mark.parametrize("metadata", [detail(total_elevation_gain=None), detail(distance=0), detail(total_elevation_gain=-1)])
def test_missing_or_invalid_totals_are_diagnostic_only(metadata):
    result = terrain_correction(frame(count=0), metadata)
    assert result["factor"] == 1 and result["gain_per_km"] is None


def test_metadata_invalidates_cached_result_but_not_model_comparison_key():
    kw = dict(zone_bounds_bpm=(50, 137, 148, 160, 170, 180), explicit_hrmax_bpm=180)
    baseline = activity_shadow_configuration_fingerprint(**kw, activity_detail=detail())
    for changes in ({"total_elevation_gain": 300}, {"distance": 25000}, {"type": "Run"}):
        assert activity_shadow_configuration_fingerprint(**kw, activity_detail=detail(**changes)) != baseline
    assert activity_shadow_configuration_fingerprint(**kw, activity_detail=detail(name="different")) == baseline


def test_pipeline_keeps_index_speed_hr_and_valid_time_independent_of_session_totals():
    count = 901
    normalized = normalize_stream_intervals(NormalizerInput(offsets=list(range(count)), metrics={
        "heartrate": [150.0] * count, "velocity_smooth": [5.0] * count,
        "gradient": [0.0] * count,
    }))
    kwargs = dict(normalized=normalized, zone_bounds_bpm=(50, 137, 148, 160, 170, 180), explicit_hrmax_bpm=180)
    _, baseline = compute_activity_shadow(detail=detail(total_elevation_gain=None), **kwargs)
    _, corrected = compute_activity_shadow(detail=detail(), **kwargs)
    factor = corrected["diagnostics"]["vflat"]["terrain_correction"]["factor"]
    assert factor == 1.0
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
