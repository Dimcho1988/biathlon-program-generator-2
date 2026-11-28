"""All measured anchors and both outward continuations form one physical model."""
import math

import numpy as np
import pytest

from biathlon import speed_duration as model
from biathlon.speed_duration_5pct import MultipointCurve


def measurements(points):
    return [{"duration_s": float(t), "speed_kmh": float(v)} for t, v in points]


THREE = [(180, 22), (720, 18.46), (3600, 16)]
CASES = [THREE, [(180, 22), (720, 20), (3600, 19.5)],
         [(60, 27), (180, 22), (720, 18.46), (3600, 16)],
         [(60, 27), (180, 22), (360, 20), (720, 18.46), (3600, 16)]]


@pytest.mark.parametrize("points", CASES)
def test_all_real_points_exact_with_physical_smooth_invertible_curve(points):
    observations = measurements(points)
    curve = model.calibrated(observations)
    assert model.calibrated(list(reversed(observations))) is curve
    assert model.model_metadata(curve)["additional_corridor_fraction"] == .05
    for t, v in points:
        assert curve.speed(t)*3.6 == pytest.approx(v, abs=1e-10)
        assert curve.inverse(v/3.6) == pytest.approx(t, rel=1e-10)
        assert not curve.point_metadata(t)["extrapolated"]
    ts = np.geomspace(*[model.REFERENCE_TIMES[i] for i in (0, -1)], 1501)
    assert np.all(np.diff([curve.speed(t) for t in ts]) < 0)
    assert np.all(np.diff([curve.distance(t) for t in ts]) > 0)
    assert all(-1 < curve.log_slope(t) < 0 for t in ts)
    for t in ts[::150]:
        assert curve.inverse(curve.speed(t)) == pytest.approx(t, rel=1e-10)
        assert curve.inverse(curve.distance(t), distance=True) == pytest.approx(t, rel=1e-10)
    joins = [math.log(t) for t, _ in points]
    for i, (eta, *_rest) in enumerate(curve.window._pieces):
        a, b = curve.window._x[i:i+2]
        joins.extend([a+eta*(b-a), b-eta*(b-a)])
    for x in joins:
        assert curve.log_slope(math.exp(x-1e-8)) == pytest.approx(
            curve.log_slope(math.exp(x+1e-8)), abs=1e-7)
    for side, anchor, edge in [(curve.left, curve.t1, ts[0]), (curve.right, curve.t2, ts[-1])]:
        correction = np.array([curve.point_metadata(t)["additional_correction"]
                               for t in np.geomspace(anchor, edge, 1501)])
        assert correction[0] == pytest.approx(0, abs=1e-12)
        assert np.min(np.diff(side.sign*correction)) >= -1e-12
        assert np.min(side.sign*correction) >= -1e-12
        assert np.max(abs(correction)) <= .05+1e-12
    for t in [10, 44000, True, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            curve.speed(t)


def test_interpolation_uses_all_measurements_only_and_continues_its_terminal_pieces():
    curve = model.calibrated(measurements(THREE))
    measured = model.Curve(tuple(t for t, _ in THREE), tuple(v/3.6 for _, v in THREE))
    for t in np.geomspace(curve.t1, curve.t2, 301):
        assert curve.speed(t) == pytest.approx(measured.speed(t), abs=1e-12)
    for side, anchor in [(-1, curve.t1), (1, curve.t2)]:
        # Same terminal quadratic on either side of the anchor, until limiting
        # is needed. The extrapolation cannot be a replacement normative tail.
        inner, outer = anchor*math.exp(-side*.01), anchor*math.exp(side*.01)
        raw = np.exp(curve.continuation_log_speed(np.array([inner, outer]), side))
        assert raw[0] == pytest.approx(curve.speed(inner), rel=1e-12)
        assert raw[1] == pytest.approx(curve.speed(outer), rel=1e-7)
        assert curve.speed(outer) != pytest.approx(curve.base_speed(outer), rel=1e-5)


def test_interior_measurement_changes_shape_and_continuation_with_extremes_fixed():
    original = model.calibrated(measurements(THREE))
    changed = model.calibrated(measurements([(180, 22), (720, 21), (3600, 16)]))
    assert original.speed(180) == changed.speed(180)
    assert original.speed(3600) == changed.speed(3600)
    assert original.speed(720) != changed.speed(720)
    assert original.left.sign == 1 and changed.left.sign == -1
    for t in (120, 1200, 5000):
        assert original.speed(t) != pytest.approx(changed.speed(t), rel=1e-4)
    assert changed.speed(720)/changed.base_speed(720) == pytest.approx(1)


def test_raw_reversal_is_held_without_forcing_every_tail_to_five_percent():
    curve = model.calibrated(measurements(THREE))
    assert curve.right.sign == -1
    end = curve.times[-1]
    raw_at_end = math.exp(float(curve.continuation_log_speed(np.array([end]), 1)[0]))
    assert raw_at_end > curve.base_speed(end)
    correction = curve.point_metadata(end)["additional_correction"]
    assert -.04 < correction < -.02
    assert not curve.point_metadata(end)["extrapolation_capped"]
    # The held relative decrease is stable while the absolute speed still falls.
    assert curve.point_metadata(21600)["additional_correction"] == pytest.approx(correction, abs=1e-12)
    assert curve.speed(end) < curve.speed(21600)
    assert curve.left.sign == 1 and curve.point_metadata(10.8)["extrapolation_capped"]


def test_opposite_tail_directions_and_neutral_tangent_with_real_curvature():
    opposite = model.calibrated(measurements(CASES[1]))
    assert opposite.left.sign == -1 and opposite.right.sign == 1
    reference = model.calibrated([])
    slope = reference.log_slope(180)
    v2 = 22*(720/180)**slope
    curve = model.calibrated(measurements([(180, 22), (720, v2), (3600, v2*.9)]))
    assert curve.raw_log_slope(180) == pytest.approx(slope, abs=1e-12)
    assert curve.left.sign != 0
    assert curve.log_slope(180*(1-1e-8)) == pytest.approx(slope, abs=1e-7)
    corrections = [curve.left.sign*curve.point_metadata(t)["additional_correction"]
                   for t in np.geomspace(180, 10.8, 301)]
    assert min(np.diff(corrections)) >= -1e-12 and max(corrections) > .001


def test_domain_edges_and_neutral_locally_identical_pieces():
    reference = model.calibrated([])
    edges = model.calibrated(measurements([(t, reference.speed(t)*3.6*.7) for t in (10.8, 180, 43516)]))
    assert edges.left.spline is edges.right.spline is None
    # All three points lie on one constant-log-slope normative piece.
    neutral = model.calibrated(measurements([(t, reference.speed(t)*3.6*.7) for t in (300, 450, 600)]))
    assert neutral.left.sign == neutral.right.sign == 0
    for t in (10.8, 180, 300, 450, 600, 7200, 43516):
        assert neutral.speed(t) == pytest.approx(reference.speed(t)*.7, rel=1e-12)


def test_grid_convergence_and_no_history_warp_after_any_real_calibration():
    curve = model.calibrated(measurements(THREE))
    dense = MultipointCurve(model.calibrated([]), tuple((t, v/3.6) for t, v in THREE), grid_size=24001)
    for t in np.geomspace(10.8, 43516, 401):
        assert curve.speed(t)*3.6 == pytest.approx(dense.speed(t)*3.6, abs=1e-4)
    for points in [THREE[:1], THREE[:2], THREE]:
        observations = measurements(points)
        calibrated = model.calibrated(observations)
        changed, strength = model.adjusted(calibrated, observations, None, [], [.1]*5,
            duration_centers=[15300, 9900, 3150, 1500, 900])
        assert changed is calibrated and strength == 0


@pytest.mark.parametrize("points", [THREE+[THREE[1]], [(180, 22), (720, 23), (3600, 16)],
                                  [(180, 22), (720, 5), (3600, 4)]])
def test_invalid_intermediate_measurements_are_not_silently_dropped(points):
    with pytest.raises(ValueError):
        model.calibrated(measurements(points))
