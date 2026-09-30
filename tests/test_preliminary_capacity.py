import copy
import json
import math

import pytest

from biathlon import preliminary_capacity as prior
from biathlon.hr_speed import Predictor, TMAX_RANGES_S
from biathlon.load_progression import WEEKLY_Q_BOUNDS, total_volume_position
from biathlon.speed_duration import Curve, REFERENCE_TIMES, REFERENCE_SPEEDS, calibrated

BOUNDS = [80, 137, 148, 160, 170, 180]
REFERENCE = Curve(REFERENCE_TIMES, REFERENCE_SPEEDS)


def paired_indices(durations=None, ratios=None):
    durations = durations or {z: sum(limits)/2 for z, limits in TMAX_RANGES_S.items()}
    ratios = ratios or {z: .7 for z in TMAX_RANGES_S}
    return {z: {"index": 100*BOUNDS[i+1]/BOUNDS[-1]/(REFERENCE.speed(durations[z])*3.6*ratios[z]),
                "count": 3, "seconds": 900}
            for i, z in enumerate(TMAX_RANGES_S)}


def test_weekly_q_only_positions_separate_expert_time_bounds():
    q = {z: sum(limits)/2 for z, limits in WEEKLY_Q_BOUNDS.items()}
    result = prior.build(BOUNDS, 180, {}, zone_weekly_q=q)
    assert result["curve"] is None
    # Equal numerical midpoints (e.g. Z4) do not make Q a maximum duration.
    assert any(a["duration_s"] != a["measured_weekly_q"]*60 for a in result["anchors"])
    for anchor in result["anchors"]:
        assert anchor["duration_position"] == .5
        assert anchor["duration_s"] == sum(TMAX_RANGES_S[anchor["zone"]])/2
        assert anchor["measured_weekly_q"] == q[anchor["zone"]]
        assert anchor["duration_source"] == "MEASURED_ZONE_Q_POSITION"
        assert anchor["kind"] == "ESTIMATE" and not anchor["is_maximal_test"]
    assert result["duration_is_training_dose"] is False


def test_total_volume_estimates_times_without_fabricating_zone_distribution_or_speeds():
    result = prior.build(None, None, {}, total_weekly_minutes=12*60)
    assert total_volume_position(12*60) == .5
    assert result["status"] == "DURATION_ONLY" and result["curve"] is None
    assert [a["duration_s"] for a in result["anchors"]] == [15300, 9900, 3150, 1500]
    assert all(a["measured_weekly_q"] is None and a["speed_kmh"] is None for a in result["anchors"])
    assert all(a["duration_source"] == "TOTAL_VOLUME_ESTIMATE" for a in result["anchors"])
    assert not result["is_measured_zone_distribution"]
    assert result["z5_duration_source"] == "Z4_SHARED_BOUNDARY"


def test_missing_volume_selects_expert_minimum_and_zero_is_distinct_from_unknown():
    result = prior.build(BOUNDS, 180, {}, zone_weekly_q={"Z2": 0})
    assert [a["duration_s"] for a in result["anchors"]] == [12600, 9000, 2700, 1200]
    assert result["anchors"][0]["duration_source"] == "EXPERT_MINIMUM"
    assert result["anchors"][0]["measured_weekly_q"] is None
    assert result["anchors"][1]["duration_source"] == "MEASURED_ZONE_Q_POSITION"
    assert result["anchors"][1]["measured_weekly_q"] == 0


def test_one_valid_index_gives_an_estimated_global_scale_not_a_real_test():
    indices = {"Z3": paired_indices()["Z3"]}
    result = prior.build(BOUNDS, 180, indices, total_weekly_minutes=720)
    curve = result["curve"]
    assert result["status"] == "PRELIMINARY" and result["speed_anchor_count"] == 1
    assert result["real_test_count"] == 0
    assert curve.calibration_mode == "PRELIMINARY_HR_HISTORY"
    for t in [10.8, 25, 180, 670, 3300, 12000, 43516]:
        assert curve.speed(t) == pytest.approx(.7*REFERENCE.speed(t), rel=1e-12)
        assert curve.log_slope(t) == pytest.approx(REFERENCE.log_slope(t), abs=1e-12)


def test_multiple_estimates_change_shape_with_exact_estimated_points_and_physical_inverses():
    ratios = dict(zip(TMAX_RANGES_S, [.67, .68, .70, .71]))
    result = prior.build(BOUNDS, 180, paired_indices(ratios=ratios), total_weekly_minutes=720)
    curve = result["curve"]
    assert curve is not None
    for a in result["anchors"]:
        assert curve.speed(a["duration_s"])*3.6 == pytest.approx(a["speed_kmh"], abs=1e-10)
    for i in range(201):
        t = math.exp(curve._x[0] + (curve._x[-1]-curve._x[0])*i/200)
        assert -1 < curve.log_slope(t) < 0
        assert curve.inverse(curve.speed(t)) == pytest.approx(t, rel=1e-10)
        assert curve.inverse(curve.distance(t), distance=True) == pytest.approx(t, rel=1e-10)


def test_bad_or_model_derived_indices_cannot_create_absolute_capacity():
    good = paired_indices()["Z1"]
    for patch in [{"count": 0}, {"seconds": 0}, {"index": None}, {"valid": False},
                  {"source": "MODEL"}, {"is_estimated": True}, {"generated": True},
                  {"index": float("nan")}, {"index": 1e-320}]:
        result = prior.build(BOUNDS, 180, {"Z1": {**good, **patch}}, total_weekly_minutes=720)
        assert result["curve"] is None
        assert result["anchors"][0]["speed_kmh"] is None
    assert prior.build(BOUNDS, 180, {"Z1": {"index": 5}})["curve"] is None


def test_conflicting_estimates_remain_visible_without_reordering_or_moving_them(monkeypatch):
    indices = paired_indices()
    indices["Z1"]["index"] /= 2
    original = copy.deepcopy(indices)
    result = prior.build(BOUNDS, 180, indices, total_weekly_minutes=720)
    assert result["curve"] is None
    assert "CONFLICTING_ESTIMATED_SPEED_DURATION_ANCHORS" in result["warnings"]
    assert result["anchors"][0]["speed_kmh"] > result["anchors"][1]["speed_kmh"]
    assert indices == original
    # Keep defensive coverage if future expert ranges overlap again.
    monkeypatch.setitem(TMAX_RANGES_S,"Z1",(7200.,18000.))
    result = prior.build(BOUNDS, 180, paired_indices(), zone_weekly_q={"Z1": 240, "Z2": 300})
    assert result["curve"] is None
    assert result["conflicting_zones"] == [["Z1", "Z2"]]
    assert [a["duration_s"] for a in result["anchors"][:2]] == [7200, 10800]


def test_explicit_coach_positions_override_history_and_outliers_remain_measured():
    result = prior.build(BOUNDS, 180, {}, zone_weekly_q={"Z1": 9000}, total_weekly_minutes=720,
                         positions={"Z2": .2}, position_overrides={"Z3": .9})
    assert result["anchors"][0]["duration_position"] == 1
    assert result["anchors"][0]["measured_weekly_q"] == 9000
    assert result["anchors"][1]["duration_position"] == .2
    assert result["anchors"][2]["duration_source"] == "COACH_POSITION"
    assert result["anchors"][2]["duration_position"] == .9
    with pytest.raises(ValueError):
        prior.build(BOUNDS, 180, {}, positions={"Z1": 1.2})
    with pytest.raises(ValueError):
        prior.build(BOUNDS, 180, {}, zone_weekly_q={"Z1": -1})


def test_saved_summary_rebuilds_identical_prior_without_promoting_estimates_to_tests():
    result = prior.build(BOUNDS, 180, paired_indices(ratios=dict(zip(TMAX_RANGES_S, [.67, .68, .70, .71]))),
                         total_weekly_minutes=720)
    payload = json.loads(json.dumps(prior.summary(result)))
    assert "curve" not in payload
    restored = prior.curve_from_summary(payload)
    for i in range(71):
        t = math.exp(restored._x[0] + (restored._x[-1]-restored._x[0])*i/70)
        assert restored.speed(t) == result["curve"].speed(t)
        assert restored.log_slope(t) == result["curve"].log_slope(t)
    payload["anchors"][0]["is_maximal_test"] = True
    with pytest.raises(ValueError, match="provenance"):
        prior.curve_from_summary(payload)
    assert prior.curve_from_summary(prior.summary(prior.build(None, None, {}))) is None


def test_one_real_test_scales_prior_exactly_and_diagnostics_do_not_average_anchors():
    result = prior.build(BOUNDS, 180, paired_indices(ratios=dict(zip(TMAX_RANGES_S, [.67, .68, .70, .71]))),
                         total_weekly_minutes=720)
    test = {"duration_s": 600., "speed_kmh": 20.}
    calibrated_curve = calibrated([test], prior=result["curve"])
    factor = 20/(result["curve"].speed(600)*3.6)
    assert calibrated_curve.speed(600)*3.6 == pytest.approx(20, abs=1e-12)
    for t in [180, 600, 3000, 15000]:
        assert calibrated_curve.speed(t) == pytest.approx(result["curve"].speed(t)*factor, rel=1e-12)
    diagnostic = prior.calibration_diagnostics(result, [test])
    assert diagnostic["residuals"][0]["measured_vs_prior_percent"] == pytest.approx(100*(factor-1))
    assert diagnostic["real_test_anchors_take_precedence"]
    assert diagnostic["prior_shape_used"]
    assert diagnostic["blend_status"] == "NOT_APPLIED"
    assert not diagnostic["sampling_counts_are_confidence_weights"]


def test_hr_fallback_uses_same_positioned_duration_and_curve_in_both_directions():
    result = prior.build(BOUNDS, 180, {}, total_weekly_minutes=600)
    durations = {a["zone"]: a["duration_s"] for a in result["anchors"]}
    curve = calibrated([{"duration_s": 1200, "speed_kmh": 22}])
    predictor = Predictor(curve, BOUNDS, 180, {}, expert_durations=durations)
    assert predictor.times == tuple(durations.values())
    assert all(a["source"] == "EXPERT_HISTORY" for a in predictor.anchors)
    for i, anchor in enumerate(predictor.anchors):
        assert predictor.duration(BOUNDS[i+1]) == pytest.approx(anchor["duration_s"])
        assert predictor.hr_for_speed(anchor["speed_kmh"]) == pytest.approx(BOUNDS[i+1])
    assert predictor.metadata(155)["hr_prediction_source"] == "EXPERT_HISTORY"
    assert curve.speed(1200)*3.6 == pytest.approx(22)


def test_hr_conflicting_positions_fall_back_to_minima_and_preserve_diagnostics(monkeypatch):
    monkeypatch.setitem(TMAX_RANGES_S,"Z1",(7200.,18000.))
    monkeypatch.setitem(TMAX_RANGES_S,"Z2",(5400.,10800.))
    curve = calibrated([{"duration_s": 1200, "speed_kmh": 22}])
    predictor = Predictor(curve, BOUNDS, 180, {}, expert_durations={"Z1": 7200, "Z2": 10800})
    assert predictor.times == (7200, 5400, 2700, 1200)
    assert all(a["source"] == "EXPERT_MINIMUM" for a in predictor.anchors)
    assert predictor.summary()["expert_duration_conflicts"] == [["Z1", "Z2"]]
    assert all(a["reason"] == "CONFLICTING_EXPERT_DURATION_ESTIMATES" for a in predictor.anchors)
    with pytest.raises(ValueError, match="within"):
        Predictor(curve, BOUNDS, 180, {}, expert_durations={"Z1": 500})
