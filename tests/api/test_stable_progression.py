"""Regression scenarios for stable references, Q limits and Z5 unit changes."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from apps.api import training_plan_engine as engine
from apps.api.management_schemas import LoadProgression
from biathlon import load_progression as policy
from biathlon.equivalence import EQUIVALENCE_VERSION
from intervals_inspector.shadow_model import configuration_with_hr_boundaries, profile_from_configuration
from intervals_inspector.onflows_intrazone_load import calculate_onflows_intrazone_load
from intervals_inspector.tests.test_onflows_intrazone_load import _result
from tests.api.test_load_progression import configured, observed
from tests.api.test_training_plan_engine import TODAY, NOW


def phases(p):
    return engine.build_periodization(p['program_start'], p['program_end'], [], reentry_days_override=0, taper_days=0)


def test_reference_survives_rotation_loss_of_history_and_repeated_reads():
    p=configured(); _,source,rows=observed()
    original=deepcopy(source)
    first=policy.context(p,source,rows,TODAY,periodization=phases(p))
    for a in source['activities']:
        for z in a['zones']: z['equivalent_time_min'] *= .2
    changed=policy.context(p,source,rows,TODAY+timedelta(days=28),retained=first['anchor'],periodization=phases(p))
    assert changed['anchor']==first['anchor'] and changed['anchor_reused']
    later=policy.context(p,{'quality':{'excluded_activities':1}},[],TODAY+timedelta(days=110),retained=changed['anchor'])
    assert later['anchor']==first['anchor'] and later['history_usable'] is False
    assert later['components']['Z5']['current_observed_q'] is None
    assert original != source  # Only the explicit test mutation changed actual data.
    snapshot=deepcopy(source)
    assert policy.context(p,source,rows,TODAY,retained=first['anchor'])['anchor']==first['anchor']
    assert source==snapshot


def test_reset_and_hr_changes_invalidate_the_reference_but_weekly_time_cap_does_not():
    p=configured();_,source,rows=observed()
    first=policy.context(p,source,rows,TODAY,physiology={'bounds':[100,120,140,160,180,200]})
    p['planning_controls']['weekly_target_hours']=1
    same=policy.context(p,source,rows,TODAY,retained=first['anchor'],physiology={'bounds':[100,120,140,160,180,200]})
    assert same['anchor_reused']
    changed=policy.context(p,source,rows,TODAY,retained=first['anchor'],physiology={'bounds':[100,120,140,160,175,200]})
    assert not changed['anchor_reused']
    p['load_progression']['reference_revision']=1
    reset=policy.context(p,source,rows,TODAY,retained=first['anchor'],physiology={'bounds':[100,120,140,160,180,200]})
    assert not reset['anchor_reused']


def test_low_observed_z5_gets_expert_destination_without_immediate_jump():
    p=configured();_,source,rows=observed()
    p['planning_controls']['accents']=['Z5']
    p['load_progression']['component_reference_positions']={'Z5':.8}
    for a in source['activities']:
        next(z for z in a['zones'] if z['zone']=='Z5')['equivalent_time_min']=2/7
    ctx=policy.context(p,source,rows,TODAY,periodization=phases(p))
    c=ctx['components']['Z5']
    assert c['expert_reference_q']==25  # 5 + .8*(30-5), all in direct Q.
    assert c['reference_q']==25 and c['weekly_q'] < 2.01
    assert c['target_q']>=25 and c['attainable_q']<3
    assert c['limitation']=='GRADUAL_APPROACH_TO_EXPERT_REFERENCE'
    goals,_,_=engine._goals(p,TODAY,'GENERAL_PREPARATION',False,{},None,0,4,rows,TODAY,False,1,ctx)
    assert goals['Z5']['target_weekly_q']<3
    assert ctx['components']['STR']['expert_reference_q'] is None


def test_missing_or_zero_history_never_becomes_expert_actual_work():
    p=configured();_,source,rows=observed()
    for a in source['activities']:
        next(z for z in a['zones'] if z['zone']=='Z5')['equivalent_time_min']=0
    ctx=policy.context(p,source,rows,TODAY,periodization=phases(p))
    assert ctx['components']['Z5']['weekly_q']==0
    assert ctx['components']['Z5']['attainable_q']==0
    missing=policy.context(p,{'quality':{'excluded_activities':1}},[],TODAY,periodization=phases(p))
    assert missing['components']['Z5']['reference_q']>=5
    assert missing['components']['Z5']['weekly_q'] is None
    assert missing['components']['Z5']['attainable_q'] is None
    assert missing['components']['STR']['reference_q'] is None


def test_three_whole_cycles_use_median_including_unloading():
    p=configured();_,source,rows=observed()
    source={'activities':[], 'quality':{}}
    rows=[]
    for i in range(84):
        d=(TODAY-timedelta(days=i+1)).isoformat()
        q=[10,20,900][i//28]
        source['activities'].append({'date':d,'sport':'Run','duration_min':60,'zones':[{'zone':z,'raw_time_min':q,'equivalent_time_min':q} for z in policy.WEEKLY_Q_BOUNDS]})
        rows += [{'date':d,'zone':z,'effective_load':q} for z in engine.COMPONENTS]
    ctx=policy.context(p,source,rows,TODAY)
    assert len(ctx['anchor']['windows'])==3
    assert ctx['components']['Z5']['weekly_q']==140
    assert ctx['components']['Z5']['annual_rate_percent']==0


def test_trajectory_freezes_during_recovery_and_never_compresses_annual_growth():
    p=configured();_,source,rows=observed()
    periodization={'phases':[{'kind':'GENERAL_PREPARATION','start_date':TODAY.isoformat(),'end_date':p['program_end']}],'taper_windows':[]}
    ctx=policy.context(p,source,rows,TODAY,periodization=periodization)
    path=ctx['trajectory']
    assert 1<path[(TODAY+timedelta(days=20)).isoformat()]['Z3']<1.03
    assert path[(TODAY+timedelta(days=27)).isoformat()]['Z3']==path[(TODAY+timedelta(days=20)).isoformat()]['Z3']
    assert all(v['STR']==1 for v in path.values())
    assert path[(TODAY+timedelta(days=20)).isoformat()]['Z5']==1  # Not this cycle's focus.


def test_direct_q_budget_counts_actual_and_multiple_proposals_once():
    source={'activities':[{'date':TODAY.isoformat(),'sport':'Run','zones':[{'zone':'Z5','equivalent_time_min':3}]}]}
    planned=[{'date':TODAY.isoformat(),'status':'TRAINING','sessions':[{'direct_equivalent_minutes':{'Z5':2}},{'direct_equivalent_minutes':{'Z5':1}}]}]
    assert policy.remaining_q(source,planned,TODAY,{'Z5':{'target_weekly_q':10}})=={'Z5':4}
    source['activities'][0]['zones'][0]['equivalent_time_min']=None
    assert policy.remaining_q(source,planned,TODAY,{'Z5':{'target_weekly_q':10}})=={'Z5':0}


@pytest.mark.parametrize('hr,expected', [(180,5),(190,7.5),(200,10),(210,10)])
def test_z5_actual_integrator_and_planner_have_identical_five_percent_units(hr,expected):
    settings=SimpleNamespace(zone_bounds_bpm=(100,125,145,160,180,200),hrmax_bpm=200)
    profile=profile_from_configuration(configuration_with_hr_boundaries(settings.zone_bounds_bpm,hrmax_bpm=200))
    observed=calculate_onflows_intrazone_load(_result(list(range(301)),[hr]*301),profile)
    z5=next(z for z in observed['zones'] if z['zone']=='Z5')
    direct,_,_=engine._canonical_load([{'zone':'Z5','duration_min':5,'target_hr_bpm':hr}],settings,[],TODAY)
    assert z5['equivalent_minutes']==pytest.approx(expected)
    assert direct['Z5']==pytest.approx(expected)
    assert [z.equivalence_slope_pp_per_bpm for z in profile.zones]==[3,3,3,3,5]


def test_new_z5_units_require_reanalysis_before_new_prescriptions():
    repo,source,_=observed();source.update(schema_version='load-history-v2',equivalence_version='intra_zone_linear_v1')
    result=engine.generate_plan(repo,'athlete',configured(),start_date=TODAY,now=NOW)
    assert result['activation_eligible'] is False
    assert not any(d['sessions'] for d in result['days'])
    assert any(w['code']=='EQUIVALENCE_REANALYSIS_REQUIRED' for w in result['warnings'])
    assert result['parameters']['load_progression']['components']['Z5']['weekly_q'] is None


def test_reference_profile_positions_are_bounded():
    with pytest.raises(ValueError): LoadProgression(component_reference_positions={'Z5':1.01})
    with pytest.raises(ValueError): LoadProgression(component_reference_positions={'STR':.8})


def test_first_valid_component_observation_fills_only_the_missing_reference():
    p=configured();_,source,rows=observed()
    original=deepcopy(source)
    for a in source['activities']:
        next(z for z in a['zones'] if z['zone']=='Z5')['equivalent_time_min']=None
    first=policy.context(p,source,rows,TODAY)
    assert first['components']['Z5']['weekly_q'] is None
    repaired=policy.context(p,original,rows,TODAY+timedelta(days=1),retained=first['anchor'])
    assert repaired['components']['Z5']['weekly_q'] is not None
    assert repaired['components']['Z1']['weekly_q']==first['components']['Z1']['weekly_q']
    assert repaired['components']['Z5']['established_on']==(TODAY+timedelta(days=1)).isoformat()


def test_changed_zone_bounds_require_reanalysis_even_with_current_equivalence_version():
    source={'schema_version':'load-history-v2','equivalence_version':EQUIVALENCE_VERSION,
            'zone_bounds_bpm':[100,120,140,160,180,200],'hrmax_bpm':200}
    assert policy.history_matches(source,{'bounds':[100,120,140,160,180,200],'hrmax':200})
    assert not policy.history_matches(source,{'bounds':[100,120,140,160,175,200],'hrmax':200})
