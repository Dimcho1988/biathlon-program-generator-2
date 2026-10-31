"""Candidate and reservation paths obey the same roles and replay choices."""
from apps.api import training_plan_engine as engine
from tests.api.test_feasible_quality_reservations import selected_methods
from tests.api.test_load_progression import configured, observed
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness, assert_readiness_dose, assert_rolling_budgets
from tests.api.test_training_plan_engine import TODAY, NOW


def test_repair_can_reduce_a_mixed_core_without_truncating_its_minimum(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=25, training_experience_years=5, available_minutes=[180]*7)
    body['planning_controls'].update(sessions_per_week=1, sessions_by_day=[1,0,0,0,0,0,0],
        mixed_sessions_enabled=True, threshold_method='AUTO')
    selected_methods(monkeypatch, body, {'END-THR-LONG-01-MIX-STEADY'})
    repo, _, _ = observed()
    baseline = engine.generate_plan(repo, 'athlete', body, start_date=TODAY, now=NOW)
    session = baseline['days'][0]['sessions'][0]
    minimum = session['dose_evidence']['minimum_structure_work_minutes']
    policy = {'optional_easy_fraction':0., 'easy_caps':{TODAY.isoformat():minimum}, 'easy_cap_zones':['Z3']}
    repaired = engine.generate_plan(repo, 'athlete', {**body, '_allocation_policy':policy}, start_date=TODAY, now=NOW)
    candidate = repaired['days'][0]['sessions'][0]
    assert candidate['method_id'] == session['method_id']
    assert candidate['direct_equivalent_minutes']['Z3'] < session['direct_equivalent_minutes']['Z3']
    assert candidate['dose_evidence']['structure_work_budget_minutes'] >= minimum
    assert any(limit['code']=='ALTERNATIVE_EASY_ALLOCATION' for limit in candidate['dose_evidence']['limits'])
    assert_readiness_dose(candidate)
    assert_rolling_budgets(repaired)


def test_background_development_receives_a_key_slot_after_the_main_accent_is_covered(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=25, training_experience_years=5, available_minutes=[180]*7)
    body['planning_controls'].update(sessions_per_week=1, sessions_by_day=[1,0,0,0,0,0,0],
        mixed_sessions_enabled=False, threshold_method='AUTO')
    selected_methods(monkeypatch, body, {'ONFLOWS-CONTROLLED-Z4-V2'})
    original = engine._goals
    def goals(*args, **kwargs):
        values, accents, state = original(*args, **kwargs)
        for z, target in [('Z1',200.),('Z3',0.),('Z4',30.)]:
            values[z].update(target_weekly_q=target, desired_weekly_q=target)
        state.update(background_development=True, component_indices={'Z3':1.6,'Z4':1.4}, focus_period='GENERAL_PREPARATION')
        return values, ['Z3'], state
    monkeypatch.setattr(engine, '_goals', goals)
    repo, _, _ = observed()
    plan = engine.generate_plan(repo, 'athlete', body, start_date=TODAY, now=NOW)
    sessions = [s for d in plan['days'] for s in d['sessions']]
    assert sessions and sessions[0]['zone']=='Z4'
    assert sessions[0]['purpose']=='BUILDING' and sessions[0]['is_key_session']
    assert_readiness_dose(sessions[0])
    assert_rolling_budgets(plan)
