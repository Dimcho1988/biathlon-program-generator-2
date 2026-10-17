"""Behavioral acceptance for the final audit and the 15 October dose decision."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier, Lock
from time import sleep
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from apps.api import management_lifecycle as lifecycle, training_plan_engine as engine
from apps.api.planning_computation import compute
from apps.api.management_schemas import ManagementDayAction
from biathlon import adaptive_methods, method_library, method_dosing, planning_history
from tests.api.test_complete_method_recipes import ROWS, context
from tests.api.test_management_lifecycle import system, activate, advance, ACTOR
from tests.api.test_training_plan_engine import TODAY, NOW, BOUNDS


@pytest.mark.parametrize('readiness', [50, 70, 80, 90, 100])
@pytest.mark.parametrize('role,base', [('BUILDING', .65), ('MAINTENANCE', .39), ('SUPPORTING', .39)])
def test_goal_role_controls_dose_without_90_percent_switch(readiness, role, base):
    method = {'zone': 'Z3', 'structure': 'CONTINUOUS'}
    policy = adaptive_methods.readiness_policy(method, {}, {'Z1': 100., 'Z3': readiness})
    evidence = {'capacity_minutes': 100., 'readiness_policy': policy}
    nominal = engine._nominal_fraction(method, role, {'building_fraction': .65}, 'SPECIAL_PREPARATION', evidence, {})
    assert nominal == base
    assert policy['maximum_role'] == 'BUILDING'
    expected = 65 * (.5 + readiness / 200) if role == 'BUILDING' else 39
    assert 100 * nominal * evidence['readiness_dose_factor'] == pytest.approx(expected)


def test_auto_calendar_horizon_is_used_for_rollforward_and_day_actions(system):
    system.profiles.body.program_end = TODAY
    started = activate(system)
    changed = lifecycle.refresh(system.repo, 'athlete', ACTOR, expected_revision=started['revision'], now=NOW,
        day_action=ManagementDayAction(date=TODAY+timedelta(days=1), action='REST', expected_revision=started['revision']))
    assert changed['payload']['status'] != 'COMPLETED'
    updated = lifecycle.refresh(system.repo, 'athlete', automatic=True, now=advance(system, 1))
    assert updated['payload']['status'] != 'COMPLETED'


@pytest.mark.parametrize('row', [r for r in ROWS.values() if r.get('prescription')], ids=lambda r:r['id'])
def test_all_numeric_recipes_have_an_executable_first_dose_or_explicit_reason(row):
    profile, view, phase = context(row)
    resolution = method_library.resolve(row, profile, view, phase=phase)
    if resolution['status'] != 'READY':
        assert resolution.get('reason')
        return
    enabled = deepcopy(profile)
    enabled.setdefault('method_library', {}).setdefault('overrides', {})[row['id']] = {'enabled': True}
    with patch.object(method_library, 'entries', return_value=[row]):
        methods = method_library.executable_methods(enabled, view, phase=phase)
    assert len(methods) == 1, row['id']
    method = methods[0]
    settings = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=190)
    capacity = engine.capacity_for(method, settings, view, engine._capacity_context(view, settings), TODAY, use_model_prior=True)
    assert capacity
    minimum = engine._minimum_work(method, capacity, settings)
    assert minimum is not None, row['id']
    assert engine._blocks(method, minimum, capacity, settings)


def test_ao31_first_stage_is_inside_source_range_and_above_minimum():
    row = ROWS['AO31']; profile, view, phase = context(row)
    resolution = method_library.resolve(row, profile, view, phase=phase)
    assert resolution['status'] == 'READY'
    assert resolution['source_count_policy'] == 'INDIVIDUAL_TMAX_NOT_SOURCE_COUNT'
    assert resolution['minimum_work_seconds'] == pytest.approx(.25*resolution['continuous_capacity_seconds'])
    assert resolution['maximum_work_seconds'] >= .25 * resolution['continuous_capacity_seconds']
    assert resolution['segments'][0]['work_distance_m'] <= row['prescription']['work']['range'][1]


@pytest.mark.parametrize('structure,expected', [('CONTINUOUS', True), ('PROGRESSIVE_CONTINUOUS', True), ('INTERVALS', False)])
def test_threshold_choice_uses_library_structure(structure, expected):
    assert method_dosing.threshold_allowed({'zone': 'Z3', 'structure': 'LIBRARY_INTERVALS',
        'method_library': {'resolution': {'structure': structure}}}, 'CONTINUOUS') is expected


class SharedStore:
    def __init__(self):
        self.lock, self.tasks = Lock(), {}
    def planning_computation(self, alias, key, token, operation, result=None):
        with self.lock:
            task = self.tasks.get(alias)
            if operation == 'CLAIM':
                if task and task['key'] == key and task['state'] == 'READY':
                    return {'state': 'READY', 'result': deepcopy(task['result'])}
                if task and task['state'] == 'RUNNING':
                    return {'state': 'BUSY'}
                self.tasks[alias] = dict(key=key, token=token, state='RUNNING')
                return {'state': 'OWNED'}
            if not task or (task['key'], task['token']) != (key, token):
                return {'state': 'LOST'}
            if operation == 'COMPLETE':
                task.update(state='READY', result=result)
                return {'state': 'READY'}
            if operation == 'FAIL':
                task.update(state='FAILED')
                return {'state': 'FAILED'}
            return {'state': 'OWNED'}


@pytest.mark.parametrize('clients', [1, 10, 25])
def test_concurrent_ui_and_worker_join_one_computation(clients):
    repository, calls, lock = SharedStore(), [], Lock()
    barrier = Barrier(clients)
    def calculate():
        with lock: calls.append(1)
        sleep(.04)
        return {'activation_eligible': True, 'review_required': True}
    def request(_):
        barrier.wait()
        return compute(repository, 'athlete', 'a'*64, calculate, poll_seconds=.005)
    with ThreadPoolExecutor(max_workers=clients) as pool:
        results = list(pool.map(request, range(clients)))
    assert len(calls) == 1
    assert all(r['review_required'] for r in results)
    assert compute(repository, 'other', 'a'*64, calculate)['review_required']
    assert len(calls) == 2


def test_failed_computation_is_retryable_and_changed_input_is_not_reused():
    repository = SharedStore()
    with pytest.raises(ValueError):
        compute(repository, 'athlete', 'a'*64, lambda: (_ for _ in ()).throw(ValueError('crash')))
    assert compute(repository, 'athlete', 'a'*64, lambda: {'version': 1}) == {'version': 1}
    assert compute(repository, 'athlete', 'b'*64, lambda: {'version': 2}) == {'version': 2}


@pytest.mark.parametrize("completed_lock", [False, True])
def test_actual_strength_counts_toward_the_same_manual_quota(monkeypatch, completed_lock):
    from tests.api.test_training_plan_engine import Repository, reference_speed
    from tests.api.test_management_schedule import body
    repo = Repository()
    profile = body(sessions_per_week=7, sessions_by_day=[1]*7, strength_days=[0,2,4],
        max_strength_sessions=2, accent_mode='MANUAL', accents=['STR'], mixed_sessions_enabled=False)
    profile.update(strength_enabled=True, max_key_sessions_per_week=0)
    source = repo.envelope['snapshot_payload']['load_history']
    actual = {'activity_ref':'completed-strength', 'date':TODAY.isoformat(), 'local_date':TODAY.isoformat(),
              'sport':'WeightTraining', 'duration_min':20., 'quality_status':'valid', 'zones':[{'zone':z, 'raw_time_min':0., 'equivalent_time_min':0., 'effective_load':0.} for z in ('Z1','Z2','Z3','Z4','Z5')]}
    source['activities'].append(deepcopy(actual)); repo.envelope['activities'].append(deepcopy(actual))
    for row in source['strength']['daily']:
        if row['date'] == TODAY.isoformat(): row['effective_load'] = 20.
        elif row['effective_load'] > 0: row['effective_load'] = 8.
    method = next(m for m in engine.resolved_methods(profile) if m['structure']=='STRENGTH_CIRCUIT')
    monkeypatch.setattr(engine.model_service,'speed_view',reference_speed)
    monkeypatch.setattr(engine,'resolved_methods',lambda _:[deepcopy(method)])
    monkeypatch.setattr(engine.method_registry,'executable_methods',lambda *args,legacy_methods,**kw:legacy_methods)
    # A pre-import approved task is no longer a pending session once actual
    # execution closes today's slots. It must not consume a second quota.
    locked = {'date': TODAY.isoformat(), 'sessions': [
        {'zone': 'STR', 'purpose': 'MAINTENANCE', 'is_key_session': False}
    ]} if completed_lock else None
    plan = engine.generate_plan(repo,'athlete',profile,start_date=TODAY,now=NOW,locked_day=locked)
    assert not plan['days'][0]['sessions']
    planned = [s for day in plan['days'] for s in day['sessions'] if s['zone']=='STR']
    assert planned  # This fixture has room for a legal remaining strength dose.
    assert len(planned)+1 <= 2


def test_search_keeps_stability_on_ties_and_distinguishes_unproven_shortage():
    from apps.api.planning_search import improve
    baseline = {'days':[{'date':'2026-10-15','sessions':[{'zone':'Z1','main_work_minutes':60}]}],
        'allocation': {'components': {z:dict(target=100,actual=0,planned=50,remaining=50) for z in ['Z1','Z3','Z4','Z5','STR']}}}
    calls=[]
    def replay(caps):
        calls.append(caps)
        return deepcopy(baseline)
    result = improve(baseline, {'planning_controls':{'accents':['Z3']}}, replay)
    assert result is baseline and len(calls)==3
    assert result['allocation']['search']['conclusion']=='SEARCH_BUDGET_EXHAUSTED'
    assert result['allocation']['search']['hard_limit_proven'] is False


def test_recovery_quality_marks_missing_dates_without_filling_load():
    source = {'period_start': (TODAY-timedelta(days=2)).isoformat(), 'daily':[
        {'date': (TODAY-timedelta(days=1)).isoformat(), 'zone': zone, 'effective_load': 0.}
        for zone in ['Z1','Z2','Z3','Z4','Z5']]}
    before = deepcopy(source)
    quality = planning_history.recovery_coverage(source, TODAY)
    assert quality['complete'] is False and quality['missing_days'] == 39
    assert source == before


def test_import_queue_is_polled_while_a_planning_tick_is_running():
    from threading import Event
    from apps.api.sync_worker import run_worker
    entered, release, stopping = Event(), Event(), Event()
    class Repository:
        def refresh_due_training_plans(self):
            entered.set(); release.wait(5)
        def claim_sync_job(self, **kwargs):
            assert entered.wait(1)
            stopping.set()
            return None
    try:
        assert run_worker(Repository(), worker_id='test', stop_event=stopping) == 0
    finally:
        release.set()


def test_three_strength_sessions_require_a_maintenance_session(monkeypatch):
    from tests.api.test_training_plan_engine import Repository, reference_speed
    from tests.api.test_management_schedule import body
    from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness
    repo = Repository()
    profile = body(sessions_per_week=7, sessions_by_day=[1]*7, strength_days=[0,2,4],
        max_strength_sessions=3, accent_mode='MANUAL', accents=['STR'], mixed_sessions_enabled=False)
    profile.update(strength_enabled=True, max_key_sessions_per_week=0)
    source = repo.envelope['snapshot_payload']['load_history']
    for index in range(2):
        actual = {'activity_ref':f'completed-strength-{index}', 'date':TODAY.isoformat(), 'local_date':TODAY.isoformat(),
            'sport':'WeightTraining', 'duration_min':20., 'quality_status':'valid', 'zones':[
                {'zone':z, 'raw_time_min':0., 'equivalent_time_min':0., 'effective_load':0.} for z in ('Z1','Z2','Z3','Z4','Z5')]}
        source['activities'].append(deepcopy(actual)); repo.envelope['activities'].append(deepcopy(actual))
    for row in source['strength']['daily']:
        row['effective_load'] = 40. if row['date']==TODAY.isoformat() else 10.
    method = next(m for m in engine.resolved_methods(profile) if m['structure']=='STRENGTH_CIRCUIT')
    fixed_readiness(monkeypatch, 100.)
    monkeypatch.setattr(engine,'resolved_methods',lambda _:[deepcopy(method)])
    monkeypatch.setattr(engine.method_registry,'executable_methods',lambda *args,legacy_methods,**kw:legacy_methods)
    plan = engine.generate_plan(repo,'athlete',profile,start_date=TODAY,now=NOW)
    planned = [s for day in plan['days'] for s in day['sessions'] if s['zone']=='STR']
    assert len(planned)==1 and planned[0]['purpose']=='MAINTENANCE'
