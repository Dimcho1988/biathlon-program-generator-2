from copy import deepcopy

from apps.api import training_plan_engine as engine
from tests.api.test_feasible_quality_reservations import selected_methods
from tests.api.test_load_progression import configured, observed
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness
from tests.api.test_training_plan_engine import TODAY, NOW


def test_speed_exposure_selects_the_same_whole_intervals_as_hr_exposure(monkeypatch):
    fixed_readiness(monkeypatch,100.)
    body=configured(age_years=25,training_experience_years=5,available_minutes=[180]*7)
    body['planning_controls'].update(accents=['Z4'],sessions_per_week=3,
        sessions_by_day=[1,0,1,0,1,0,0],mixed_sessions_enabled=False)
    selected_methods(monkeypatch,body,{'ONFLOWS-CONTROLLED-Z4-V2','END-CROSS-TRAIN-01-Z2','RUN-REC-EASY-01'})
    repo,_,_=observed()
    baseline=engine.generate_plan(repo,'athlete',body,start_date=TODAY,now=NOW)
    prepare=engine.planning_source_details
    def speed_source(*args,**kwargs):
        result=prepare(*args,**kwargs)
        measured=deepcopy(result.measured_source)
        measured['load_selection']={'leading_indicator':'SPEED'}
        for a in measured['activities']:
            active=sum(z['raw_time_min'] for z in a['zones'])*60
            a.update(quality_status='limited',hr_coverage_percent=0.,
                load_source={'recorded_seconds':active,'active_seconds':active,
                             'rest_seconds':0.,'unknown_seconds':0.,'source_seconds':{'SPEED':active}})
        return engine.PlanningSourceResult(result.source,result.evidence,measured)
    monkeypatch.setattr(engine,'planning_source_details',speed_source)
    alternative=engine.generate_plan(repo,'athlete',body,start_date=TODAY,now=NOW)
    def schedule(p):
        return [(d['date'],[(s['method_id'],s['total_minutes'],s['direct_equivalent_minutes'])
                           for s in d['sessions']]) for d in [*p['days'],*p.get('lookahead_days',[])]]
    assert any(s['method_id']=='ONFLOWS-CONTROLLED-Z4-V2' for d in baseline['days'] for s in d['sessions'])
    assert schedule(alternative) == schedule(baseline)
