"""Exact instance-local reuse of repeated candidate speed/HR conversions."""
from copy import deepcopy

import pytest

from apps.api import training_plan_engine as engine
from tests.api.test_capacity_policy import TODAY, view_at


@pytest.mark.parametrize("blended", [False, True])
def test_repeated_speed_reference_keeps_exact_value_without_reinverting(monkeypatch, blended):
    _, _, (predictor, _, _) = view_at([180., 7200.], blended=blended)
    speeds = [predictor.speed_for_hr(predictor.min_hr + fraction * (predictor.max_hr-predictor.min_hr))
              for fraction in (.35, .65)]
    direct = type(predictor).hr_for_speed
    expected = [direct(predictor, speed) for speed in speeds]
    inverse = predictor.curve.inverse
    calls = []

    def counted(value, **kwargs):
        calls.append(value)
        return inverse(value, **kwargs)

    monkeypatch.setattr(predictor.curve, "inverse", counted)
    for _ in range(3):
        assert [predictor.hr_for_speed(speed) for speed in speeds] == expected
    assert len(calls) == 0  # Common zone pairs invert directly, without V(t).


@pytest.mark.parametrize("blended", [False, True])
def test_repeated_capacity_checks_retain_every_dose_and_reference_field(blended):
    speed, settings, cached = view_at([180., 7200.], blended=blended)
    if not blended:
        speed["index_summary"] = {
            anchor["zone"]: {"index": 100*anchor["hr_bpm"]/settings.hrmax_bpm/anchor["speed_kmh"], "count": 3}
            for anchor in cached[0].anchors
        }
        cached = engine._capacity_context(speed, settings)
    uncached = engine._capacity_context(speed, settings)
    predictor = uncached[0]
    predictor.hr_for_speed = type(predictor).hr_for_speed.__get__(predictor)
    methods = [{"zone": zone, "structure": "CONTINUOUS", "position": .6}
               for zone in ("Z1", "Z2", "Z3", "Z4")]
    for method in methods:
        expected = engine.capacity_for(method, settings, speed, uncached, TODAY, use_model_prior=True)
        assert expected is not None
        for _ in range(2):
            assert engine.capacity_for(method, settings, speed, cached, TODAY, use_model_prior=True) == expected


def test_different_curve_contexts_do_not_share_the_same_speed_reference(monkeypatch):
    speed, settings, (first, _, _) = view_at([180., 7200.])
    changed = deepcopy(speed)
    for test in changed["tests"]:
        test["payload"]["speed_kmh"] *= 1.05
    second, _, _ = engine._capacity_context(changed, settings)
    velocity = first.speed_for_hr((first.min_hr+first.max_hr)/2)
    expected = [type(predictor).hr_for_speed(predictor, velocity) for predictor in (first, second)]
    assert expected[0] != expected[1]
    calls = [0, 0]
    for index, predictor in enumerate((first, second)):
        inverse = predictor.curve.inverse

        def counted(value, *, _inverse=inverse, _index=index, **kwargs):
            calls[_index] += 1
            return _inverse(value, **kwargs)

        monkeypatch.setattr(predictor.curve, "inverse", counted)
    for _ in range(2):
        assert [predictor.hr_for_speed(velocity) for predictor in (first, second)] == expected
    assert calls == [0, 0]


def test_outside_curve_reference_still_raises_instead_of_becoming_a_prediction():
    _, _, (predictor, _, _) = view_at([180., 7200.])
    outside = predictor.curve.speed(predictor.curve.times[0])*3.6*2
    for _ in range(2):
        with pytest.raises(ValueError):
            predictor.hr_for_speed(outside)
