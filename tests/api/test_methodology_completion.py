"""Acceptance boundaries from the 12 October methodology audit."""
from copy import deepcopy
from datetime import timedelta
import pytest

from apps.api import training_plan_engine as engine
from apps.api.double_threshold_continuation import reconcile
from apps.api.management_schemas import ManagementProfile, PlanningControls
from biathlon import adaptive_methods, combined_methods, method_library, method_progression, method_registry
from biathlon.speed_duration import prescription_critical_speed
from tests.api.test_training_plan_engine import Repository, TODAY, NOW, profile
from tests.api.test_sourced_method_library import view, athlete, recipe
from tests.api.test_management_schedule import body, high_capacity_history, run, completed_activity
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness


def proven_shortage(monkeypatch):
    """Isolate dosing/reservations; the real slot proof has acceptance tests."""
    monkeypatch.setattr(engine.combined_methods, "opportunities", lambda *args, **kwargs: {
        "shortage": True, "sessions_by_component": {}, "required_separate_sessions": 2,
        "available_sessions": 1, "basis": "TEST_PROVEN_SLOT_SHORTAGE"})


@pytest.mark.parametrize('recovery,role,fraction', [(49.99,'NONE',0), (50,'BUILDING',.4875), (90,'BUILDING',.6175), (90.01,'BUILDING',.6175325), (100,'BUILDING',.65)])
def test_role_boundaries_are_inclusive_and_apply_recovery_once(recovery, role, fraction):
    method = {'zone':'Z2','structure':'CONTINUOUS'}
    policy = adaptive_methods.readiness_policy(method, {}, {'Z1':100., 'Z2':recovery})
    evidence = {'readiness_policy':policy}
    nominal = engine._nominal_fraction(method, 'BUILDING', {'building_fraction':.65}, 'GENERAL_PREPARATION', evidence, None)
    assert policy['maximum_role'] == role
    assert nominal*policy['dose_factor'] == pytest.approx(fraction)


def test_low_required_recovery_blocks_quality_but_keeps_z1_available():
    assert not adaptive_methods.readiness_policy({'zone':'Z3'}, {}, {'Z1':49, 'Z3':100})['allowed']
    assert adaptive_methods.readiness_policy({'zone':'Z1'}, {}, {'Z1':49})['allowed']


def test_cs_prefers_real_tests_and_model_fallback_never_creates_tests():
    v=view(); curve,tests,_=method_library.selected_curve(v)
    original=deepcopy(tests)
    measured=prescription_critical_speed(tests,curve)
    assert measured['source']=='DIRECT_ACCEPTED_TEST_CS'
    assert measured['speed_kmh']>0
    estimated=prescription_critical_speed([],curve)
    expected=(curve.speed(720)*720-curve.speed(180)*180)/540*3.6
    assert estimated['speed_kmh']==pytest.approx(expected)
    assert estimated['source']=='CURRENT_CURVE_V3_V12' and estimated['measured'] is False
    with pytest.raises(ValueError, match='CV_REQUIRES'):
        prescription_critical_speed([],curve,allow_model_estimate=False)
    assert tests==original


def test_one_registry_exposes_and_disables_existing_coach_methods():
    p=athlete(); original=method_registry.catalog(p,view(),phase='GENERAL_PREPARATION')
    identifier='END-LONG-Z1-01-BUILD'
    assert any(m['id']==identifier and m['registry_kind']=='COACH_PROFILE' for m in original['methods'])
    p['method_library']={'enabled':True,'overrides':{identifier:{'enabled':False}}}
    assert all(m['id']!=identifier for m in method_registry.executable_methods(p,{'Run':view()}))
    p['method_library']['enabled']=False
    assert not method_registry.executable_methods(p,{'Run':view()})


def test_mixed_off_removes_all_legacy_combined_shapes():
    p=athlete(); p['planning_controls']={**p.get('planning_controls',{}),'mixed_sessions_enabled':False}
    methods=method_registry.executable_methods(p,{'Run':view()})
    assert all(not m.get('mixed_component') and m['structure'] not in method_registry.COMBINED_STRUCTURES for m in methods)


def progression_row(record,p,day,step=0,success=True,sport='Run'):
    spec=method_progression.definition(record,p)
    return {'id':day+str(step),'day':day,'success':success,'rpe':7,
        'method':{'sport':sport,'progression':{'version':method_progression.VERSION,'key':spec['key'],'step':step}}}


def test_race_progression_requires_confirmed_distinct_dates_and_retracts_on_failure():
    records={m['id']:m for m in method_library.entries()}
    short,medium,long=[records['OF-RUN-5000-'+suffix] for suffix in ('SHORT','MEDIUM','LONG')]
    p=athlete()
    # The coaching templates contain no published repeat count. Qualification
    # changes eligibility only; an explicit selected standard still owns dose.
    p['method_library']={'overrides':{r['id']:{'repetitions':[6,6]} for r in (short,medium,long)}}
    assert method_library.resolve(medium,p,view(),phase='PRECOMPETITION')['status']=='PROGRESSION_LOCKED'
    one=progression_row(short,p,'2026-09-01')
    p['_method_progression']={'observations':[one,deepcopy(one)]}
    assert method_progression.state(medium,p,phase='SPECIAL_PREPARATION')['eligible'] is False
    p['_method_progression']['observations'].append(progression_row(short,p,'2026-09-03'))
    assert method_library.resolve(medium,p,view(),phase='SPECIAL_PREPARATION')['status']=='READY'
    assert method_library.resolve(long,p,view(),phase='PRECOMPETITION')['status']=='PROGRESSION_LOCKED'
    p['_method_progression']['observations'].append(progression_row(medium,p,'2026-09-05',step=1,success=False))
    assert method_library.resolve(medium,p,view(),phase='SPECIAL_PREPARATION')['status']=='PROGRESSION_LOCKED'


def test_execution_progression_is_specific_to_actual_sport_and_coach_ranges():
    record=next(m for m in method_library.entries() if m['id']=='OF-XC-MEDIUM')
    p=athlete(sport='NordicSki',actual_sport='RollerSki',discipline='10 km')
    p['_method_progression']={'observations':[progression_row(record,p,'2026-09-01',sport='NordicSki'), progression_row(record,p,'2026-09-03',sport='NordicSki')]}
    assert not method_progression.state(record,p,phase='SPECIAL_PREPARATION')['eligible']
    for row in p['_method_progression']['observations']: row['method']['sport']='RollerSki'
    assert method_progression.state(record,p,phase='SPECIAL_PREPARATION')['eligible']
    p['method_library']={'overrides':{record['id']:{'speed_multiplier':[1.1,1.2]}}}
    assert not method_progression.state(record,p,phase='SPECIAL_PREPARATION')['eligible']


def test_variable_length_advances_from_execution_not_calendar():
    r=recipe();r['prescription'].update(work={'unit':'seconds','range':[30,90]},speed_multiplier=[1.01,1.05])
    p=athlete(); early=method_library.resolve(r,p,view(),phase='GENERAL_PREPARATION')
    later=method_library.resolve(r,p,view(),phase='PRECOMPETITION')
    assert early['work_duration_seconds']==later['work_duration_seconds']==30
    p['_method_progression']={'observations':[progression_row(r,p,'2026-09-01'),progression_row(r,p,'2026-09-03')]}
    progressed=method_library.resolve(r,p,view(),phase='SPECIAL_PREPARATION')
    assert progressed['work_duration_seconds']==60
    assert progressed['speed_multiplier']<early['speed_multiplier']
    assert progressed['anchor']['speed_kmh']==early['anchor']['speed_kmh']


def parents():
    return [{'id':z,'title':z,'zone':z,'structure':'CONTINUOUS','sports':['Run'],
        'periods':['GENERAL_PREPARATION'],'purpose':'BUILDING','position':.5,
        'min_work_min':1.,'max_work_min':100.,'warmup_min':10.,'cooldown_min':5.,'instructions':'Steady'} for z in ('Z3','Z2')]


@pytest.mark.parametrize('recovery,expected',[ (100.,65.),(80.,58.5) ])
def test_combination_keeps_each_own_role_budget_then_scales_once(recovery,expected):
    shortage=combined_methods.opportunities({'Z3':65.,'Z2':65.},{'Z3':65.,'Z2':65.},1)
    _,method=combined_methods.candidates([('Run',m) for m in parents()],shortage)[0]
    policy=adaptive_methods.readiness_policy(method,{}, {'Z1':100.,'Z2':recovery,'Z3':recovery})
    caps=[{'capacity_minutes':100.,'target_speed_kmh':None,'target_hr_bpm':155. if p['zone']=='Z3' else 143.} for p in method['combined_methods']]
    evidence={**caps[0],'combined_capacities':caps,'readiness_policy':policy,'readiness_dose_factor':policy['dose_factor']}
    nominal=engine._nominal_fraction(method,'BUILDING',{'building_fraction':.65},'GENERAL_PREPARATION',evidence,None)
    blocks=engine._blocks(method,100*nominal*policy['dose_factor'],evidence,Repository().settings)
    assert blocks
    for index in (1,2):
        assert sum(b['duration_min'] for b in blocks if b['kind']=='WORK' and b.get('combination_part')==index)==pytest.approx(expected)
    assert sum(b['kind']=='WARMUP' for b in blocks)==1
    assert sum(b['kind']=='COOLDOWN' for b in blocks)==1
    assert engine._dose_fits(blocks,evidence,'Z3',nominal*policy['dose_factor'])
    assert all(b['allocation']==1. for b in evidence['combined_budgets'])
    assert combined_methods.candidates([('Run',m) for m in parents()],combined_methods.opportunities({'Z3':65.,'Z2':65.},{'Z3':65.,'Z2':65.},2))


def test_double_threshold_range_and_legacy_profile_migration():
    with pytest.raises(ValueError): PlanningControls(double_threshold_fraction=.49)
    legacy=body(double_threshold_days=[0]);legacy['planning_controls']['double_threshold_fraction']=.4
    normalized=ManagementProfile.model_validate(legacy)
    assert normalized.planning_controls.double_threshold_fraction==.5
    assert legacy['planning_controls']['double_threshold_fraction']==.4


def test_double_pair_continues_only_exact_confirmed_success(monkeypatch):
    fixed_readiness(monkeypatch,100.)
    p=body(sessions_per_week=13,sessions_by_day=[2,2,2,2,2,2,1],double_threshold_days=[0])
    repo=high_capacity_history(); approved=run(monkeypatch,p,repo)['days'][0]
    assert len(approved['sessions'])==2 and all(s['double_threshold'] for s in approved['sessions'])
    first,second=approved['sessions'];actual=[{'activity_ref':'morning'}]
    report={'id':'morning','day':approved['date'],'method':{'id':first['method_id'],'sport':first['sport']},'success':True,'rpe':7}
    before=deepcopy(approved)
    assert reconcile(approved,actual,[])==approved
    assert reconcile(approved,actual,[{**report,'success':False}])==approved
    resumed=reconcile(approved,actual,[report])
    assert resumed['sessions'][0]['method_id']==second['method_id']
    assert resumed['sessions'][0]['continuation_of_confirmed_activity']=='morning'
    assert resumed['activity_refs']==['morning']
    assert approved==before


def test_imported_confirmed_morning_preserves_remaining_pair_in_real_generator(monkeypatch):
    fixed_readiness(monkeypatch,100.)
    p=body(sessions_per_week=13,sessions_by_day=[2,2,2,2,2,2,1],double_threshold_days=[0])
    repo=high_capacity_history()
    approved=run(monkeypatch,p,repo)['days'][0]
    first,second=approved['sessions']
    minutes=first['total_minutes']; completed_activity(repo,'morning',minutes)
    source=repo.envelope['snapshot_payload']['load_history']
    # Replace the imported easy fixture with the actual threshold session Q.
    actual=source['activities'][-1]
    for row in actual['zones']:
        row['raw_time_min']=sum(b['duration_min'] for b in first['blocks'] if b['zone']==row['zone'])
        row['equivalent_time_min']=first['direct_equivalent_minutes'][row['zone']]
    from tests.api.test_management_schedule import stored_activity_load
    effective=stored_activity_load(actual)
    for row in source['daily']:
        if row['date']==TODAY.isoformat(): row['effective_load']=effective[row['zone']]
    repo.envelope['activities'][-1]=deepcopy(actual)
    descriptor={'id':first['method_id'],'zone':first['zone'],'sport':'Run','structure':'THRESHOLD_LONG',
                'purpose':'BUILDING','version':'confirmed-test-definition','catalog_version':'test'}
    repo.entries=[{'kind':'SESSION','entry_key':'morning','revision':1,'recorded_at':NOW.isoformat(),
        'payload':{'method_confirmed':True,'execution_comparable':True,'executed_method_id':first['method_id'],
            'executed_method':descriptor,'duration_minutes':minutes,'planned_duration_minutes':minutes,
            'rpe':7.,'timing':'IMMEDIATE','execution_reason':'AS_PLANNED'}}]
    plan=engine.generate_plan(repo,'athlete',p,start_date=TODAY,now=NOW,locked_day=approved)
    day=plan['days'][0]
    assert len(day['sessions'])==1, day['explanation']
    assert day['sessions'][0]['method_id']==second['method_id']
    assert day['sessions'][0]['continuation_of_confirmed_activity']=='morning'
    assert plan['summary']['actual_sessions']==1
    assert day['actual_minutes']==minutes
    assert day['sessions'][0]['main_work_minutes']==second['main_work_minutes']


def test_combinations_follow_remaining_components_without_a_separate_slot_solver():
    for slots in (1, 2, 7):
        result = combined_methods.opportunities({'Z1':30.,'Z3':60.}, {'Z1':30.,'Z3':30.}, slots)
        assert result['consider_combinations']
        assert result['sessions_by_component'] == {'Z1': 1, 'Z3': 2}
        assert 'PROVEN' not in result['basis']
    assert not combined_methods.opportunities({'Z5':10.}, {'Z1':30.}, 1)['consider_combinations']
    assert not combined_methods.opportunities({'Z1':30.,'Z3':0.}, {'Z1':30.,'Z3':30.}, 1)['consider_combinations']


def test_real_week_can_cover_two_components_in_one_complete_combination(monkeypatch):
    from tests.api.test_training_plan_engine import reference_speed
    from tests.api.test_readiness_adaptive_plan_v2 import assert_rolling_budgets
    fixed_readiness(monkeypatch, 100.)
    p = body(sessions_per_week=1, sessions_by_day=[0,1,0,0,0,0,0], threshold_days=[1],
             accent_mode='MANUAL', accents=['Z3','Z2'], mixed_sessions_enabled=True)
    p.update(age_years=30, training_experience_years=5, available_minutes=[360]*7,
             component_targets_weekly={'Z1':400,'Z2':200,'Z3':100,'Z4':0,'Z5':0,'STR':0})
    methods = [dict(m, source_id=m['id'], source_version='1') for m in parents()]
    monkeypatch.setattr(engine, 'resolved_methods', lambda _: deepcopy(methods))
    monkeypatch.setattr(method_library, 'executable_methods', lambda *a, **kw: [])
    monkeypatch.setattr(engine.model_service, 'speed_view', reference_speed)
    plan = engine.generate_plan(high_capacity_history(), 'athlete', p, start_date=TODAY, now=NOW)
    selected = [s for d in plan['days'] for s in d['sessions']]
    assert len(selected) == 1 and selected[0]['method_id'].startswith('COMBO-')
    session = selected[0]
    evidence = session['dose_evidence']
    assert evidence['combination_shortage']['required_separate_sessions'] > 1
    assert {part['id'] for part in session['execution_descriptor']['parts']} == {'Z2','Z3'}
    assert {card['id'] for card in session['method_library']['components']} == {'Z2','Z3'}
    assert all(b['duration_s'] == pytest.approx(b['duration_min']*60, abs=.001) for b in session['blocks'])
    for number, budget in enumerate(evidence['combined_budgets'], 1):
        work = sum(b['duration_min'] for b in session['blocks'] if b['kind']=='WORK' and b.get('combination_part')==number)
        assert work <= budget['nominal_work_minutes'] + .001
        assert 0 < budget['allocation'] <= 1.
    assert_rolling_budgets(plan)
    p['planning_controls']['mixed_sessions_enabled'] = False
    separate = engine.generate_plan(high_capacity_history(), 'athlete', p, start_date=TODAY, now=NOW)
    assert all(not s['method_id'].startswith('COMBO-') for d in separate['days'] for s in d['sessions'])


def test_approved_combination_feedback_preserves_source_steps_and_rejects_other_day(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from apps.api.learning_methods import method_descriptor, confirmed_descriptor, assess_methods
    from apps.api.management_plan_store import PlanStore
    p = athlete()
    short = next(m for m in method_library.entries() if m['id']=='OF-RUN-5000-SHORT')
    medium = next(m for m in method_library.entries() if m['id']=='OF-RUN-5000-MEDIUM')
    resolution = method_library.resolve(short, p, view(), phase='GENERAL_PREPARATION')
    source = {'id':'LIB-OF-RUN-5000-SHORT-GENERAL_PREPARATION', 'title':'Short', 'zone':'Z4',
              'sports':['Run'], 'structure':'LIBRARY_INTERVALS', 'purpose':'BUILDING',
              'method_library':{**short, 'resolution':resolution}}
    combo = {**parents()[0], 'id':'COMBO-approved', 'combined_methods':[source, parents()[1]]}
    frozen = method_descriptor(combo, 'Run')
    dates = [(TODAY-timedelta(days=n)).isoformat() for n in (4,2)]
    plan = {'payload':{'plan':{'days':[{'date':day, 'sessions':[{'method_id':combo['id'], 'sport':'Run', 'execution_descriptor':frozen}]} for day in dates]}}}
    monkeypatch.setattr(PlanStore, 'current', lambda self, alias: deepcopy(plan))
    report = SimpleNamespace(method_confirmed=True, executed_method_id=combo['id'])
    entries, activities = [], []
    for day in dates:
        activity = {'activity_ref':day, 'local_date':day, 'sport':'Run', 'duration_min':40.}
        descriptor = confirmed_descriptor(object(), 'athlete', report, activity)
        assert descriptor == frozen
        entries.append({'kind':'SESSION', 'entry_key':day, 'revision':1, 'recorded_at':day+'T12:00:00Z',
                        'payload':{'method_confirmed':True, 'execution_comparable':True,
                            'executed_method_id':combo['id'], 'executed_method':descriptor,
                            'planned_duration_minutes':40., 'duration_minutes':40., 'rpe':7,
                            'timing':'IMMEDIATE', 'execution_reason':'AS_PLANNED'}})
        activities.append(activity)
    observations = assess_methods(entries=entries, source={'activities':activities}, today=TODAY)['memory']['observations']
    p['_method_progression'] = {'observations':observations}
    advanced = method_progression.state(medium, p, phase='SPECIAL_PREPARATION')
    assert advanced['eligible'] and advanced['requires_fresh_building']
    with pytest.raises(HTTPException) as exc:
        confirmed_descriptor(object(), 'athlete', report, {**activities[0], 'local_date':TODAY.isoformat()})
    assert exc.value.status_code == 422


def test_rolling_window_retains_server_qualified_stage_without_accepting_unqualified_skip():
    long = next(m for m in method_library.entries() if m['id']=='OF-RUN-5000-LONG')
    p = athlete()
    row = progression_row(long, p, '2026-09-03', step=2)
    p['_method_progression'] = {'observations':[row]}
    assert not method_progression.state(long, p, phase='SPECIAL_PREPARATION')['eligible']
    # This flag is frozen server-side only when the stage was already unlocked.
    row['method']['progression']['qualified_at_prescription'] = True
    assert method_progression.state(long, p, phase='SPECIAL_PREPARATION')['eligible']
    failed = deepcopy(row); failed.update(day='2026-09-05', id='fatigue', success=False)
    p['_method_progression']['observations'].append(failed)
    assert not method_progression.state(long, p, phase='SPECIAL_PREPARATION')['eligible']
