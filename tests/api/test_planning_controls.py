from copy import deepcopy
from datetime import timedelta
import pytest
from pydantic import ValidationError
from apps.api import training_plan_engine as engine
from apps.api.management_schemas import PlanningControls, CycleDirective
from biathlon import planning_controls
from biathlon.training_methods import resolved_methods
from tests.api.test_training_plan_engine import Repository, TODAY, NOW, profile, reference_speed, supported_speed


def controls(**patch):
    return PlanningControls(**patch).model_dump(mode="json")


def test_7_40_is_an_explicit_target_not_a_hidden_105_percent_c40_ceiling():
    repo=Repository();rows=engine._daily_rows(repo.envelope['snapshot_payload']['load_history'],TODAY)
    p=profile(planning_controls=controls(accent_mode='MANUAL', accents=['Z3'],accent_index=1.2,mesocycle_anchor=TODAY))
    goals,focus,state=engine._goals(p,TODAY+timedelta(days=14),'GENERAL_PREPARATION',False,{},None,2,4,rows,TODAY,False,1)
    base=planning_controls.reference(rows,TODAY)['Z3']
    assert focus==['Z3']
    assert goals['Z3']['target_index']==pytest.approx(1.32)
    assert (base['b50']+goals['Z3']['target']/7)/(base['b50']+base['c40'])==pytest.approx(1.32)
    assert goals['Z3']['readiness_permission'] is False


def test_stress_microcycle_is_followed_by_unloading_and_cannot_override_taper():
    d=dict(start_date=TODAY,end_date=TODAY+timedelta(days=6),name='Лагер',kind='STRESS',accents=['Z4'],target_index=1.35,volume_factor=1.1,recovery_days=7)
    p=profile(planning_controls=controls(cycles=[d]))
    a=planning_controls.resolve(p,TODAY,'SPECIAL_PREPARATION',['Z3','Z4'])
    b=planning_controls.resolve(p,TODAY+timedelta(days=7),'SPECIAL_PREPARATION',['Z3','Z4'])
    assert a['kind']=='STRESS' and a['accents']==['Z4']
    assert b['kind']=='RECOVERY' and b['target_index']==.78
    rows=engine._daily_rows(Repository().envelope['snapshot_payload']['load_history'],TODAY)
    ref=engine.training_targets.development_reference(rows,TODAY,TODAY,4)
    taper,_,_=engine._goals(p,TODAY,'PRECOMPETITION',True,ref,None,0,4,rows,TODAY,False,.5)
    plain,_,_=engine._goals(p,TODAY,'PRECOMPETITION',False,ref,None,0,4,rows,TODAY,False,1)
    assert taper['Z4']['target']==plain['Z4']['target']*.5
    with pytest.raises(ValidationError): controls(cycles=[d,{**d,'start_date':TODAY+timedelta(days=9),'end_date':TODAY+timedelta(days=12)}])
    with pytest.raises(ValidationError): CycleDirective(**{**d,'end_date':TODAY+timedelta(days=7)})


def test_selected_sports_add_their_own_history_and_keep_distinct_capacity_sources(monkeypatch):
    repo=Repository();source=repo.envelope['snapshot_payload']['load_history']
    source['activities'] += [{**a,'activity_ref':'ski-'+a['activity_ref'],'sport':'NordicSki','duration_min':30} for a in list(source['activities'])]
    seen=[]
    def speed(r,a,s): seen.append(s);return reference_speed(r,a,s)
    monkeypatch.setattr(engine.model_service,'speed_view',speed)
    result=engine.generate_plan(repo,'athlete',profile(sport='NordicSki',actual_sport='NordicSki',planning_controls=controls(training_sports=['NordicSki','Run'],intensity_days=[1],long_session_day=5)),start_date=TODAY+timedelta(days=1),now=NOW)
    v=result['parameters']['volume_evidence']['by_sport_weekly_minutes']
    assert result['parameters']['historical_selected_weekly_minutes']==pytest.approx(v['Run']+v['NordicSki'],abs=.002)
    assert seen==['NordicSki','Run']
    for d in result['days']:
        if d['session'] and d['session']['zone'] in {'Z3','Z4','Z5'}: assert engine.date.fromisoformat(d['date']).weekday()==1
    assert result['source']['speed_models_by_sport'].keys()=={'Run','NordicSki'}


def test_whole_training_budget_retains_means_specific_dosing_and_availability(monkeypatch):
    repo=Repository();s=repo.envelope['snapshot_payload']['load_history']
    s['activities'] += [{**a,'sport':'Ride','duration_min':600} for a in list(s['activities'])]
    monkeypatch.setattr(engine.model_service,'speed_view',reference_speed)
    result=engine.generate_plan(repo,'athlete',profile(planning_controls=controls(training_sports=['Run'])),start_date=TODAY+timedelta(days=1),now=NOW)
    v=result['parameters']['volume_evidence']
    assert v['all_sports_weekly_minutes'] > result['parameters']['historical_selected_weekly_minutes']*5
    assert result['parameters']['historical_selected_weekly_minutes']==pytest.approx(v['by_sport_weekly_minutes']['Run'],abs=.001)
    assert result['parameters']['baseline_weekly_minutes'] == pytest.approx(v['all_sports_weekly_minutes'], abs=.002)
    assert result['parameters']['weekly_minutes_ceiling'] <= sum(profile()['available_minutes'])
    for d in result['days']:
        if d['session']:
            assert d['session']['sport'] == 'Run'
            # Cycling's 600-minute sessions must not authorize such a run.
            assert d['session']['total_minutes'] <= 60*max(1, d['load_budget']['mesocycle_factor'])+.002
            assert any(l['code']=='ACTUAL_SPORT_SESSION_EXPOSURE' for l in d['session']['dose_evidence']['limits'])
    assert all(w['planned_minutes'] is None for w in result['history_comparison'])


def test_volume_basis_excludes_disabled_strength_and_suggests_observed_weekdays():
    source = Repository().envelope['snapshot_payload']['load_history']
    source['activities'] += [{**a,'sport':'WeightTraining','duration_min':30} for a in list(source['activities'])]
    evidence = planning_controls.volume_history(source, TODAY, 28)
    p = profile(planning_controls=controls())
    without = planning_controls.volume_basis(p, evidence)
    with_strength = planning_controls.volume_basis({**p,'strength_enabled':True}, evidence)
    assert with_strength['baseline_weekly_minutes']-without['baseline_weekly_minutes'] == pytest.approx(evidence['by_sport_weekly_minutes']['WeightTraining'], abs=.002)
    assert len(evidence['suggested_available_minutes']) == 7
    assert sum(evidence['suggested_available_minutes']) == pytest.approx(evidence['all_sports_weekly_minutes'], abs=18)
    source['strength']['daily'] = []
    assert planning_controls.volume_history(source, TODAY, 28)['suggested_available_minutes'] is None


def test_new_means_can_only_get_light_introduction_despite_large_other_sport_history(monkeypatch):
    monkeypatch.setattr(engine.model_service, 'speed_view', reference_speed)
    p = profile(sport='NordicSki', actual_sport='NordicSki', reentry_days=0,
                planning_controls=controls(training_sports=['NordicSki', 'Run']))
    result = engine.generate_plan(Repository(), 'athlete', p, start_date=TODAY+timedelta(days=1), now=NOW)
    for d in result['days']:
        s = d['session']
        if s and s['sport'] == 'NordicSki':
            assert s['purpose'] == 'RECOVERY' and s['zone'] in {'Z1', 'Z2'}
            assert s['main_work_minutes'] <= 30
    assert any(r['code'] == 'NO_ACTUAL_MODE_EXPOSURE' for d in result['days'] for r in d['rejected_alternatives'])


def test_model_prior_is_explicit_and_does_not_bypass_stale_or_exploratory_data():
    settings=Repository().settings;speed=supported_speed(settings);speed['tests']=speed['tests'][:1]
    curve=engine.speed_duration.calibrated([e['payload'] for e in speed['tests']])
    speed['index_summary']['Z3']['index']=(100*settings.zone_bounds_bpm[3]/settings.hrmax_bpm)/(curve.speed(3000)*3.6)
    method=next(m for m in resolved_methods({}) if m['zone']=='Z3')
    cap=engine.capacity_for(method,settings,speed,engine._capacity_context(speed,settings),TODAY,use_model_prior=True)
    assert cap['capacity_source']=='SPEED_DURATION_PRIOR'
    assert cap['target_speed_kmh']>0
    speed['index_window']['last_activity_date']=(TODAY-timedelta(days=20)).isoformat()
    cap=engine.capacity_for(method,settings,speed,engine._capacity_context(speed,settings),TODAY,use_model_prior=True)
    assert cap['capacity_source']=='EXPERT_CONTINUOUS_TREF'
    speed['tests'][0]['payload']['maximal']=False
    assert engine._capacity_context(speed,settings)[0] is None


def test_threshold_repetitions_fit_one_shared_dose_and_discretize_down():
    repo=Repository();method=next(m for m in resolved_methods({}) if m['structure']=='THRESHOLD_REPETITIONS')
    cap=engine.capacity_for(method,repo.settings,None,(None,[],['NO_INDIVIDUAL_SPEED_CURVE']),TODAY)
    blocks=engine._blocks(method,37,cap,repo.settings)
    works=[b for b in blocks if b['kind']=='WORK']
    assert 2<=len(works)<=8
    assert all(6<=b['duration_min']<=12 for b in works)
    assert sum(b['duration_min'] for b in works)<=37
    assert len([b for b in blocks if b['kind']=='RECOVERY'])==len(works)-1


def test_integrated_week_respects_separate_strength_days_and_uses_mixed_methods(monkeypatch):
    monkeypatch.setattr(engine.model_service,'speed_view',reference_speed)
    p=profile(strength_enabled=True,reentry_days=0,planning_controls=controls(accent_mode='MANUAL',accents=['Z3','STR'],mesocycle_anchor=TODAY,intensity_days=[1,4],strength_days=[2,5],long_session_day=6))
    r=engine.generate_plan(Repository(),'athlete',p,start_date=TODAY+timedelta(days=1),now=NOW)
    sessions=[d for d in r['days'] if d['session']]
    assert {d['session']['zone'] for d in sessions} >= {'Z1','Z2','Z3','STR'}
    for d in sessions:
        z=d['session']['zone'];weekday=engine.date.fromisoformat(d['date']).weekday()
        if z=='STR': assert weekday in [2,5]
        if z in {'Z3','Z4','Z5'}: assert weekday in [1,4]
        assert d['readiness_before'][z]>=90
    assert max(w['components']['Z3']['target_index_7_40'] for w in r['long_term']['weeks'])>1.1
    mixed=next(d['session'] for d in sessions if d['session']['method_id']=='END-ALT-10-05-01')
    assert {b['zone'] for b in mixed['blocks'] if b['kind']=='WORK'}=={'Z1','Z2'}
    assert mixed['total_minutes']==pytest.approx(sum(b['duration_min'] for b in mixed['blocks']),abs=.002)


def test_high_target_cannot_override_recovery_and_incomplete_history_is_unknown(monkeypatch):
    monkeypatch.setattr(engine.model_service,'speed_view',reference_speed)
    repo=Repository();source=repo.envelope['snapshot_payload']['load_history']
    for row in source['daily']:
        if row['date']==TODAY.isoformat() and row['zone']=='Z1':row['effective_load']=1000.
    r=engine.generate_plan(repo,'athlete',profile(planning_controls=controls(accent_mode='MANUAL',accents=['Z3'],accent_index=1.5)),start_date=TODAY+timedelta(days=1),now=NOW)
    assert any(a['code'] in {'RECOVERY_BELOW_90','WARMUP_NOT_READY'} for d in r['days'] for a in d['rejected_alternatives'])
    source['strength']['daily']=[]
    evidence=planning_controls.volume_history(source,TODAY,28)
    assert all(w['actual_minutes'] is None for w in evidence['weeks'])


def test_plan_actual_load_vectors_are_separate_and_missing_actual_load_is_unknown():
    from apps.api.management_lifecycle import reconcile
    repo=Repository();source=repo.envelope['snapshot_payload']['load_history'];key=(TODAY-timedelta(days=10)).isoformat()
    vector={z:float(i) for i,z in enumerate(engine.COMPONENTS)}
    plan={'days':[{'date':key,'session':{'title':'Test','sport':'Run','total_minutes':50,'canonical_effective_load':vector}}]}
    o=reconcile(plan,source,TODAY,{})[0]
    assert o['planned_load']==vector
    assert o['actual_load']==dict(Z1=60,Z2=30,Z3=20,Z4=10,Z5=8,STR=8)
    source['strength']['daily']=[]
    assert reconcile(plan,source,TODAY,{})[0]['actual_load'] is None
