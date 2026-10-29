from copy import deepcopy
import json

import pytest

from biathlon import dosing_curve, preliminary_capacity, speed_duration, zone_profile


BOUNDS = (90, 144, 158, 178, 188, 200)
TESTS = [{"duration_s": t, "speed_kmh": v, "maximal": True,
          "comparable": True, "source": "MANUAL", "test_mode": "STRICT"}
         for t, v in ((180., 23.), (720., 19.2))]


def curve():
    return speed_duration.with_ten_second_tail(speed_duration.calibrated(TESTS))


def pair(value):
    return {"index": value, "count": 2, "seconds": 900}


def conflicting():
    return {"Z1": pair(3), "Z2": pair(10), "GENERAL": pair(100)}


def test_conflicting_zonal_and_general_indices_use_real_test_curve_without_mutation():
    indices = conflicting()
    original = deepcopy(indices)
    profile = zone_profile.build(curve(), BOUNDS, 200, indices, tests=TESTS)
    direct = zone_profile.build(curve(), BOUNDS, 200, {}, tests=TESTS)
    assert profile["status"] == "AVAILABLE"
    assert profile["bounds_kmh"] == pytest.approx(direct["bounds_kmh"])
    assert profile["reason"] == "CONFLICTING_PAIRED_BOUNDARIES_TEST_FALLBACK"
    assert profile["basis"] == "TEST_CURVE_AFTER_PAIRED_PROFILE_CONFLICT"
    assert len(profile["excluded_profiles"]) == 2
    assert all(z["source"] == "TEST_CURVE_CONFLICT_FALLBACK" for z in profile["zones"][:4])
    assert profile["accepted_test_count"] == 2
    assert profile["calibration_eligible"] is False
    assert indices == original
    json.dumps(profile, allow_nan=False)


@pytest.mark.parametrize("mutation", [
    {"maximal": False}, {"generated": True}, {"is_estimated": True},
    {"source": "MODEL"}, {"source": "REFERENCE"}, {"test_mode": "EXPLORATORY"},
    {"kind": "MODEL_POINT"}, {"kind": "ESTIMATE"},
    {"comparable": False}, {"enabled": False}, {"is_maximal_test": False},
    {"speed_kmh": float("nan")}, {"duration_s": -1},
    {"sport": "Ride"},
])
def test_unaccepted_tests_cannot_unlock_conflict_fallback(mutation):
    tests = [{**t, **mutation} for t in TESTS]
    profile = zone_profile.build(curve(), BOUNDS, 200, conflicting(), tests=tests)
    assert profile["status"] == "UNAVAILABLE"
    assert profile["reason"] == "NONMONOTONE_INDEX_BOUNDARIES"


@pytest.mark.parametrize("tests", [[], [{"accepted_test_count": 5}], [None],
    [{"duration_s": 10., "speed_kmh": 40., "maximal": True,
      "comparable": True, "source": "MANUAL"}]])
def test_empty_malformed_or_sprint_only_evidence_is_insufficient(tests):
    profile = zone_profile.build(curve(), BOUNDS, 200, conflicting(), tests=tests)
    assert profile["status"] == "UNAVAILABLE"


def test_valid_measured_boundaries_remain_exact_with_tests_present():
    indices = {"GENERAL": pair(6)}
    with_tests = zone_profile.build(curve(), BOUNDS, 200, indices, tests=TESTS)
    without_tests = zone_profile.build(curve(), BOUNDS, 200, indices)
    assert with_tests["status"] == "AVAILABLE"
    assert with_tests["bounds_kmh"] == without_tests["bounds_kmh"]
    assert with_tests["bounds_kmh"][1:5] == pytest.approx([100*h/200/6 for h in BOUNDS[1:5]])
    assert "excluded_profiles" not in with_tests


def test_valid_general_profile_keeps_priority_after_zonal_conflict():
    indices = {**conflicting(), "GENERAL": pair(6)}
    profile = zone_profile.build(curve(), BOUNDS, 200, indices, tests=TESTS)
    assert profile["status"] == "AVAILABLE"
    assert profile["reason"] == "CONFLICTING_ZONAL_INDICES"
    assert all(z["source"] == "GENERAL_CONFLICT_FALLBACK" for z in profile["zones"][:4])


def test_blend_conflict_fallback_uses_test_anchors_and_describe_propagates_evidence():
    valid_indices = {f"Z{i}": pair(6) for i in range(1, 5)}
    prior = preliminary_capacity.build(BOUNDS, 200, valid_indices)
    test_curve = curve()
    described = dosing_curve.describe(prior["curve"], test_curve, TESTS,
                                     BOUNDS, 200, conflicting(), prior["anchors"])
    assert described["status"] == "AVAILABLE"
    profile = described["speed_zones"]
    direct = zone_profile.build(test_curve, BOUNDS, 200, tests=TESTS)
    assert profile["bounds_kmh"] == pytest.approx(direct["bounds_kmh"])
    assert profile["v6_kmh"] == pytest.approx(test_curve.speed(360)*3.6)
    assert profile["basis"] == "TEST_CURVE_AFTER_PAIRED_PROFILE_CONFLICT"
    assert profile["capacity_basis"] == "COACH_INDEX_TEST_BLEND"
    assert all(z["source"] == "TEST_CURVE_CONFLICT_FALLBACK" for z in profile["zones"][:4])


@pytest.mark.parametrize("bounds,hrmax", [(None, 200), ([90, None], 200),
    ([90, "bad"], 200), (BOUNDS, "bad"), (BOUNDS, float("nan")),
    ((False, 144, 158, 178, 188, 200), 200)])
def test_malformed_hr_inputs_return_unavailable(bounds, hrmax):
    profile = zone_profile.build(curve(), bounds, hrmax, tests=TESTS)
    assert profile["status"] == "UNAVAILABLE"
    assert profile["reason"] == "HR_PROFILE_REQUIRED"
    json.dumps(profile, allow_nan=False)


def test_malformed_index_rows_are_not_measurements():
    profile = zone_profile.build(curve(), BOUNDS, 200,
                                 {"Z1": "bad", "GENERAL": 3}, tests=TESTS)
    assert profile["status"] == "AVAILABLE"
    assert all(z["source"] == "INITIAL_V50_V6_ESTIMATE" for z in profile["zones"][:4])


def test_conflict_fallback_does_not_reuse_an_index_prior_shape():
    tests = TESTS[:1]
    reference = speed_duration.with_ten_second_tail(speed_duration.calibrated(tests))
    # Even if the caller provides another available curve, accepted real tests
    # independently determine fallback anchors with the default reference shape.
    profile = zone_profile.build(curve(), BOUNDS, 200, conflicting(), tests=tests)
    direct = zone_profile.build(reference, BOUNDS, 200, tests=tests)
    assert profile["bounds_kmh"] == pytest.approx(direct["bounds_kmh"])
    assert profile["v6_kmh"] == pytest.approx(reference.speed(360)*3.6)


def test_conflicting_real_tests_remain_unavailable():
    tests = [TESTS[0], {**TESTS[0], "speed_kmh": 30}]
    profile = zone_profile.build(curve(), BOUNDS, 200, conflicting(), tests=tests)
    assert profile["status"] == "UNAVAILABLE"
    assert profile["fallback_reason"] == "INVALID_ACCEPTED_TEST_CURVE"
