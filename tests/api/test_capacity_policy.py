"""Observed-only capacity policy excludes sparse and extrapolated curve doses."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from apps.api import training_plan_engine as engine
from biathlon import dosing_curve, preliminary_capacity, speed_duration
from tests.api.test_method_curve_capacity import SETTINGS, metabolic_method
from tests.test_dosing_curve import BOUNDS, INDICES, example


TODAY = date(2026, 10, 8)
CASES = [
    ([1050.], False, False),
    ([1200., 7200.], False, False),
    ([1050.], True, True),
    ([1200., 7200.], True, True),
    ([180., 720.], False, True),
]


def view_at(durations, *, blended=False):
    base = speed_duration.calibrated([{"duration_s": 1050., "speed_kmh": 20.571428571428573}])
    tests = [{"duration_s": t, "speed_kmh": base.speed(t)*3.6, "maximal": True,
              "test_mode": "STRICT", "day": TODAY.isoformat()} for t in durations]
    speed = {"status": "CALIBRATED", "sport": "NordicSki", "model_version": speed_duration.VERSION,
             "active_test_keys": [str(i) for i in range(len(tests))],
             "tests": [{"entry_key": str(i), "payload": t} for i, t in enumerate(tests)],
             "index_window": {"last_activity_date": TODAY.isoformat()}, "index_summary": {}}
    settings = SETTINGS
    if blended:
        speed = {**example(), **speed, "sport": "Run", "index_summary": deepcopy(INDICES)}
        settings = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=205)
        prior = preliminary_capacity.curve_from_summary(speed["preliminary_capacity"])
        speed["dosing_model"] = dosing_curve.describe(prior, speed_duration.calibrated(tests), tests,
            BOUNDS, 205, INDICES, speed["preliminary_capacity"]["anchors"])
    return speed, settings, engine._capacity_context(speed, settings)


@pytest.mark.parametrize("durations,use_model_prior,allowed", CASES)
@pytest.mark.parametrize("kind", ["Z5", "RACE", "METABOLIC"])
def test_curve_policy_requires_two_observed_durations_unless_model_estimates_selected(
        durations, use_model_prior, allowed, kind):
    speed, settings, context = view_at(durations)
    target = context[0].curve.speed(600)*3.6
    if kind == "METABOLIC":
        method = metabolic_method(target)  # Old assessment cannot mask a rejected curve.
    else:
        method = {"zone": "Z5", "structure": "MODEL_INTERVALS", "sports": ("NordicSki",),
                  "interval_template": {"work_seconds": 30}, "instructions": "Repeatable effort",
                  "implementation_profile": "policy-regression"}
        if kind == "RACE":
            method["race_specific"] = {"zone": "Z5", "speed_kmh": target, "maximum_duration_s": 600.}
    evidence = engine.capacity_for(method, settings, speed, context, TODAY, use_model_prior=use_model_prior)
    if not allowed:
        assert evidence is None
        return
    assert evidence["capacity_source"] in {"SPEED_DURATION", "SPEED_DURATION_MODEL_CURVE", "SPEED_DURATION_TEST_ANCHOR", "RACE_SPEED_DURATION"}
    assert evidence["capacity_minutes"] > 0
    assert evidence["target_speed_kmh"] == pytest.approx(
        context[0].curve.speed(evidence["capacity_minutes"]*60)*3.6)
    if not use_model_prior:
        assert min(durations) <= evidence["capacity_minutes"]*60 <= max(durations)


@pytest.mark.parametrize("durations", [[1050.], [1200., 7200.]])
def test_expert_effort_assessment_is_used_only_without_an_individual_curve(durations):
    speed, settings, context = view_at(durations)
    target = context[0].curve.speed(600)*3.6
    method = metabolic_method(target, assessed_on=TODAY.isoformat())
    evidence = engine.capacity_for(method, settings, speed, context, TODAY, use_model_prior=False)
    assert evidence is None  # Existing curve is outside the selected evidence policy.
    evidence = engine.capacity_for(method, settings, None, (None, [], []), TODAY, use_model_prior=False)
    assert evidence["capacity_source"] == "COACH_EFFORT_CAPACITY"
    assert evidence["capacity_reference"] == "INDIVIDUAL_COACH_ASSESSMENT"
    assert evidence["capacity_minutes"] == 20.
    assert evidence["target_speed_kmh"] == target
    assert evidence["supported_test_duration_s"] is None
    assert not evidence["within_observed_test_window"]


@pytest.mark.parametrize("durations,use_model_prior,allowed", [
    ([1050.], False, False), ([180., 720.], False, False),
    ([1050.], True, True), ([180., 720.], True, True),
    ([180., 7200.], False, True),
])
def test_continuous_blend_honors_same_observed_only_policy(durations, use_model_prior, allowed):
    speed, settings, context = view_at(durations, blended=True)
    method = {"zone": "Z3", "structure": "CONTINUOUS", "position": .75, "actual_sport": "Run"}
    evidence = engine.capacity_for(method, settings, speed, context, TODAY, use_model_prior=use_model_prior)
    if allowed:
        assert evidence is not None
        assert evidence["capacity_source"] == "BLENDED_DOSING_CURVE"
        assert evidence["target_speed_kmh"] is not None
    else:
        assert evidence is None
        assert engine.capacity_for(method, settings, speed, context, TODAY, False,
            use_model_prior=use_model_prior) is None
