from copy import deepcopy
from datetime import date,timedelta

import pytest

from apps.api import recovery_policy as policy
from biathlon import individual_learning as learning
from biathlon.constants import COMPONENTS

START=date(2026,9,1)


def fixture(length=14, returned=3, *, performance=105., follow_days=10, elevated=True):
    load_end=START+timedelta(days=length-1)
    today=load_end+timedelta(days=follow_days+1)
    rows,activities,strength,days,entries=[],[],[],[],[]
    for n in range(-28,length+follow_days):
        day=(START+timedelta(days=n)).isoformat()
        loaded=0<=n<length
        q=20. if loaded else 10. if n<0 else 5.
        zones=[{"zone":z,"equivalent_time_min":q,"raw_time_min":q} for z in COMPONENTS if z!="STR"]
        activities.append({"date":day,"sport":"Run","zones":zones})
        strength.append({"date":day,"real_time_min":0.,"equivalent_time_min":0.})
        for z in COMPONENTS:
            rows.append({"date":day,"zone":z,"effective_load":q if z!="STR" else 0.})
        unreturned=loaded or (n>=length and (returned is None or n-length+1<returned))
        high=elevated and unreturned
        channels=[{"key":"resting_hr","raw":82. if high else 60.,"score":95. if high else 50.},
                  {"key":"hrv","raw":20. if high else 60.,"score":95. if high else 50.},
                  # An excellent functional score cannot manufacture return.
                  {"key":"performance","score":0.,"raw":1.}]
        days.append({"day":day,"channels":channels})
    if returned is not None and performance is not None:
        for dt,val in [(START-timedelta(days=2),100.),(load_end+timedelta(days=returned+1),performance)]:
            p={"day":dt.isoformat(),"protocol":"standard run","protocol_version":"1","unit":"W","direction":"HIGHER","conditions":"same protocol","value":val,"comparable":True,"components":["Z1"],"meaningful_change_percent":1.}
            entries.append({"kind":"TEST","entry_key":p["day"],"revision":1,"payload":p})
    return {"source":{"period_start":(START-timedelta(days=28)).isoformat(),"period_end":today.isoformat(),"activities":activities,"strength":{"daily":strength},"quality":{}},"rows":rows,"history":{"days":days,"sessions":[]},"entries":entries,"trainability":[],"today":today,"context_key":"test"}


def build(**kwargs):
    return policy.build_view_data(**fixture(**kwargs))


@pytest.mark.parametrize('days,expected',[(7,3),(14,5),(21,7)])
def test_expert_limits(days,expected):
    assert policy.recovery_limit(days)==expected
    assert policy.proposed_step(days,expected)==0


def test_branch_bounds_and_asymmetry():
    assert policy.proposed_step(14,3)==pytest.approx(.08)
    assert policy.proposed_step(14,6)==pytest.approx(-.08)
    assert policy.proposed_step(14,7.5)==-.20
    assert policy.proposed_step(14,3,.125)==pytest.approx(.01)
    assert policy.recovery_limit(6) is None
    assert policy.recovery_limit(22) is None


def test_actual_blocks_replace_calendar_grid_and_return_uses_first_confirming_day():
    evidence=build()
    assert len(evidence['episodes'])==1
    episode=evidence['episodes'][0]
    assert episode['start']==str(START)
    assert episode['loading_days']==14
    assert episode['response']['recovery_days']==3
    assert episode['response']['returned_on']==str(START+timedelta(days=16))
    assert episode['outcomes'][0]['change_percent']==pytest.approx(5.)
    assert 'performance' not in episode['response']['channels']
    assert evidence['recovery_policy']['episodes'][0]['ratio']==.6


def test_device_panel_works_without_functional_or_subjective_fill():
    evidence=build()
    response=evidence['episodes'][0]['response']
    assert response['channels']==['hrv','resting_hr']
    assert response['nominal_coverage_percent']==15
    assert evidence['current']['independent_channel_count']==2
    assert evidence['current']['coverage']==100


def test_delayed_return_is_censored_not_assigned_fake_recovery_time():
    evidence=build(returned=None)
    e=evidence['episodes'][0]
    assert e['response']['recovery_days'] is None
    assert e['response']['right_censored']
    assert e['outcomes']==[]
    assert evidence['recovery_policy']['episodes'][0]['status']=='DELAYED'


def test_no_stress_rise_is_not_zero_day_recovery_or_positive_credit():
    evidence=build(elevated=False)
    assert evidence['episodes'][0]['response']['recovery_days'] is None
    assert not evidence['episodes'][0]['response']['right_censored']
    assert evidence['recovery_policy']['episodes'][0]['status']=='NO_ELEVATED_RESPONSE'


def test_old_limited_activity_does_not_veto_valid_later_block():
    data=fixture()
    data['source']['quality']={'limited_activities':1}
    # The bad date is outside this loading block and its baseline.
    activity=data['source']['activities'][0]
    activity['quality_status']='limited'
    evidence=policy.build_view_data(**data)
    assert len(evidence['episodes'])==1
    assert evidence['recovery_policy']['observations']['excluded_days']==1
    # The same incomplete record inside the relevant exposure invalidates it.
    data=fixture()
    next(a for a in data['source']['activities'] if a['date']==str(START+timedelta(days=3)))['quality_status']='limited'
    assert policy.build_view_data(**data)['episodes']==[]


def test_manual_block_also_requires_actual_downshift():
    data=fixture()
    # A false claimed earlier endpoint is still inside actual high training.
    p={'start':str(START),'load_end':str(START+timedelta(days=6)),'recovery_end':str(START+timedelta(days=13)),'phase':'BUILD','components':['Z1']}
    data['entries'].append({'kind':'BLOCK','entry_key':str(START),'revision':1,'payload':p})
    result=policy.build_view_data(**data)
    assert len(result['episodes'])==1
    assert result['episodes'][0]['load_end']==str(START+timedelta(days=13))


def decision_data(returned=3,performance=105):
    data=fixture(returned=returned,performance=performance)
    evidence=policy.build_view_data(**data)
    models=learning.fit(evidence['episodes'],data['today'])
    report={'mode':'CONTROL','version':learning.VERSION,'components':{},'validation':models['GLOBAL']['validation']}
    return data,evidence,models,report


def test_worsening_performance_blocks_increase():
    data,evidence,models,report=decision_data(performance=95)
    policy.apply_decision(report,evidence,models,config={'mode':'CONTROL','max_volume_step_percent':20},allowed_components=['Z1'],phase='GENERAL_PREPARATION',taper=False,today=data['today'])
    assert report['decision'] is None
    assert report['components']['Z1']['volume_factor']==1


def test_delayed_recovery_can_reduce_and_shadow_does_not_apply():
    data,evidence,models,report=decision_data(returned=None)
    policy.apply_decision(report,evidence,models,config={'mode':'SHADOW','max_volume_step_percent':20},allowed_components=['Z1'],phase='GENERAL_PREPARATION',taper=False,today=data['today'])
    assert report['components']['Z1']['proposed_volume_factor']==.8
    assert report['components']['Z1']['volume_factor']==1
    assert report['recovery_policy']['decision']['applied_change_percent']==0
    assert report['decision']['baseline_actual_weekly_q']==140
    saved=deepcopy(report['decision'])
    data2,evidence2,models2,report2=decision_data(returned=None)
    policy.apply_decision(report2,evidence2,models2,config={'mode':'SHADOW','max_volume_step_percent':20},allowed_components=['Z1'],phase='GENERAL_PREPARATION',taper=False,today=data['today'],retained={'decision':saved})
    assert report2['decision']['baseline_actual_weekly_q']==140
    assert report2['components']['Z1']['proposed_volume_factor']==.8


def test_supported_boundary_hold_has_actual_baseline_and_no_percentage():
    data,evidence,models,report=decision_data(returned=5)
    policy.apply_decision(report,evidence,models,config={'mode':'CONTROL','max_volume_step_percent':20},allowed_components=['Z1'],phase='GENERAL_PREPARATION',taper=False,today=data['today'])
    assert report['decision']['step']==0
    assert report['components']['Z1']['action']=='HOLD'
    assert report['components']['Z1']['baseline_actual_weekly_q']==140


def test_out_of_local_support_cannot_anchor_next_target():
    data,evidence,models,report=decision_data(returned=None)
    policy.apply_decision(report,evidence,models,config={'mode':'CONTROL','max_volume_step_percent':20},allowed_components=['Z1'],phase='GENERAL_PREPARATION',taper=False,today=data['today'],supported_episodes=[])
    assert report['decision'] is None
    assert report['components']['Z1']['volume_factor']==1


def test_expired_decision_marker_keeps_same_exposure_closed_on_later_refreshes():
    data,evidence,models,report=decision_data(returned=None)
    config={'mode':'CONTROL','max_volume_step_percent':20}
    previous={'source_episode_id':evidence['episodes'][0]['id'],'component':'Z1','mode':'CONTROL','config':config,'expires_on':str(data['today']-timedelta(days=1))}
    for _ in range(2):
        policy.apply_decision(report,evidence,models,config=config,allowed_components=['Z1'],phase='GENERAL_PREPARATION',taper=False,today=data['today'],retained={'decision':previous})
        assert report['decision'] is None
        # This is the service persistence rule: a neutral view retains marker.
        previous=deepcopy(report.get('decision') or previous)
        assert previous['source_episode_id']==evidence['episodes'][0]['id']


def test_config_or_mode_change_does_not_restart_observation():
    data,evidence,models,report=decision_data(returned=None)
    previous={'source_episode_id':evidence['episodes'][0]['id'],'component':'Z1','mode':'SHADOW','config':{'mode':'SHADOW','max_volume_step_percent':20},'expires_on':str(data['today']+timedelta(days=5))}
    policy.apply_decision(report,evidence,models,config={'mode':'CONTROL','max_volume_step_percent':20},allowed_components=['Z1'],phase='GENERAL_PREPARATION',taper=False,today=data['today'],retained={'decision':previous})
    assert report['decision'] is None


def test_missing_member_of_fallback_graph_panel_creates_gap():
    data=fixture()
    # No observed dose rise/downshift means graph uses a fixed fallback panel.
    for a in data['source']['activities']:
        for z in a['zones']:
            z['equivalent_time_min']=10.
            z['raw_time_min']=10.
    missing=data['history']['days'][-2]
    missing['channels']=[c for c in missing['channels'] if c['key']!='hrv']
    evidence=policy.build_view_data(**data)
    assert evidence['recovery_policy']['episodes']==[]
    assert next(r for r in evidence['recovery_policy']['series'] if r['day']==missing['day'])['stress'] is None


def test_new_models_include_duration_and_followup_context_without_changing_action_coordinates():
    episode=build()['episodes'][0]
    features=learning.features(episode)
    assert len(features)==len(learning.FEATURES)+1
    assert features[learning.FEATURES.index('context:loading_duration')+1]==0
    assert features[learning.FEATURES.index('context:followup_load')+1]<0
    contrast=learning.action_contrast('Z1','volume',.1,{'Z1':{'weekly_q':140.,'weekly_minutes':140.}})
    assert contrast[-2:]==[0.,0.]


def test_unidentified_exclusion_cannot_be_invented_as_a_covered_rest_day():
    data=fixture()
    data['source']['quality']['excluded_activities']=1
    result=policy.build_view_data(**data)
    assert result['episodes']==[]
    assert any(e['reason']=='UNKNOWN_EXCLUDED_DATES' for e in result['exclusions'])
    assert all(r['actual_q'] is None for r in result['recovery_policy']['series'])


def test_unreturned_policy_keeps_current_unloading_ceiling_separate_from_macro_change():
    data,evidence,models,report=decision_data(returned=None)
    policy.apply_decision(report,evidence,models,config={'mode':'CONTROL','max_volume_step_percent':20},allowed_components=['Z1'],phase='GENERAL_PREPARATION',taper=False,today=data['today'])
    assert report['decision']['step']==-.20
    assert report['decision']['execution_weekly_q_ceiling']==35.
    assert report['components']['Z1']['baseline_actual_weekly_q']==140.


def test_unreturned_policy_abstains_when_current_unloading_dose_is_unknown():
    data,evidence,models,report=decision_data(returned=None)
    evidence['_recent_window']['complete']=False
    policy.apply_decision(report,evidence,models,config={'mode':'CONTROL','max_volume_step_percent':20},allowed_components=['Z1'],phase='GENERAL_PREPARATION',taper=False,today=data['today'])
    assert report['decision'] is None


def test_active_component_change_cannot_start_second_experiment_from_same_episode():
    data,evidence,models,report=decision_data(returned=None)
    config={'mode':'CONTROL','max_volume_step_percent':20}
    previous={'source_episode_id':evidence['episodes'][0]['id'],'component':'Z1',
              'kind':'volume','mode':'CONTROL','config':deepcopy(config),
              'effective_from':str(data['today']-timedelta(days=2)),
              'expires_on':str(data['today']+timedelta(days=11)),
              'baseline_actual_weekly_q':140.,'step':-.2}
    original=deepcopy(previous)
    policy.apply_decision(report,evidence,models,config=config,allowed_components=['Z3'],
                          phase='GENERAL_PREPARATION',taper=False,today=data['today'],retained={'decision':previous})
    assert report['decision'] is None
    assert all(c['volume_factor']==1. and c['proposed_volume_factor']==1. for c in report['components'].values())
    assert report['recovery_policy']['decision']['component'] is None
    assert report['recovery_policy']['decision']['proposed_change_percent']==0
    assert previous==original
    # Restoring the original eligible component keeps its old observation window.
    policy.apply_decision(report,evidence,models,config=config,allowed_components=['Z1'],
                          phase='GENERAL_PREPARATION',taper=False,today=data['today'],retained={'decision':previous})
    assert report['decision']['component']=='Z1'
    assert report['decision']['effective_from']==previous['effective_from']
    assert report['decision']['expires_on']==previous['expires_on']
    assert report['decision']['baseline_actual_weekly_q']==140.
