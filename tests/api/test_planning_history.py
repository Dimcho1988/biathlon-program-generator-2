from copy import deepcopy
from datetime import timedelta

import pytest
from apps.api import training_plan_engine as engine
from apps.api.management_schemas import ManagementProfile
from biathlon import planning_history, planning_controls
from biathlon.training_methods import resolved_methods
from tests.api.test_training_plan_engine import Repository, TODAY, NOW, profile, reference_speed, supported_speed
from tests.api.test_planning_controls import controls


def history(days=28, pauses=(), missing=()):
    source = deepcopy(Repository().envelope['snapshot_payload']['load_history'])
    dates = {(TODAY-timedelta(days=n)).isoformat() for n in range(1, days+1)}
    absent = {(TODAY-timedelta(days=n)).isoformat() for n in missing}
    resting = {(TODAY-timedelta(days=n)).isoformat() for n in pauses}
    source['daily'] = [r for r in source['daily'] if r['date'] in dates-absent]
    source['strength']['daily'] = [r for r in source['strength']['daily'] if r['date'] in dates-absent]
    source['activities'] = [dict(date=d, sport='Run', duration_min=120, zones=[dict(zone='Z1',raw_time_min=120)]) for d in dates-absent-resting]
    return source


@pytest.mark.parametrize('days,usable', [(9,False),(10,True),(19,True),(28,True)])
def test_ten_observed_days_are_sufficient_including_confirmed_rest(days, usable):
    result = planning_history.assess(history(days, pauses=[1,2]), TODAY)
    assert result['usable'] is usable
    assert result['reference_days'] == days


@pytest.mark.parametrize('missing', [True,False])
@pytest.mark.parametrize('resumed,usable', [(9,False),(10,True)])
def test_large_break_uses_only_resumed_segment(missing, resumed, usable):
    gap = range(resumed+1,resumed+11)
    source = history(missing=gap) if missing else history(pauses=gap)
    # Old volume must not inflate the resumed baseline.
    for a in source['activities']:
        if a['date'] < (TODAY-timedelta(days=resumed)).isoformat(): a['duration_min']=300
    evidence = planning_controls.volume_history(source, TODAY, 0)
    policy = evidence['history_policy']
    assert policy['usable'] is usable and policy['reference_days'] == resumed
    assert policy['gaps'][0]['kind'] == ('MISSING_OR_MIXED_COVERAGE' if missing else 'CONFIRMED_BREAK')
    assert planning_controls.volume_basis(profile(planning_controls=controls()), evidence)['baseline_weekly_minutes'] == 840


def test_missing_days_are_not_counted_as_rest_and_gap_threshold_is_configurable():
    source = history(12, missing=[2,3,4])
    assert planning_history.assess(source,TODAY)['reference_days'] == 9
    source = history(pauses=range(1,8))
    assert planning_history.assess(source,TODAY)['usable']
    assert not planning_history.assess(source,TODAY,gap_days=7)['usable']


def test_legacy_template_migrates_but_custom_and_explicit_manual_limits_survive():
    p=profile(available_minutes=planning_history.LEGACY_AVAILABILITY)
    assert planning_history.availability_mode(p)=='AUTO_HISTORY'
    assert sum(planning_history.availability(p))>390
    assert planning_history.availability_mode({**p,'availability_mode':'MANUAL'})=='MANUAL'
    assert planning_history.availability(profile())==[75]*7
    p.update(availability_mode='AUTO_HISTORY',training_days=[0,2,4])
    assert [i for i,v in enumerate(planning_history.availability(p)) if v>0]==[0,2,4]
    parsed=ManagementProfile.model_validate({**p,'discipline':'5000 m','planning_controls':controls(intensity_days=[2])})
    assert parsed.training_days==[0,2,4]


def test_regular_history_skips_automatic_reentry_but_keeps_explicit_coach_choice():
    evidence=planning_controls.volume_history(history(),TODAY,0)
    assert planning_history.reentry(profile(),evidence)==(0,'CONTINUING_OBSERVED_TRAINING')
    assert planning_history.reentry(profile(reentry_days=7),evidence)[0]==7
    assert planning_history.reentry(profile(),planning_controls.volume_history(history(9),TODAY,0))[0] is None


def test_auto_week_has_no_template_or_history_wave_cap_and_keeps_canonical_gates(monkeypatch):
    monkeypatch.setattr(engine.model_service,'speed_view',reference_speed)
    repo=Repository(); original=deepcopy(repo.envelope)
    p=profile(available_minutes=planning_history.LEGACY_AVAILABILITY,planning_controls=controls())
    result=engine.generate_plan(repo,'athlete',p,start_date=TODAY+timedelta(days=1),now=NOW)
    assert result['parameters']['available_weekly_minutes'] is None
    assert result['parameters']['weekly_minutes_ceiling'] is None
    assert result['parameters']['volume_governor']=='COMPONENT_7_40'
    assert result['periodization']['entry_basis']['days_override']==0
    for day in result['days']:
        if day['session']:
            for zone,load in day['session']['canonical_effective_load'].items():
                assert load <= day['load_budget']['components'][zone]['deficit_effective']+.002
            assert day['readiness_before'][day['session']['zone']]>=90
    manual=engine.generate_plan(repo,'athlete',{**p,'availability_mode':'MANUAL'},start_date=TODAY+timedelta(days=1),now=NOW)
    assert manual['parameters']['available_weekly_minutes']==390
    assert manual['summary']['planned_minutes']<=390
    assert repo.envelope==original


def test_automatic_z4_dose_has_complete_reps_and_does_not_inherit_z5_capacity():
    repo=Repository(); settings=repo.settings
    methods=resolved_methods(profile(planning_controls=controls()))
    method=next(m for m in methods if m['id']=='ONFLOWS-CONTROLLED-Z4-V1')
    method['actual_sport']='NordicSki'
    cap=engine.capacity_for(method,settings,None,(None,[],['NO_INDIVIDUAL_SPEED_CURVE']),TODAY)
    assert cap['effort_profile']['sport']=='NordicSki'
    blocks=engine._blocks(method,min(cap['capacity_minutes']*.4,method['max_work_min']),cap,settings)
    works=[b for b in blocks if b['kind']=='WORK'];rests=[b for b in blocks if b['kind']=='RECOVERY']
    assert 3 <= len(works) <= 6 and len(rests)==len(works)-1
    assert all(b['target_hr_bpm'] is None and b['reserve_repetitions']==2 for b in works)
    assert all(b['duration_min']==2 for b in rests)
    assert sum(b['duration_min'] for b in works)<=cap['capacity_minutes']*.4
    z5=next(m for m in methods if m['zone']=='Z5')
    assert engine.capacity_for(z5,settings,None,(None,[],[]),TODAY) is None


def test_z5_requires_recent_test_above_individually_supported_z4_boundary():
    from types import SimpleNamespace
    method=next(m for m in resolved_methods(profile(planning_controls=controls())) if m['zone']=='Z5')
    test=dict(duration_s=300.,speed_kmh=24.,day=TODAY.isoformat(),maximal=True,test_mode='STRICT')
    predictor=SimpleNamespace(metadata=lambda hr:{'hr_prediction_source':'INDEX'},speed_for_hr=lambda hr:20.)
    speed=dict(index_window={'last_activity_date':TODAY.isoformat()},index_summary={'Z4':{'count':4}},model_version='test')
    cap=engine.capacity_for(method,Repository().settings,speed,(predictor,[test],[]),TODAY)
    assert cap['capacity_source']=='SPEED_DURATION_TEST_ANCHOR' and cap['capacity_minutes']==5
    assert cap['target_hr_bpm'] is None and cap['target_speed_kmh']==24
    stale={**test,'day':(TODAY-timedelta(days=43)).isoformat()}
    assert engine.capacity_for(method,Repository().settings,speed,(predictor,[stale],[]),TODAY) is None
    assert engine.capacity_for(method,Repository().settings,speed,(predictor,[{**test,'speed_kmh':19}],[]),TODAY) is None
