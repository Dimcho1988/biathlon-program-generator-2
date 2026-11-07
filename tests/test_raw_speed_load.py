from copy import deepcopy

import pytest

from biathlon import raw_speed, speed_equivalence, unified_load
from tests.test_unified_zone_load import BOUNDS, profile, streams


def raw_profile():
    return {**profile(), "speed_role": raw_speed.ROLE,
            "source": "INITIAL_HEURISTIC_V50_REFERENCE_SCALE", "initial_heuristic": True}


def raw_stream(speed=12., seconds=60):
    value = streams(None, 24., seconds)
    for row in value["speed_test_series"]:
        row.update(speed_raw_kmh=speed, load_speed_kmh=25., load_status="ACTIVE",
                   load_source="DESCENT_BALANCE", grade_smoothed_pct=-15., exclusion_reason="DOWNHILL")
    return value


def test_raw_coordinate_uses_raw_observation_and_retains_explicit_heuristic_provenance():
    p = speed_equivalence.prepare(raw_profile())
    shadow = raw_stream()
    original = deepcopy(shadow)
    ledger = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=p, duration_s=60)
    expected = speed_equivalence.evaluate(p, 12.)
    assert ledger["source_seconds"] == {"HEART_RATE": 0., "SPEED": 60.}
    assert ledger["fallback_seconds"] == 60.
    assert ledger["speed_role"] == raw_speed.ROLE
    assert ledger["initial_heuristic"] is True and ledger["calibration_source"] == "INITIAL_HEURISTIC"
    assert ledger["calibration_eligible"] is False and ledger["estimated_descent_seconds"] == 0
    assert next(z for z in ledger["zones"] if z["zone"] == expected["zone"])["equivalent_time_min"] == pytest.approx(expected["coefficient"])
    assert shadow == original
    assert speed_equivalence.prepare(profile())["parameters_version"] != p["parameters_version"]


@pytest.mark.parametrize("raw,expected_rest,expected_unknown", [(None, 0, 60), (0., 60, 0), (-1., 0, 60), (1., 60, 0)])
def test_corrected_speed_cannot_fill_absent_raw_or_turn_unknown_into_rest(raw, expected_rest, expected_unknown):
    ledger = unified_load.integrate(raw_stream(raw), bounds=BOUNDS, sport="Run",
        parameters=speed_equivalence.prepare(raw_profile()), duration_s=60)
    assert ledger["rest_seconds"] == expected_rest
    assert ledger["unknown_seconds"] == expected_unknown


def test_missing_raw_falls_back_to_measured_hr_and_gap_stays_unknown():
    shadow = raw_stream(None)
    shadow["timeseries"] = [{"elapsed_s": i, "hr_raw_bpm": 160.} for i in range(30)]
    ledger = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run",
        parameters=speed_equivalence.prepare(raw_profile()), duration_s=60, leading_indicator="SPEED")
    assert ledger["source_seconds"] == {"HEART_RATE": 30., "SPEED": 0.}
    assert ledger["fallback_seconds"] == 30. and ledger["unknown_seconds"] == 30.


def test_legacy_raw_timestamps_cover_only_supported_intervals_and_ignore_terrain_versions():
    shadow = {"speed_test_series": [{"elapsed_s": 60., "dt_s": 60., "vflat_b65_kmh": 24.}],
              "timeseries": [{"elapsed_s": i, "speed_raw_kmh": 12.} for i in [0., 1., 30., 31.]]}
    assert raw_speed.available(shadow)
    assert sum(right-left for left,right,_,_ in raw_speed.intervals(shadow)) == 4.
    ledger = unified_load.integrate(shadow, bounds=BOUNDS, sport="Ride",
        parameters=speed_equivalence.prepare(raw_profile(), sport="Ride"), duration_s=60)
    assert ledger["active_seconds"] == pytest.approx(4.)
    assert ledger["unknown_seconds"] == 56.


def test_explicit_missing_independent_raw_never_borrows_another_coordinate():
    shadow = raw_stream(None, 1)
    shadow["timeseries"] = [{"elapsed_s": 0., "speed_raw_kmh": 15.}]
    assert raw_speed.intervals(shadow) == [(0., 1., None, "INVALID_RAW_SPEED")]
