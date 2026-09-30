import math

import numpy as np
import pytest

from biathlon import speed_duration as model
from biathlon.speed_duration_5pct import TwoAnchorCurve, _soft_cap, _strict_polynomial_sign


TESTS = [{"duration_s": 180., "speed_kmh": 22.},
         {"duration_s": 720., "speed_kmh": 18.46}]


def test_specification_control_example_and_anchors():
    curve = model.calibrated(TESTS)
    assert curve.a == pytest.approx(-.13315163168842203, abs=1e-14)
    assert curve.c == pytest.approx(.0047617205194181, abs=1e-14)
    expected = [(10.8, 29.572768486590412), (180., 22.), (720., 18.46),
        (1800., 16.609783876511734), (3600., 15.951005284730869),
        (7200., 15.323595287121313), (21600., 12.068286648664060),
        (43200., 10.228164159778950)]
    for seconds, speed in expected:
        assert curve.speed(seconds)*3.6 == pytest.approx(speed, abs=1e-4)
    for item in TESTS:
        assert curve.speed(item["duration_s"])*3.6 == pytest.approx(item["speed_kmh"], abs=1e-8)
    assert model.calibrated(list(reversed(TESTS))) is curve
    assert model.model_metadata(curve)["additional_corridor_fraction"] == .05


def test_reduced_cached_grid_matches_reference_prototype():
    fast = model.calibrated(TESTS)
    dense = TwoAnchorCurve(model.Curve(model.REFERENCE_TIMES, model.REFERENCE_SPEEDS),
        tuple((t["duration_s"], t["speed_kmh"]/3.6) for t in TESTS), grid_size=24001)
    for seconds in np.geomspace(10.8, 43516., 701):
        assert fast.speed(seconds)*3.6 == pytest.approx(dense.speed(seconds)*3.6, abs=1e-4)


def test_correction_keeps_outward_direction_and_smooth_joins():
    curve = model.calibrated(TESTS)
    for direction, anchor, endpoint in [(-1, 180., 10.8), (1, 720., 43516.)]:
        times = np.geomspace(anchor, endpoint, 1001)
        corrections = np.array([curve.point_metadata(t)["additional_correction"] for t in times])
        sign = 1 if direction == -1 else -1
        assert np.min(np.diff(sign*corrections)) >= -1e-12
        assert max(abs(corrections)) <= .05+1e-12
        assert sign*corrections[-1] == pytest.approx(.05)
        assert curve.log_slope(anchor*(1-1e-8)) == pytest.approx(curve.log_slope(anchor*(1+1e-8)), abs=1e-7)
    assert curve.point_metadata(3600)["extrapolation_capped"]
    assert not curve.point_metadata(360)["extrapolated"]
    inputs = np.array([.04-1e-8, .04, .04+1e-8, .06-1e-8, .06, .06+1e-8])
    values = _soft_cap(inputs)
    assert np.all(np.diff(values) >= 0)
    assert values[-1] == .05
    assert (values[1]-values[0])/1e-8 == pytest.approx((values[2]-values[1])/1e-8, abs=1e-6)
    assert (values[4]-values[3])/1e-8 == pytest.approx((values[5]-values[4])/1e-8, abs=1e-6)


def test_curve_is_invertible_without_out_of_range_clamping():
    curve = model.calibrated(TESTS)
    for seconds in np.geomspace(10.8, 43516., 31):
        assert -1 < curve.log_slope(seconds) < 0
        assert curve.inverse(curve.speed(seconds)) == pytest.approx(seconds, rel=1e-10)
        assert curve.inverse(curve.distance(seconds), distance=True) == pytest.approx(seconds, rel=1e-10)
    for seconds in [0, -1, 10, 44000, float("nan"), float("inf"), True]:
        with pytest.raises(ValueError): curve.speed(seconds)
    with pytest.raises(ValueError): curve.inverse(curve.speed(10.8)*1.1)


def test_analytic_polynomial_check_catches_between_node_failure():
    # Endpoints positive but a narrow interior dip violates the constraint.
    polynomial = np.array([[.09, -1., 1., 0., 0.]])
    assert not _strict_polynomial_sign(polynomial, positive=True)
    assert _strict_polynomial_sign(np.array([[.3, -1., 1., 0., 0.]]), positive=True)


def test_physical_endpoint_order_does_not_hide_invalid_inner_shape():
    # Distances grow and speeds fall at the tests, yet fitted end slope > 0.
    with pytest.raises(ValueError, match="TWO_ANCHOR_NONPHYSICAL_TEST_WINDOW"):
        model.calibrated([TESTS[0], {"duration_s": 720., "speed_kmh": 21.98}])
    for tests in [[TESTS[0], TESTS[0]], [TESTS[0], {"duration_s":180.,"speed_kmh":21.}],
                  [TESTS[0], {"duration_s":720.,"speed_kmh":23.}]]:
        with pytest.raises(ValueError): model.calibrated(tests)


def test_reference_prior_and_one_anchor_preserve_shape_without_five_percent_warp():
    reference = model.calibrated([])
    prior = model.Curve(model.REFERENCE_TIMES,
        tuple(v*r for v, r in zip(model.REFERENCE_SPEEDS, [.75, .74, .73, .72, .70, .65])))
    assert model.calibrated([], prior=prior) is prior
    curve = model.calibrated(TESTS[:1], prior=prior)
    ratio = (22/3.6)/reference.speed(180)
    for seconds in (10.8, 60, 180, 1800, 7200, 43516):
        assert reference.speed(seconds) == pytest.approx(model.Curve(model.REFERENCE_TIMES, model.REFERENCE_SPEEDS).speed(seconds))
        assert curve.speed(seconds) == pytest.approx(reference.speed(seconds)*ratio)
        assert curve.log_slope(seconds) == reference.log_slope(seconds)
        assert curve.speed(seconds) == model.calibrated(TESTS[:1]).speed(seconds)
    assert model.calibrated(TESTS, prior=prior) is model.calibrated(TESTS)


def test_two_anchor_curve_ignores_later_zone_warp_and_multi_keeps_every_test():
    curve = model.calibrated(TESTS)
    changed, factor = model.adjusted(curve, TESTS, None, [], [.1]*5,
                                    duration_centers=[12600,8100,3300,1200,900])
    assert changed is curve and factor == 0.
    extra = {"duration_s": 3600., "speed_kmh": 16.}
    multiple = model.calibrated([*TESTS, extra])
    assert model.model_metadata(multiple)["calibration_mode"] == "MULTIPOINT_C1_5PCT"
    for item in [*TESTS, extra]:
        assert multiple.speed(item["duration_s"])*3.6 == pytest.approx(item["speed_kmh"], abs=1e-8)


def test_domain_edge_test_has_no_empty_side_interpolator():
    reference = model.calibrated([])
    tests = [{"duration_s": 10.8, "speed_kmh": reference.speed(10.8)*3.6},
             {"duration_s": 60., "speed_kmh": reference.speed(60.)*3.6}]
    curve = model.calibrated(tests)
    assert curve.left.spline is None
    assert curve.speed(10.8) == pytest.approx(reference.speed(10.8))


def test_opposite_trends_and_neutral_slopes_are_determined_from_the_tests():
    opposite = model.calibrated([TESTS[0], {"duration_s":720., "speed_kmh":20.}])
    assert opposite.left.sign == -1 and opposite.right.sign == 1
    for anchor, endpoint, sign in [(180., 10.8, -1), (720., 43516., 1)]:
        correction = [opposite.point_metadata(t)["additional_correction"] for t in np.geomspace(anchor, endpoint, 101)]
        assert np.min(np.diff(sign*np.array(correction))) >= -1e-12
        assert max(abs(v) for v in correction) <= .05+1e-12
    reference = model.calibrated([])
    # Both observations fall inside one exact constant-log-slope interval.
    neutral = model.calibrated([{"duration_s": t, "speed_kmh": reference.speed(t)*3.6}
                                for t in (300., 600.)])
    assert neutral.left.sign == neutral.right.sign == 0
    for seconds in (10.8, 60., 300., 600., 3600., 43516.):
        assert neutral.speed(seconds) == pytest.approx(reference.speed(seconds), rel=1e-12)
