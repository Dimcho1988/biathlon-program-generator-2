from copy import deepcopy

import pytest

from biathlon import unified_load


BOUNDS = (80, 120, 140, 160, 180, 200)


def recording():
    return {"timeseries": [
        {"elapsed_s": 0., "hr_raw_bpm": 150., "hr_clean_bpm": 150.,
         "hrmod_candidate_bpm": 170., "hrmod_final_bpm": 170.,
         "hr_interval_classification": "smart_recording", "hr_interval_end_s": 4.},
        {"elapsed_s": 4., "hr_raw_bpm": 170., "hr_clean_bpm": 170.,
         "hrmod_candidate_bpm": 190., "hrmod_final_bpm": 190.}],
        "speed_test_series": [{"elapsed_s": 4., "dt_s": 4., "load_status": "ACTIVE", "load_speed_kmh": 30.}],
        "trainability_index": {"zone_bounds_bpm": list(BOUNDS)}}


def test_all_channels_split_smart_recording_crossings_and_model_keeps_hrmod():
    shadow = recording()
    before = deepcopy(shadow)
    comparison = unified_load.hr_zone_times(shadow, bounds=BOUNDS, sport="Run", duration_s=4.)
    by_zone = {r["zone_name"]:r for r in comparison["rows"]}
    assert by_zone["Z3"]["raw_seconds"] == pytest.approx(2.)
    assert by_zone["Z4"]["raw_seconds"] == pytest.approx(2.)
    assert by_zone["Z4"]["hrmod_final_seconds"] == pytest.approx(2.)
    assert by_zone["Z5"]["hrmod_final_seconds"] == pytest.approx(2.)
    model = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", duration_s=4.)
    assert model["hr_coordinate"] == "CURRENT_HRMOD_WHEN_AVAILABLE"
    assert model["source_seconds"] == {"HEART_RATE":4.,"SPEED":0.}
    assert [r["raw_time_min"]*60 for r in model["zones"]] == pytest.approx(
        [r["hrmod_final_seconds"] for r in comparison["rows"]])
    assert shadow == before


def test_missing_derived_channel_stays_unknown_in_comparison_without_substitution():
    shadow = recording()
    shadow["timeseries"][0]["hrmod_final_bpm"] = None
    comparison = unified_load.hr_zone_times(shadow, bounds=BOUNDS, sport="Run", duration_s=4.)
    assert comparison["channels"]["raw"]["unknown_seconds"] == 0.
    assert comparison["channels"]["hrmod_final"]["unknown_seconds"] == 4.
    assert all(r["hrmod_final_seconds"] == 0. for r in comparison["rows"])


def test_derived_hr_never_makes_missing_measured_hr_available():
    shadow = recording()
    for row in shadow["timeseries"]:
        row["hr_raw_bpm"] = None
    comparison = unified_load.hr_zone_times(shadow, bounds=BOUNDS, sport="Run", duration_s=4.)
    assert all(v["unknown_seconds"] == 4. for v in comparison["channels"].values())


def test_long_gap_does_not_receive_time_from_either_hr_channel():
    shadow = recording()
    shadow["timeseries"][1]["elapsed_s"] = 30.
    comparison = unified_load.hr_zone_times(shadow, bounds=BOUNDS, sport="Run", duration_s=30.)
    for channel in comparison["channels"].values():
        assert channel["active_seconds"] == pytest.approx(1.)
        assert channel["unknown_seconds"] == pytest.approx(29.)


def test_cycling_reference_offset_is_used_once_in_both_displays_and_model():
    shadow = recording()
    for row in shadow["timeseries"]:
        for key in ("hr_raw_bpm", "hr_clean_bpm", "hrmod_candidate_bpm", "hrmod_final_bpm"):
            row[key] -= 7.
    comparison = unified_load.hr_zone_times(shadow, bounds=BOUNDS, sport="Ride", duration_s=4.)
    model = unified_load.integrate(shadow, bounds=BOUNDS, sport="Ride", duration_s=4.)
    assert [r["raw_time_min"]*60 for r in model["zones"]] == pytest.approx(
        [r["hrmod_final_seconds"] for r in comparison["rows"]])
    assert comparison["rows"][2]["raw_seconds"] == pytest.approx(2.)


def test_explicit_signal_and_zone_contracts_are_validated():
    with pytest.raises(ValueError, match="signal"):
        unified_load.integrate(recording(), bounds=BOUNDS, sport="Run", hr_signal="unknown")
    with pytest.raises(ValueError, match="ordered"):
        unified_load.hr_zone_times(recording(), bounds=BOUNDS[::-1], sport="Run")
