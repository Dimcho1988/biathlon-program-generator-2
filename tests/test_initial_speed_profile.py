from copy import deepcopy
import json
import math

import pytest

from biathlon import hr_speed, initial_speed_profile as initial, initial_threshold, speed_duration, zone_profile


HRMAX = 190.
BOUNDS = tuple(HRMAX*p/100 for p in zone_profile.HR_PERCENTAGES)


def estimate():
    return initial_threshold.estimate(8, 12, 4)


def result(sport="Run"):
    return initial.build(estimate(), BOUNDS, HRMAX, sport=sport)


def view():
    built = result()
    return {"status": "INITIAL_HEURISTIC", "speed_role": "RAW_MOVING_SPEED",
            "initial_threshold": {**estimate(), "used_for_zones": True, "use_status": "ZONES"},
            "initial_speed_profile": initial.summary(built), "speed_zones": built["speed_zones"]}


def test_control_estimate_becomes_only_a_conditional_v50_anchor_without_mutation():
    observed = estimate()
    before = deepcopy(observed)
    built = initial.build(observed, BOUNDS, HRMAX)
    assert built["status"] == "AVAILABLE"
    assert observed == before
    assert observed["p_percent"] == pytest.approx(80.5476, abs=.0001)
    assert built["curve"].speed(3000)*3.6 == pytest.approx(14.8980, abs=.0001)
    assert built["conditional_v50"] == {
        "duration_s": 3000., "speed_kmh": observed["v_threshold_estimated_kmh"],
        "role": "CONDITIONAL_THRESHOLD_AS_V50", "is_measured": False}
    assert built["speed_zones"]["v50_kmh"] == pytest.approx(observed["v_threshold_estimated_kmh"])


def test_uniform_scale_preserves_normative_shape_and_existing_ten_second_tail():
    curve = result()["curve"]
    normative = speed_duration.with_ten_second_tail(
        speed_duration.Curve(speed_duration.REFERENCE_TIMES, speed_duration.REFERENCE_SPEEDS))
    for duration in (10., 10.4, 10.8, 60., 180., 360., 1200., 3000., 7200., 43516.):
        assert curve.speed(duration)/curve.speed(3000) == pytest.approx(normative.speed(duration)/normative.speed(3000))
        assert curve.log_slope(duration) == pytest.approx(normative.log_slope(duration))
        assert curve.inverse(curve.speed(duration)) == pytest.approx(duration)
        assert curve.inverse(curve.distance(duration), distance=True) == pytest.approx(duration)
    assert curve.times[0] == 10
    with pytest.raises(ValueError):
        curve.speed(9.99)
    with pytest.raises(ValueError):
        curve.speed(43517)


@pytest.mark.parametrize("sport", ["Run", "NordicSki", "Ride"])
def test_zone_values_reuse_existing_policy_and_preserve_raw_estimated_provenance(sport):
    built = result(sport)
    curve, profile = built["curve"], built["speed_zones"]
    original_policy = zone_profile.build(curve, BOUNDS, HRMAX, {}, sport=sport, tests=())
    assert profile["bounds_kmh"] == pytest.approx(original_policy["bounds_kmh"])
    assert profile["version"] == zone_profile.VERSION
    assert profile["zone_policy_version"] == initial.ZONE_POLICY_VERSION
    assert 0 < profile["v50_kmh"] < profile["v6_kmh"] < profile["v10"]["speed_kmh"]
    assert profile["v6_kmh"] == pytest.approx(curve.speed(360)*3.6)
    assert profile["v10"]["speed_kmh"] == pytest.approx(curve.speed(10)*3.6)
    assert len(profile["zones"]) == 5
    assert all(a < b for a, b in zip(profile["bounds_kmh"], profile["bounds_kmh"][1:]))
    for item in [built, profile, profile["v10"], *profile["zones"]]:
        assert item["is_estimated"] is True
        assert item["calibration_eligible"] is False
        assert item["speed_role"] == "RAW_MOVING_SPEED"
    assert profile["v10"]["evidence"] == "ESTIMATED"
    assert built["accepted_test_count"] == profile["accepted_test_count"] == 0


def test_no_generated_tests_enter_calibration_and_every_curve_point_stays_estimated(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Heuristic scaling must not call measured-test calibration")
    monkeypatch.setattr(speed_duration, "calibrated", forbidden)
    curve = result()["curve"]
    for duration in (10., 360., 3000., 7200.):
        meta = curve.point_metadata(duration)
        assert meta["evidence"] == "INITIAL_HEURISTIC_ESTIMATE"
        assert meta["is_estimated"] is True
        assert meta["within_test_window"] is False
        assert meta["calibration_eligible"] is False
    assert speed_duration.model_metadata(curve)["calibration_mode"] == "HEURISTIC_REFERENCE_SCALE"
    assert speed_duration.model_metadata(curve)["model_version"] == "heuristic_v0.1_unvalidated"


def test_zonal_predictor_accepts_same_profile_and_keeps_boundary_provenance():
    built = result()
    predictor = hr_speed.ZonalPredictor(built["curve"], BOUNDS, HRMAX, {}, profile=built["speed_zones"])
    assert predictor.profile == built["speed_zones"]
    for hr in BOUNDS:
        assert predictor.hr_for_speed(predictor.speed_for_hr(hr)) == pytest.approx(hr)
    assert predictor.metadata(150)["hr_prediction_source"] == initial.SOURCE


@pytest.mark.parametrize("anchor", ["TESTS", "INDEX"])
def test_caller_validated_individual_anchor_has_priority(anchor):
    built = initial.build(estimate(), BOUNDS, HRMAX, reliable_anchor=anchor)
    assert built["status"] == "SUPERSEDED"
    assert built["curve"] is built["speed_zones"] is None


@pytest.mark.parametrize("change", [
    {"status": "UNAVAILABLE"}, {"use_status": "SUPERSEDED"},
    {"model_version": "future-model"}, {"speed_role": "FLAT_EQUIVALENT"},
    {"is_estimated": False}, {"calibration_eligible": True},
    {"v_threshold_estimated_kmh": None}, {"v_threshold_estimated_kmh": math.nan},
    {"v_threshold_estimated_kmh": 0}, {"v_threshold_estimated_kmh": True},
])
def test_ineligible_estimate_never_builds_a_curve(change):
    built = initial.build({**estimate(), **change}, BOUNDS, HRMAX)
    assert built["status"] != "AVAILABLE"
    assert built["curve"] is built["speed_zones"] is None


def test_missing_hr_profile_keeps_zones_unavailable_without_invented_bounds():
    built = initial.build(estimate(), None, None)
    assert built["reason"] == "HR_PROFILE_REQUIRED"
    assert built["curve"] is built["speed_zones"] is None


def test_serialized_view_reconstructs_identical_scale_without_runtime_objects():
    serialized = json.loads(json.dumps(view(), allow_nan=False))
    curve = initial.curve_from_view(serialized)
    assert curve is not None
    assert curve.speed(3000)*3.6 == pytest.approx(estimate()["v_threshold_estimated_kmh"])
    assert curve.speed(360) == pytest.approx(result()["curve"].speed(360))
    assert "curve" not in serialized["initial_speed_profile"]
    assert "speed_zones" not in serialized["initial_speed_profile"]


@pytest.mark.parametrize("path,value", [
    (("status",), "CALIBRATED"), (("speed_role",), "FLAT_EQUIVALENT"),
    (("initial_threshold", "used_for_zones"), False), (("initial_threshold", "use_status"), "THRESHOLD_ONLY"),
    (("initial_speed_profile", "status"), "UNAVAILABLE"),
    (("initial_speed_profile", "zone_policy_version"), "old"),
    (("initial_speed_profile", "calibration_eligible"), True),
    (("initial_speed_profile", "normative_version"), "unknown"),
    (("initial_speed_profile", "accepted_test_count"), 1),
    (("initial_speed_profile", "conditional_v50", "is_measured"), True),
    (("initial_speed_profile", "conditional_v50", "speed_kmh"), 99),
])
def test_curve_reconstruction_rejects_inactive_wrong_frame_or_changed_provenance(path, value):
    model = view()
    target = model
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert initial.curve_from_view(model) is None
