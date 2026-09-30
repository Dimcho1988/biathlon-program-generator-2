from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from apps.api import training_plan_engine as engine, model_service
from biathlon import dosing_curve, race_specific, speed_duration
from tests.test_dosing_curve import BOUNDS, INDICES, TESTS, example
from biathlon.training_methods import resolved_methods
from tests.api.test_load_progression import configured

TODAY = date(2026, 9, 30)
SETTINGS = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=205)


def test_continuous_method_gets_the_blended_speed_and_estimated_time():
    view = example()
    context = engine._capacity_context(view, SETTINGS)
    method = {'zone': 'Z2', 'structure': 'CONTINUOUS', 'position': .6}
    dose = engine.capacity_for(method, SETTINGS, view, context, TODAY)
    assert dose['capacity_source'] == 'BLENDED_DOSING_CURVE'
    assert dose['model_version'] == dosing_curve.VERSION
    assert dose['capacity_confidence'] == 'COACH_30_70_ESTIMATE'
    predictor = context[0]
    assert dose['target_speed_kmh'] == pytest.approx(predictor.speed_for_hr(dose['target_hr_bpm']), abs=.001)
    assert dose['capacity_minutes'] == pytest.approx(predictor.duration(dose['target_hr_bpm'])/60, abs=.001)
    assert engine.capacity_for(method, SETTINGS, view, context, TODAY, False) is None
    view['index_window']['last_activity_date'] = '2026-08-31'
    stale = engine.capacity_for(method, SETTINGS, view, context, TODAY)
    assert stale['capacity_source'] == 'EXPERT_CONTINUOUS_TREF'
    assert stale['target_speed_kmh'] is None


def test_short_intervals_use_blend_but_retain_the_measured_test_as_evidence():
    view = example()
    method = next(m for m in resolved_methods(configured()) if m['zone']=='Z5' and m['structure']=='MODEL_INTERVALS')
    context = engine._capacity_context(view, SETTINGS)
    dose = engine.capacity_for(method, SETTINGS, view, context, TODAY)
    assert dose['capacity_source'] == 'BLENDED_DOSING_CURVE'
    assert dose['test_anchor'] == TESTS[0]
    assert dose['target_hr_bpm'] is None
    assert dose['target_speed_kmh'] == pytest.approx(context[0].curve.speed(180)*3.6)
    assert dose['effort_profile']['target_speed_kmh'] == dose['target_speed_kmh']


def test_manual_speed_keeps_coach_target_and_uses_the_blended_inverse_for_capacity():
    view = example()
    context = engine._capacity_context(view, SETTINGS)
    speed = context[0].curve.speed(600)*3.6
    method = {'zone':'Z5', 'structure':'METABOLIC_INTERVALS',
              'interval_profile': {'continuous_capacity_min':20, 'target_speed_kmh':speed,
                  'speed_basis':'FLAT_EQUIVALENT', 'work_seconds':30, 'assessed_on':TODAY.isoformat()}}
    dose = engine.capacity_for(method, SETTINGS, view, context, TODAY)
    assert dose['capacity_source'] == 'BLENDED_DOSING_CURVE'
    assert dose['capacity_minutes'] == pytest.approx(10)
    assert dose['target_speed_kmh'] == speed


def test_race_performance_and_cs_stay_original_while_methods_use_dosing_bands():
    view = example()
    profile = configured(discipline='4000 m', age_years=30, training_experience_years=5)
    event = {'source':'SPEED_DURATION', 'distance_m':4000}
    old_view = deepcopy(view); old_view.pop('dosing_model')
    old = race_specific.reference(profile, old_view, event, TODAY)
    new = race_specific.reference(profile, view, event, TODAY)
    assert new['speed_kmh'] == old['speed_kmh']
    assert new['duration_s'] == old['duration_s']
    assert new['bands'] == old['bands']
    curve = dosing_curve.from_view(view)
    for band in new['dosing_bands']:
        assert band['speed_kmh'] == pytest.approx(curve.speed(band['maximum_duration_s'])*3.6)
        assert band['lactate_reference'] is None
    methods = race_specific.methods(profile, new, resolved_methods(profile))
    assert methods and all(m['race_specific']['dosing_model_version'] == dosing_curve.VERSION for m in methods)
    assert speed_duration.standardized_critical_speed(TESTS) == speed_duration.standardized_critical_speed([e['payload'] for e in view['tests']])


def test_service_adds_a_dosing_view_without_changing_original_points_or_tests(monkeypatch):
    from tests.api.test_speed_history_window import Repository, NOW
    from datetime import datetime
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None): return NOW.astimezone(tz)
    monkeypatch.setattr(model_service, 'datetime', Fixed)
    repo = Repository()
    repo.add('paired-run', -1)
    repo.test(duration_s=180, speed_kmh=23)
    repo.test(duration_s=720, speed_kmh=19.2)
    before = deepcopy(repo.rows)
    view = model_service.speed_view(repo,'ath-test','Run')
    assert view['dosing_model']['status'] == 'AVAILABLE'
    for test in TESTS:
        point = next(p for p in view['points'] if p['duration_s']==test['duration_s'])
        assert point['speed_kmh'] == pytest.approx(test['speed_kmh'])
        assert point['evidence'] == 'MEASURED'
    assert repo.rows == before and not repo.saved
    repo.activities[0]['local_date'] = '2026-01-01'
    assert model_service.speed_view(repo,'ath-test','Run')['dosing_model']['status'] == 'UNAVAILABLE'
