from copy import deepcopy
from datetime import timedelta

import pytest
from pydantic import ValidationError

from apps.api import training_plan_engine as engine, management_service
from apps.api.management_schemas import TrainingReturn, ManagementProfile
from types import SimpleNamespace
from biathlon import training_return as policy, load_progression
from biathlon.component_load import calculate_component_load
from tests.api.test_load_progression import configured
from tests.api.test_training_plan_engine import TODAY, NOW, Repository, supported_speed


def fixture(gap=30, h=(600.,180.,75.,30.,30.)):
    p=configured();p['discipline']='5000 m'
    p['planning_controls'].update(accent_mode='AUTO', sessions_per_week=7, automatic_intervals=True)
    p['training_return']={'mode':'CONFIRMED_BREAK','pause_start':(TODAY-timedelta(days=gap)).isoformat(),
                          'resume_date':TODAY.isoformat(),'historical_weekly_q':{}}
    repo=Repository();source=repo.envelope['snapshot_payload']['load_history']
    source.update(daily=[],activities=[],strength={'daily':[]},period_start=(TODAY-timedelta(days=84+gap)).isoformat())
    for n in range(84+gap,-1,-1):
        day=(TODAY-timedelta(days=n)).isoformat()
        direct={z:(h[i]/7 if n>gap else 0.) for i,z in enumerate(engine.COMPONENTS[:5])}
        e=calculate_component_load(direct)['effective']
        source['daily'] += [{'date':day,'zone':z,'effective_load':e[z]} for z in engine.COMPONENTS[:5]]
        source['strength']['daily'].append({'date':day,'effective_load':0.,'real_time_min':0.,'equivalent_time_min':0.})
        if n>gap:
            source['activities'].append({'activity_ref':'before-'+day,'date':day,'sport':'Run','duration_min':sum(direct.values()),
                'zones':[{'zone':z,'equivalent_time_min':v,'raw_time_min':v,'effective_load':e[z]} for z,v in direct.items()]})
    repo.envelope['activities']=[{**a,'local_date':a['date']} for a in source['activities']]
    return p,repo,source


def ctx(p,source,today=TODAY,retained=None):
    source=policy.confirmed_source(source,p,today)
    phases=engine.build_periodization(p['program_start'],p['program_end'],[],reentry_days_override=0)
    return load_progression.context(p,source,engine._daily_rows(source,today),today,retained=retained,periodization=phases)


@pytest.mark.parametrize('gap,target,days,weeks',[(14,1.,18.2,3),(30,1.,39.,6),(60,1.,78.,12),(61,.9,78.,12),(180,.9,78.,12)])
def test_start_linear_horizon_and_long_pause_target(gap,target,days,weeks):
    p,repo,source=fixture(gap)
    before=deepcopy(source);c=ctx(p,source);r=c['training_return']
    assert r['target_fraction']==target and r['nominal_days']==pytest.approx(days) and r['weeks']==weeks
    assert r['components']['Z1']['historical_q']==pytest.approx(600)
    assert r['components']['Z1']['initial_q']==pytest.approx(300)
    assert r['components']['Z1']['target_q']==pytest.approx(600*target)
    points=[policy.point(c,'Z1',TODAY+timedelta(days=7*i))['weekly_q'] for i in range(weeks)]
    assert points[0]==pytest.approx(300) and points[-1]==pytest.approx(600*target)
    assert all(b-a==pytest.approx(points[1]-points[0]) for a,b in zip(points,points[1:]))
    assert all(c['trajectory'][(TODAY+timedelta(days=7*i)).isoformat()]['Z1']==1 for i in range(weeks))
    assert source==before


def test_zero_and_low_history_are_not_forced_to_population_floor():
    p,_,source=fixture(90,h=(120.,30.,15.,5.,0.));c=ctx(p,source)
    assert c['training_return']['components']['Z1']['initial_q']==pytest.approx(108)
    assert c['training_return']['components']['Z5']['initial_q']==0
    assert c['training_return']['components']['Z5']['target_q']==0


def test_missing_records_are_not_a_break_and_declaration_conflicts_do_not_erase_training():
    p,_,source=fixture();p.pop('training_return')
    assert 'training_return' not in ctx(p,source)
    p,_,source=fixture();source['activities'][0]['date']=(TODAY-timedelta(days=1)).isoformat()
    before=deepcopy(source);out=policy.confirmed_source(source,p,TODAY)
    assert out['training_return_declaration']['status']=='CONFLICT'
    assert out['activities']==source['activities'] and source==before
    assert not ctx(p,source)['training_return']['supported']


def test_declared_rest_fills_only_its_dates_and_retains_later_unknown_days():
    p,_,source=fixture();source['daily']=[];source['strength']['daily']=[]
    out=policy.confirmed_source(source,p,TODAY+timedelta(days=3))
    assert out['period_end']==(TODAY-timedelta(days=1)).isoformat()
    assert not any(r['date']>=TODAY.isoformat() for r in out['daily'])


def test_stable_history_is_median_of_complete_prebreak_cycles_not_last_peak():
    p,_,source=fixture()
    for a in source['activities']:
        before=(TODAY-timedelta(days=30)-__import__('datetime').date.fromisoformat(a['date'])).days
        factor=10 if before<=28 else 1
        for row in a['zones']:row['equivalent_time_min']*=factor
    assert ctx(p,source)['training_return']['components']['Z1']['historical_q']==pytest.approx(600)


def test_declared_history_and_legacy_anchor_survive_expired_prebreak_records():
    p,_,source=fixture(180);first=ctx(p,source)
    source['activities']=[]
    same=ctx(p,source,retained=first['anchor'])
    assert same['training_return']==first['training_return']
    p['training_return']['historical_weekly_q']=dict(zip(engine.COMPONENTS[:5],[1000,400,150,60,60]))
    changed=ctx(p,source,retained=first['anchor'])
    assert changed['training_return']['components']['Z1']['initial_q']==500
    assert changed['training_return']['components']['Z1']['target_q']==900


def test_unexecuted_weeks_and_repeated_reads_never_accrue_return_or_growth():
    p,_,source=fixture();first=ctx(p,source)
    for n in range(35):
        d=(TODAY+timedelta(days=n)).isoformat()
        if n>0:source['daily'] += [{'date':d,'zone':z,'effective_load':0.} for z in engine.COMPONENTS[:5]]
    source['period_end']=(TODAY+timedelta(days=34)).isoformat()
    later=ctx(p,source,TODAY+timedelta(days=35),first['anchor'])
    assert policy.point(later,'Z1',TODAY+timedelta(days=35))['weekly_q']==pytest.approx(300)
    assert later['training_return']['components']['Z1']['completed_weeks']==0
    repeated=ctx(p,source,TODAY+timedelta(days=35),later['anchor'])
    assert repeated['training_return']==later['training_return']
    assert all(v['Z1']==1 for d,v in later['trajectory'].items() if d<=(TODAY+timedelta(days=35)).isoformat())


def test_completed_return_rebases_development_at_ninety_percent_without_jump():
    p,_,source=fixture(90);first=ctx(p,source)
    for week in range(12):
        for n in range(7):
            d=TODAY+timedelta(days=week*7+n);key=d.isoformat()
            q={z:policy.point(first,z, d)['weekly_q']/7 for z in engine.COMPONENTS[:5]}
            source['daily']=[r for r in source['daily'] if r['date']!=key]
            e=calculate_component_load(q)['effective']
            source['daily'] += [{'date':key,'zone':z,'effective_load':e[z]} for z in engine.COMPONENTS[:5]]
            source['activities'].append({'date':key,'sport':'Run','duration_min':sum(q.values()),'zones':[
                {'zone':z,'raw_time_min':v,'equivalent_time_min':v} for z,v in q.items()]})
    day=TODAY+timedelta(days=84);source['period_end']=(day-timedelta(days=1)).isoformat()
    c=ctx(p,source,day,first['anchor'])
    assert c['training_return']['status']=='COMPLETED'
    assert c['components']['Z1']['reference_q']==pytest.approx(540)
    assert c['components']['Z1']['annual_rate_percent']==pytest.approx(30*(900-540)/(900-240))
    assert 540<=c['components']['Z1']['reference_q']*c['trajectory'][day.isoformat()]['Z1']<541


def test_schema_rejects_bad_dates_and_partial_history():
    with pytest.raises(ValidationError):TrainingReturn(mode='CONFIRMED_BREAK',pause_start=TODAY,resume_date=TODAY)
    with pytest.raises(ValidationError):TrainingReturn(mode='CONFIRMED_BREAK',pause_start=TODAY-timedelta(days=30),resume_date=TODAY,historical_weekly_q={'Z1':600})
    with pytest.raises(ValidationError):TrainingReturn(mode='CONTINUED',historical_weekly_q=dict(zip(engine.COMPONENTS[:5],[float('nan'),1,1,1,1])))


def test_generator_can_restart_and_keeps_daily_budgets_and_manual_time_limit(monkeypatch):
    p,repo,source=fixture(60)
    p['planning_controls'].update(weekly_target_hours=4.)
    original=deepcopy(source)
    monkeypatch.setattr(engine.model_service,'speed_view',lambda repository, alias, sport: {**supported_speed(repository.athlete_settings(alias)), "sport":sport,"source_generation_id":repository.envelope["generation_id"],"source_revision":repository.envelope["revision"]})
    result=engine.generate_plan(repo,'athlete',p,start_date=TODAY,now=NOW)
    assert result['parameters']['load_progression']['training_return']['supported']
    assert any(d['sessions'] for d in result['days'])
    assert sum(s['total_minutes'] for d in result['days'] for s in d['sessions'])<=240.01
    assert not any(w['code']=='WEEKLY_VOLUME_REQUIRED' for w in result['warnings'])
    assert source==original


def test_live_outlook_and_generator_use_the_same_long_break_reference(monkeypatch):
    p,repo,_=fixture(90)
    repo.active_analysis=lambda _:deepcopy(repo.envelope)
    monkeypatch.setattr(engine.model_service,'speed_view',lambda repository,alias,sport:{**supported_speed(repository.athlete_settings(alias)),
        'sport':sport,'source_generation_id':repo.envelope['generation_id'],'source_revision':repo.envelope['revision']})
    monkeypatch.setattr(management_service,'ManagementStore',lambda _:SimpleNamespace(profile=lambda _: {'configured':True,'revision':1,'profile':deepcopy(p)}))
    plan=engine.generate_plan(repo,'athlete',p,start_date=TODAY,now=NOW)
    outlook=management_service.outlook(repo,'athlete',now=NOW)['outlook']
    assert not outlook['long_term']['limited']
    assert plan['long_term']['progression']['training_return']==outlook['long_term']['progression']['training_return']
    assert plan['long_term']['weeks']==outlook['long_term']['weeks']


def test_future_resume_cannot_schedule_before_the_declared_start(monkeypatch):
    p,repo,_=fixture()
    p['training_return']['resume_date']=(TODAY+timedelta(days=2)).isoformat()
    monkeypatch.setattr(engine.model_service,'speed_view',lambda *args:None)
    result=engine.generate_plan(repo,'athlete',p,start_date=TODAY,now=NOW)
    assert result['status']=='BLOCKED' and not any(d['sessions'] for d in result['days'])
    assert any(w['code']=='TRAINING_RETURN_NOT_STARTED' for w in result['warnings'])


def test_disabled_growth_cannot_silently_ignore_confirmed_return():
    p,_,_=fixture();p['load_progression']['enabled']=False
    with pytest.raises(ValidationError):ManagementProfile.model_validate(p)


def test_recorded_week_corrections_remove_credit_without_erasing_history():
    p,_,source=fixture();first=ctx(p,source)
    for n in range(7):
        key=(TODAY+timedelta(days=n)).isoformat()
        source['daily']=[r for r in source['daily'] if r['date']!=key]
        q={z:first['training_return']['components'][z]['initial_q']/7 for z in engine.COMPONENTS[:5]}
        e=calculate_component_load(q)['effective']
        source['daily'] += [{'date':key,'zone':z,'effective_load':e[z]} for z in q]
        source['activities'].append({'date':key,'sport':'Run','duration_min':sum(q.values()),'zones':[
            {'zone':z,'equivalent_time_min':v,'raw_time_min':v,'effective_load':e[z]} for z,v in q.items()]})
    day=TODAY+timedelta(days=7);source['period_end']=(day-timedelta(days=1)).isoformat()
    credited=ctx(p,source,day,first['anchor'])
    assert credited['training_return']['components']['Z1']['completed_weeks']==1
    source['activities']=[a for a in source['activities'] if a['date']<TODAY.isoformat()]
    for row in source['daily']:
        if row['date']>=TODAY.isoformat():row['effective_load']=0.
    corrected=ctx(p,source,day,credited['anchor'])
    assert corrected['training_return']['components']['Z1']['completed_weeks']==0
    assert policy.point(corrected,'Z1',day)['weekly_q']==pytest.approx(300)


@pytest.mark.parametrize('manual',[False,True])
def test_return_with_expired_history_uses_declared_q_or_expert_start(monkeypatch,manual):
    p,repo,source=fixture(180)
    cutoff=(TODAY-timedelta(days=89)).isoformat()
    source['activities']=[];repo.envelope['activities']=[]
    source['daily']=[r for r in source['daily'] if r['date']>=cutoff]
    source['strength']['daily']=[r for r in source['strength']['daily'] if r['date']>=cutoff]
    source['period_start']=cutoff
    if manual:p['training_return']['historical_weekly_q']=dict(zip(engine.COMPONENTS[:5],[600,180,75,30,30]))
    monkeypatch.setattr(engine.model_service,'speed_view',lambda *args:None)
    result=engine.generate_plan(repo,'athlete',p,start_date=TODAY,now=NOW)
    component=result['parameters']['load_progression']['training_return']['components']['Z1']
    assert component['initial_q']==pytest.approx(300 if manual else 216)
    assert component['target_q']==pytest.approx(540 if manual else 216)
    assert component['source']==('DECLARED_HISTORY' if manual else 'EXPERT_START')
    assert result['status']!='BLOCKED' and any(d['sessions'] for d in result['days'])
    for d in result['days']:
        for z in engine.COMPONENTS:
            assert sum(s['canonical_effective_load'][z] for s in d['sessions'])<=d['load_budget']['components'][z]['deficit_effective']+.01

