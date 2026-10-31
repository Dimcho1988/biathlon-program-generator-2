"""Small admissible doses and anchored frequency must not become forced rest."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import training_plan_engine as engine
from biathlon import planning_schedule
from tests.api.test_feasible_quality_reservations import selected_methods, q_calendar
from tests.api.test_load_progression import configured, observed
from tests.api.test_management_schedule import body, high_capacity_history, completed_activity
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness, assert_readiness_dose, assert_rolling_budgets
from tests.api.test_training_plan_engine import TODAY, NOW, reference_speed


def test_coarse_upper_variant_cannot_discard_a_useful_lower_dose():
    # Only 2 Q remain from a larger cycle objective. Both variants can fit
    # its tolerance, but the 5-Q variant makes the residual gap worse.
    smaller, upper = (1., False, 1.), (3., True, 5.)
    assert engine._beneficial_q_variant([smaller, upper], 2.) == smaller
    assert engine._beneficial_q_variant([upper], 2.) is None
    nearby_upper = (.5, True, 2.5)
    assert engine._beneficial_q_variant([smaller, nearby_upper], 2.) == nearby_upper


def test_minimum_preserves_three_exact_45_second_repetitions():
    from tests.api.test_load_progression import configured
    from tests.api.test_training_plan_engine import Repository
    method = next(m for m in engine.resolved_methods(configured()) if m['structure']=='MODEL_INTERVALS')
    method.update(min_work_min=2.25, max_work_min=10., developmental_variant=True)
    method['interval_template'].update(work_seconds=45., min_repetitions=3)
    settings = Repository().settings
    evidence = engine.capacity_for(method, settings, None, (None, [], []), TODAY)
    engine._nominal_fraction(method, 'BUILDING', {'building_fraction':.65}, 'GENERAL_PREPARATION', evidence, {})
    minimum = engine._minimum_work(method, evidence, settings)
    assert minimum == 2.25
    assert engine._quantized_work(method, minimum) == minimum
    blocks = engine._blocks(method, minimum, evidence, settings)
    assert [b['duration_min'] for b in blocks if b['kind']=='WORK'] == [.75]*3


@pytest.mark.parametrize('identifier', ['END-THR-LONG-01', 'END-THR-TIME-01-FLEX'])
@pytest.mark.parametrize('available_q,expected', [(31., True), (2., False)])
def test_remaining_q_fits_complete_work_below_generic_tmax_floor(monkeypatch, identifier, available_q, expected):
    fixed_readiness(monkeypatch, 100.)
    monkeypatch.setattr(engine.model_service, 'speed_view', reference_speed)
    p = configured(age_years=30, training_experience_years=10, available_minutes=[180]*7)
    p['planning_controls'].update(sessions_per_week=1, sessions_by_day=[1,0,0,0,0,0,0],
        threshold_days=[0], threshold_method='AUTO', mixed_sessions_enabled=False)
    selected_methods(monkeypatch, p, {identifier})
    capacity = engine.capacity_for
    def large_capacity(*args, **kwargs):
        result = capacity(*args, **kwargs)
        return {**result, 'capacity_minutes':200.} if result and args[0]['zone']=='Z3' else result
    monkeypatch.setattr(engine, 'capacity_for', large_capacity)
    q_calendar(monkeypatch, lambda _: available_q/1.1)
    repo, _, _ = observed()
    original = deepcopy(repo.envelope)
    plan = engine.generate_plan(repo, 'athlete', p, start_date=TODAY, now=NOW)
    day = plan['days'][0]
    assert bool(day['sessions']) is expected
    assert repo.envelope == original
    if not expected:
        assert not any(d['sessions'] for d in plan['days'])
        return
    session = day['sessions'][0]
    e = session['dose_evidence']
    assert 25. < session['direct_equivalent_minutes']['Z3'] <= available_q + .005
    assert e['applied_minimum_capacity_fraction'] < .25
    assert e['dose_reduced']
    assert session['main_work_minutes'] >= e['minimum_primary_work_minutes']
    assert {b['kind'] for b in session['blocks']} >= {'WARMUP', 'WORK', 'COOLDOWN'}
    if identifier.endswith('FLEX'):
        work = [b for b in session['blocks'] if b['kind']=='WORK']
        assert len(work) >= 2
        assert all(6. <= b['duration_min'] <= 12. for b in work)
        assert len([b for b in session['blocks'] if b['kind']=='RECOVERY']) == len(work)-1
    assert_readiness_dose(session)
    assert_rolling_budgets(plan)
    replay = engine.generate_plan(repo, 'athlete', p, start_date=TODAY, now=NOW, locked_day=day)
    assert replay['days'][0]['locked']
    assert replay['days'][0]['sessions'] == day['sessions']


def test_previous_cycle_frequency_leaves_current_slots_but_all_fatigue_remains(monkeypatch):
    monkeypatch.setattr(engine.model_service, 'speed_view', reference_speed)
    repo = high_capacity_history()
    for i in range(10):
        completed_activity(repo, f'previous-{i}', minutes=5.)
        day = (TODAY-timedelta(days=1+i//2)).isoformat()
        for activities in (repo.envelope['activities'], repo.envelope['snapshot_payload']['load_history']['activities']):
            activities[-1].update(date=day, local_date=day, start_local=f'{day}T{8+8*(i%2):02}:00:00')
    completed_activity(repo, 'current', minutes=5.)
    source = repo.envelope['snapshot_payload']['load_history']
    for row in source['daily']:
        row['effective_load'] = sum(z['effective_load'] for a in source['activities']
            if a['date'] == row['date'] for z in a['zones'] if z['zone'] == row['zone'])
    p = body(sessions_per_week=7, mesocycle_anchor=TODAY.isoformat())
    p.update(program_start=TODAY.isoformat(), max_key_sessions_per_week=0)
    original = deepcopy(repo.envelope)
    plan = engine.generate_plan(repo, 'athlete', p, start_date=TODAY+timedelta(days=1), now=NOW)
    assert all(d['sessions'] for d in plan['days'][:3])
    for d in plan['days']:
        at = engine.date.fromisoformat(d['date'])
        left, right = planning_schedule.microcycle_bounds(at, at, TODAY, TODAY,
            engine.date.fromisoformat(p['program_end']))
        planned = sum(len(row['sessions']) for row in plan['days'] if left.isoformat() <= row['date'] <= right.isoformat())
        assert planned + int(left <= TODAY <= right) <= 7
    assert repo.envelope == original
    # Frequency eligibility changes no source loads or first-day Recovery.
    alternate = deepcopy(p)
    alternate['planning_controls']['sessions_per_week'] = 21
    wider = engine.generate_plan(repo, 'athlete', alternate, start_date=TODAY+timedelta(days=1), now=NOW)
    assert plan['days'][0]['readiness_before'] == wider['days'][0]['readiness_before']
