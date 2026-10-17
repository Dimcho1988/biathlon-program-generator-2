from copy import deepcopy
import math

import pytest

from biathlon import dosing_curve, preliminary_capacity, speed_duration


BOUNDS = (100, 135, 155, 175, 190, 205)
INDICES = {f"Z{i+1}": {"index": v, "count": 5, "seconds": 2400}
           for i, v in enumerate((7., 7., 6.5, 6.))}
TESTS = [{"duration_s": t, "speed_kmh": v, "maximal": True, "test_mode": "STRICT",
          "enabled": True, "comparable": True, "sport": "Run", "day": "2026-09-30", "use_for_cs": True}
         for t, v in ((180., 23.), (720., 19.2))]


def example():
    prior = preliminary_capacity.build(BOUNDS, 205, INDICES, total_weekly_minutes=240)
    test_curve = speed_duration.calibrated(TESTS)
    described = dosing_curve.describe(prior["curve"], test_curve, TESTS, BOUNDS, 205, INDICES, prior["anchors"])
    return {"sport": "Run", "status": "CALIBRATED", "model_version": speed_duration.VERSION,
            "tests": [{"entry_key": str(i), "payload": deepcopy(t)} for i, t in enumerate(TESTS)],
            "active_test_keys": ["0", "1"], "active_test_count": 2,
            "index_window": {"last_activity_date": "2026-09-30"}, "index_summary": deepcopy(INDICES),
            "preliminary_capacity": preliminary_capacity.summary(prior), "dosing_model": described}


def test_exact_30_70_mean_preserves_both_sources_and_both_inverses():
    view = example()
    original = deepcopy(view)
    curve = dosing_curve.from_view(view)
    for i in range(301):
        t = math.exp(curve._x[0]+(curve._x[-1]-curve._x[0])*i/300)
        speed = curve.speed(t)
        assert speed == pytest.approx(.3*curve.index_curve.speed(t)+.7*curve.test_curve.speed(t), rel=1e-13)
        assert -1 < curve.log_slope(t) < 0
        if 0 < i < 300:
            numerical_slope = (math.log(curve.speed(t*math.exp(1e-5)))-math.log(curve.speed(t*math.exp(-1e-5))))/2e-5
            assert curve.log_slope(t) == pytest.approx(numerical_slope, abs=1e-5)
        assert curve.inverse(speed) == pytest.approx(t, rel=1e-8)
        assert curve.inverse(curve.distance(t), distance=True) == pytest.approx(t, rel=1e-8)
    for test in TESTS:
        assert curve.test_curve.speed(test["duration_s"])*3.6 == pytest.approx(test["speed_kmh"])
        assert curve.speed(test["duration_s"])*3.6 != pytest.approx(test["speed_kmh"])
    assert view == original
    with pytest.raises(ValueError):
        curve.speed(curve.times[-1]*1.01)


def test_hr_coordinates_are_preserved_instead_of_cancelling_the_blend():
    view = example()
    curve = dosing_curve.from_view(view)
    predictor = dosing_curve.Predictor(curve, BOUNDS, 205, INDICES,
        expert_durations={a["zone"]: a["duration_s"] for a in view["preliminary_capacity"]["anchors"]})
    for row in view["dosing_model"]["zone_comparison"]:
        hr, t = row["hr_bpm"], row["duration_s"]
        assert predictor.duration(hr) == pytest.approx(t)
        assert predictor.speed_for_hr(hr) == pytest.approx(.3*row["index_speed_kmh"]+.7*row["test_speed_kmh"])
        assert predictor.hr_for_speed(predictor.speed_for_hr(hr)) == pytest.approx(hr)
        assert row["source"] == "COACH_INDEX_TEST_BLEND"
    assert all(p["evidence"] == "COACH_BLEND_ESTIMATE" for p in view["dosing_model"]["points"])
    assert any(p["estimated_hr_bpm"] is None for p in view["dosing_model"]["points"])


def test_missing_or_nonmaximal_source_cannot_be_presented_as_a_blend():
    view = example()
    assert dosing_curve.describe(None, None, [], BOUNDS, 205, {}, [])['status'] == 'UNAVAILABLE'
    for mutation in ('missing', 'nonmaximal', 'generated', 'no_index', 'wrong_weights'):
        changed = deepcopy(view)
        if mutation == 'missing': changed.pop('dosing_model')
        if mutation == 'nonmaximal': changed['tests'][0]['payload']['maximal'] = False
        if mutation == 'generated': changed['tests'][0]['payload']['generated'] = True
        if mutation == 'no_index': changed['preliminary_capacity']['status'] = 'DURATION_ONLY'
        if mutation == 'wrong_weights': changed['dosing_model']['weights'] = {'index': .5, 'tests': .5}
        assert dosing_curve.from_view(changed) is None


def test_single_test_may_supply_its_existing_scaled_curve_without_inventing_cs():
    prior = preliminary_capacity.build(BOUNDS, 205, INDICES)
    test_curve = speed_duration.calibrated(TESTS[:1])
    dose = dosing_curve.describe(prior['curve'], test_curve, TESTS[:1], BOUNDS, 205, INDICES, prior['anchors'])
    assert dose['status'] == 'AVAILABLE' and dose['accepted_test_count'] == 1
    assert speed_duration.critical_speed(TESTS[:1])['status'] == 'INSUFFICIENT_TESTS'


def test_incompatible_hr_domain_is_unavailable_instead_of_breaking_the_speed_view():
    prior = preliminary_capacity.build(BOUNDS, 205, INDICES)
    dose = dosing_curve.describe(prior['curve'], speed_duration.calibrated(TESTS), TESTS,
                                BOUNDS, 90, INDICES, prior['anchors'])
    assert dose['status'] == 'UNAVAILABLE'
    assert dose['reason'] == 'INCOMPATIBLE_DURATION_OR_HR_DOMAIN'
