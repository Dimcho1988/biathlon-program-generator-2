"""A display boundary must not divide a dose or hide a provisional commitment."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from apps.api.planning_search import improve, reservation_methods, score
from biathlon import planning_allocation, planning_schedule
from biathlon.constants import COMPONENTS
from tests.api.test_management_schedule import body, run
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness
from tests.api.test_training_plan_engine import TODAY, Repository


def test_visible_week_expands_only_to_its_anchored_cycles_and_program_bounds():
    anchor = date(2026, 9, 22)
    start, end = date(2026, 10, 17), date(2026, 10, 23)
    assert planning_schedule.microcycle_bounds(start, end, anchor, anchor, date(2026, 12, 1)) == (
        date(2026, 10, 13), date(2026, 10, 26))
    assert planning_schedule.microcycle_bounds(start, end, anchor, start, end) == (start, end)


def test_lookahead_repeats_slots_without_increasing_any_rolling_week():
    p = body(sessions_per_week=9, sessions_by_day=[2,2,2,2,2,1,0], double_threshold_days=[1,4])
    schedule = planning_schedule.slots(p, [600]*7, TODAY, TODAY+timedelta(days=12))
    counts = {d:n for d,_,n in schedule}
    assert sum(n for d,n in counts.items() if d < TODAY+timedelta(days=7)) == 9
    assert sum(counts.values()) > 9
    for d in counts:
        assert sum(n for other,n in counts.items() if d-timedelta(days=6) <= other <= d) <= 9
        assert counts[d] <= p['planning_controls']['sessions_by_day'][d.weekday()]


def test_full_cycle_counts_execution_before_visible_start_and_keeps_other_cycle_separate():
    start = TODAY-timedelta(days=3)
    windows = {start+timedelta(days=i):{z:{'target':70., 'target_weekly_q':7.} for z in COMPONENTS}
               for i in range(14)}
    keys = {d:(d-start).days//7 for d in windows}
    source = {'activities':[{'date':start.isoformat(),'zones':[{'zone':z,'equivalent_time_min':2.} for z in COMPONENTS]}]}
    first = planning_allocation.segment_headroom(windows, [], [], source, [], TODAY, segment_keys=keys)
    assert first['days'] == 7
    assert first['components']['Z5']['remaining'] == 5.  # full 7 Q minus execution, not 4/7
    second = planning_allocation.segment_headroom(windows, [], [], source, [], start+timedelta(days=7), segment_keys=keys)
    assert second['components']['Z5']['remaining'] == 7.


def test_engine_exposes_seven_days_and_reports_complete_cycle_loads_and_rolling_limits(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    p = body(sessions_per_week=7, sessions_by_day=[1]*7,
             mesocycle_anchor=(TODAY-timedelta(days=3)).isoformat(),
             accent_mode='MANUAL', accents=['Z1'], wave=[1.,1.,1.,.78], weekly_target_hours=4.)
    p['component_targets_weekly'] = {z:180. if z=='Z1' else 0. for z in COMPONENTS}
    p['max_key_sessions_per_week'] = 0
    original = deepcopy(p)
    plan = run(monkeypatch,p)
    assert p == original
    assert plan['end_date'] == (TODAY+timedelta(days=6)).isoformat()
    assert len(plan['days']) == 7 and len(plan['lookahead_days']) == 4
    assert all(d['date'] > plan['end_date'] and d['provisional'] for d in plan['lookahead_days'])
    assert plan['allocation']['scope'] == 'WHOLE_MICROCYCLES'
    assert all(s['days']==7 for s in plan['allocation']['segments'])
    days = plan['days']+plan['lookahead_days']
    sessions = [s for d in days for s in d['sessions']]
    assert plan['allocation']['components']['Z1']['planned_q'] == pytest.approx(
        sum(s['direct_equivalent_minutes']['Z1'] for s in sessions),abs=.01)
    assert plan['summary']['planned_minutes'] == pytest.approx(
        sum(s['total_minutes'] for d in plan['days'] for s in d['sessions']),abs=.01)
    for d in days:
        right=date.fromisoformat(d['date']);left=(right-timedelta(days=6)).isoformat()
        window=[s for other in days if left <= other['date'] <= d['date'] for s in other['sessions']]
        assert len(window) <= 7
        assert sum(s['total_minutes'] for s in window) <= 240.01
        for z in COMPONENTS:
            assert sum(s['canonical_effective_load'][z] for s in d['sessions']) <= d['load_budget']['components'][z]['deficit_effective']+.01
    for segment in plan['allocation']['segments']:
        assert not any(row['planned_exceeds_target'] or row['planned_effective_exceeds_target'] for row in segment['components'].values())


def test_search_reclaims_unused_reservations_by_causal_replay_including_tail():
    baseline={'days':[{'date':'2026-10-20','sessions':[
        {'method_id':'S40','sport':'NordicSki','zone':'Z5','slot':1,'is_key_session':True,'main_work_minutes':8/3}]}],
        'lookahead_days':[{'date':'2026-10-24','sessions':[
            {'method_id':'Z4_ONLY','sport':'NordicSki','zone':'Z4','slot':1,'is_key_session':True}]},
            {'date':'2026-10-25','sessions':[]}],
        'allocation':{'components':{'Z5':{'target':5.128,'actual':0,'planned':4.,'remaining':1.128}}}}
    calls=[]
    def replay(policy):
        calls.append(policy)
        updated=deepcopy(baseline)
        actual=policy['reservation_methods']['2026-10-24'][0]
        assert actual['method_id']=='Z4_ONLY'  # replace the earlier guessed Z5 future method
        assert policy['reservation_methods']['2026-10-25']==[]  # no imaginary task on a rest day
        updated['allocation']['components']['Z5'].update(planned=5.,remaining=.128)
        return updated
    result=improve(baseline,{'planning_controls':{'accents':['Z5']}},replay)
    assert result['allocation']['components']['Z5']['planned']==5.
    assert result['allocation']['search']['reservation_replays']==2
    assert result['allocation']['search']['hard_limit_proven'] is False
    assert baseline['allocation']['components']['Z5']['planned']==4.
    assert reservation_methods(baseline)['2026-10-24'][0]['method_id']=='Z4_ONLY'


def test_search_does_not_credit_previous_cycle_overshoot_to_the_next_cycle():
    plan={'allocation':{'components':{'Z5':{'target':120.,'actual':100.,'planned':20.}},
        'segments':[{'components':{'Z5':{'target':60.,'actual':100.,'planned':0.}}},
                    {'components':{'Z5':{'target':60.,'actual':0.,'planned':20.}}}]}}
    assert score(plan,{'Z5'})[0] == pytest.approx(2/3,abs=1e-5)
    plan['allocation']['segments'][1]['components']['Z5']['planned']=60.
    assert score(plan,{'Z5'})[0] == 1.


def test_unmeasured_race_in_tail_blocks_later_forecast_without_invalidating_visible_week(monkeypatch):
    fixed_readiness(monkeypatch,100.)
    repo=Repository()
    race=TODAY+timedelta(days=7)
    repo.events.append({'event_type':'CONTROL_RACE','event_id':'tail-race','name':'Test',
                        'start_date':race.isoformat(),'end_date':race.isoformat()})
    p=body(mesocycle_anchor=(TODAY-timedelta(days=3)).isoformat())
    plan=run(monkeypatch,p,repo)
    assert plan['status']=='DRAFT'
    assert plan['activation_eligible'] is True
    assert plan['allocation']['forecast_complete'] is False
    assert any(d['status']=='RACE' for d in plan['lookahead_days'])
    assert all(not d['sessions'] for d in plan['lookahead_days'] if d['date']>race.isoformat())
