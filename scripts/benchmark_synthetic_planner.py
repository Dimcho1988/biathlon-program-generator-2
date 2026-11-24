"""Profile deterministic two-sport planning without stored athlete inputs.

Examples (run from the repository):
    python scripts/benchmark_synthetic_planner.py --catalog source --mode engine
    python scripts/benchmark_synthetic_planner.py --catalog source --mode search
    python scripts/benchmark_synthetic_planner.py --catalog source --profile /tmp/planner.prof

The fixture follows the repository's synthetic tests: 70 synthetic calendar days,
independent calibrated Run/NordicSki curves, and an automatic 14-day horizon.
The source catalogue scenario includes calibrated speed zones and HR settings;
the native scenario intentionally omits them. No data service is contacted.

Wall time and profiler time must be measured separately; cProfile adds overhead.
Full search retains its production 150-second budget. Diagnostic reference runs
may load the five affected Python modules from a git revision, or a local engine snapshot.
"""
import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import cProfile
import json
from pathlib import Path
import pstats
import sys
from time import perf_counter, process_time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apps.api import training_plan_engine as engine, planning_search, method_capacity
from apps.api.management_schemas import LoadProgression, PlanningControls
from biathlon import hr_speed, speed_duration, speed_zones, method_dosing, combined_methods
from biathlon.component_load import calculate_component_load
from biathlon.sport_heart_rate import local_settings, reference_offset

TODAY = date(2026, 9, 21)
NOW = datetime(2026, 9, 21, 9, tzinfo=timezone.utc)
SPORTS = ['NordicSki', 'Run']
ZONES = ['Z1', 'Z2', 'Z3', 'Z4', 'Z5', 'STR']

class Repository:
    def __init__(self):
        self.settings = SimpleNamespace(timezone='Europe/Sofia', hrmax_bpm=190,
                                        zone_bounds_bpm=(100,125,145,160,175,190))
        self.events = [{'event_type':'MAIN_RACE','event_id':'synthetic-race', 'name':'Synthetic race',
                       'start_date':(TODAY+timedelta(days=120)).isoformat(),
                       'end_date':(TODAY+timedelta(days=120)).isoformat()}]
        self.preferences = {}
        self.accents = {'accent_mode':'AUTO','accent_limit':3,'manual_components':[]}
        days = [TODAY-timedelta(days=n) for n in range(69,-1,-1)]
        direct = dict(zip(ZONES, (60.,30.,20.,10.,8.,0.)))
        effective = calculate_component_load(direct)['effective']
        activities=[];daily=[];strength=[]
        for i,day in enumerate(days):
            recorded = (TODAY-day).days>7
            if recorded:
                sport=SPORTS[i%len(SPORTS)]
                zones=[{'zone':z,'raw_time_min':direct[z],'equivalent_time_min':direct[z],
                        'effective_load':effective[z]} for z in ZONES if z!='STR']
                activities.append({'activity_ref':f'synthetic-{i}', 'date':day.isoformat(),
                    'sport':sport,'duration_min':sum(direct.values()),'zones':zones})
            daily.extend({'date':day.isoformat(),'zone':z,'effective_load':effective[z] if recorded else 0.}
                         for z in ZONES if z!='STR')
            strength.append({'date':day.isoformat(),'effective_load':8. if recorded else 0.})
        self.envelope={'generation_id':'synthetic-generation','revision':1,
            'activities':[{**deepcopy(a),'local_date':a['date']} for a in activities],
            'snapshot_payload':{'load_history':{'period_start':days[0].isoformat(),
                'period_end':TODAY.isoformat(),'quality':{'limited_activities':0,'excluded_activities':0},
                'daily':daily,'activities':activities,'strength':{'daily':strength}}}}
    def athlete_settings(self,alias):return self.settings
    def active_activity_calendar(self,alias,start,end):return deepcopy(self.envelope)
    def athlete_planning_calendar(self,alias):return {'events':deepcopy(self.events)}
    def athlete_planning_profile(self,alias):return deepcopy(self.preferences)
    def athlete_mesocycle_accent_preferences(self,alias):return deepcopy(self.accents)
    def _request(self,method,path,**kwargs):
        assert method=='GET'
        return []
    def _json(self,value):return value

def sport_curve(repo,sport):
    scale=20./22. if sport=='NordicSki' else 1.
    tests=[{'duration_s':duration,'speed_kmh':speed*scale,'maximal':True,'test_mode':'STRICT'}
           for duration,speed in ((600.,22.),(7200.,15.))]
    curve=speed_duration.calibrated(tests)
    durations=(18000.,10800.,3600.,1800.) if sport=='NordicSki' else (12600.,9000.,2700.,1200.)
    settings=local_settings(repo.settings,sport);offset=reference_offset(sport)
    indices={f'Z{i+1}':{'index':(100*(settings.zone_bounds_bpm[i+1]+offset)/(settings.hrmax_bpm+offset))/(curve.speed(t)*3.6),
                         'count':3,'seconds':1800.} for i,t in enumerate(durations)}
    predictor=hr_speed.ZonalPredictor(curve,settings.zone_bounds_bpm,settings.hrmax_bpm,indices,
                                      normalization_offset_bpm=offset)
    return {'status':'CALIBRATED','sport':sport,'model_version':speed_duration.VERSION,
        'hr_model':predictor.summary(),'source_generation_id':'synthetic-generation','source_revision':1,
        'active_test_keys':['short','long'],'tests':[{'entry_key':k,'payload':t} for k,t in zip(('short','long'),tests)],
        'index_window':{'last_activity_date':TODAY.isoformat()},'index_summary':indices,'zone_corrections':{}}

def fixture(catalog="native"):
    repo=Repository()
    body={'schema_version':'management-profile-v1','sport':'NordicSki','actual_sport':'NordicSki',
        'discipline':'10 km','age_years':30,'training_experience_years':10,
        'program_start':(TODAY-timedelta(days=28)).isoformat(),'program_end':(TODAY+timedelta(days=120)).isoformat(),
        'available_minutes':[180]*6+[60],'taper_days':7,'recent_weekly_hours':None,
        'building_fraction':.65,'maintenance_fraction':.39,'reentry_fraction':.39,
        'max_key_sessions_per_week':3,'recovery_session_cap_min':30,'allow_expert_fallback':True,
        'strength_enabled':True,'reentry_days':0,'adaptation_mode':'AUTO','race_duration_min':35.,
        'load_progression':LoadProgression(training_level='HIGH').model_dump(),
        'planning_controls':PlanningControls(mesocycle_anchor=TODAY,accent_mode='MANUAL',accents=['Z3','Z4'],
            training_sports=SPORTS,capacity_policy='MODEL_WITH_PRIOR',sessions_per_week=12,
            sessions_by_day=[2,2,2,2,2,1,1],threshold_days=[1,4],strength_days=[1,4],
            mixed_sessions_enabled=True,automatic_intervals=True).model_dump(mode='json'),
        'method_library':{'enabled':True,'overrides':{}},
        'interval_profiles':[{'zone':zone,'sport':sport,'continuous_capacity_min':capacity,
            'assessed_on':TODAY.isoformat(),'effort':'Synthetic measured coach effort',
            'work_seconds':work,'recovery_seconds':work,'min_repetitions':low,'max_repetitions':high,
            'total_capacity_ratio':ratio,'reserve_repetitions':2,'target_speed_kmh':None}
            for sport in SPORTS for zone,capacity,work,low,high,ratio in
            [('Z4',20.,180,3,8,2.),('Z5',12.,60,6,20,2.5)]]}
    speeds={sport:sport_curve(repo,sport) for sport in SPORTS}
    if catalog == 'source':
        for sport, view in speeds.items():
            local=local_settings(repo.settings,sport)
            tests=[entry['payload'] for entry in view['tests']]
            curve=speed_duration.calibrated(tests)
            predictor=hr_speed.ZonalPredictor(curve,local.zone_bounds_bpm,local.hrmax_bpm,
                view['index_summary'],normalization_offset_bpm=reference_offset(sport))
            view['speed_zones']=speed_zones.build(curve,predictor,tests)
            view['sport_hr_policy']={'sport_hrmax_bpm':local.hrmax_bpm,
                'sport_zone_bounds_bpm':list(local.zone_bounds_bpm),'offset_bpm':reference_offset(sport)}
    return repo,body,speeds

def run(mode, profile_path=None, catalog="native", plan_path=None):
    repo,body,speeds=fixture(catalog)
    calls=[];cache={};catalog_sizes=[]
    original_registry=engine.method_registry.executable_methods
    def registry(*args,**kwargs):
        result=original_registry(*args,**kwargs);catalog_sizes.append(len(result));return result
    def replay(policy=None):
        start=perf_counter()
        plan=engine.generate_plan(repo,'synthetic', {**body,**({'_allocation_policy':policy} if policy else {})},
                                 start_date=TODAY,now=NOW,_recovery_calculation_cache=cache)
        calls.append({'seconds':perf_counter()-start, 'policy_keys':sorted(policy or {}),
            'policy_digest':sha256(json.dumps(policy,sort_keys=True,default=str).encode()).hexdigest()})
        return plan
    profiler=cProfile.Profile() if profile_path else None
    with patch.object(engine.model_service,'speed_view',side_effect=lambda repo,alias,sport:deepcopy(speeds[sport])), \
         patch.object(engine.method_registry,'executable_methods',side_effect=registry):
        start=perf_counter();cpu_start=process_time()
        if profiler:profiler.enable()
        baseline=replay()
        plan=planning_search.improve(baseline,body,replay,seconds_remaining=150.) if mode=='search' else baseline
        if profiler:profiler.disable()
        elapsed=perf_counter()-start;cpu_elapsed=process_time()-cpu_start
    if profiler:
        profiler.dump_stats(profile_path)
        with open(str(profile_path)+'.txt','w') as stream:
            stats=pstats.Stats(profiler,stream=stream).strip_dirs()
            stats.sort_stats('cumulative').print_stats(55)
            stats.sort_stats('tottime').print_stats(40)
    if plan_path:Path(plan_path).write_text(json.dumps(plan,sort_keys=True,default=str))
    sessions=[s for d in [*plan['days'],*plan.get('lookahead_days',[])] for s in d.get('sessions',[])]
    stable={'sessions':[{k:s.get(k) for k in ('date','method_id','sport','purpose','main_work_minutes','blocks')}
                        for s in sessions], 'allocation':{k:v for k,v in plan.get('allocation',{}).items() if k!='search'}}
    return {'mode':mode,'catalog':catalog,'profiled':bool(profiler),'seconds':elapsed,'cpu_seconds':cpu_elapsed,'replays':calls,'catalog_sizes':catalog_sizes,
        'visible_days':len(plan['days']),'lookahead_days':len(plan.get('lookahead_days',[])),
        'sessions':len(sessions),'sports':sorted({s['sport'] for s in sessions}),
        'allocation':{z:{k:r.get(k) for k in ('coverage_percent','planned_q','target_q','within_tolerance')}
                      for z,r in (plan.get('allocation') or {}).get('components',{}).items()},
        'search':(plan.get('allocation') or {}).get('search'),
        'development':[{k:row.get(k) for k in ('window_start','window_end','kind','planned','minimum','target','minimum_shortfall','target_shortfall')}
                       for row in ((plan.get('allocation') or {}).get('development') or {}).get('segments',[])],
        'digest':sha256(json.dumps(stable,sort_keys=True,default=str).encode()).hexdigest()}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['engine','search'],default='engine')
    parser.add_argument('--repeat',type=int,default=1);parser.add_argument('--profile');parser.add_argument('--output');parser.add_argument('--catalog',choices=['native','source'],default='native');parser.add_argument('--baseline-ref');parser.add_argument('--engine-snapshot');parser.add_argument('--plan-output')
    args=parser.parse_args();results=[]
    if args.baseline_ref:
        import subprocess
        for module,path in [(method_dosing,'biathlon/method_dosing.py'),
                            (combined_methods,'biathlon/combined_methods.py'),
                            (method_capacity,'apps/api/method_capacity.py'),
                            (engine,'apps/api/training_plan_engine.py'),
                            (planning_search,'apps/api/planning_search.py')]:
            code=subprocess.run(['git','show',f'{args.baseline_ref}:{path}'],cwd=ROOT,text=True,capture_output=True,check=True).stdout
            exec(compile(code,str(ROOT/path),'exec'),module.__dict__)
        print(f'BASELINE_MODULES_CAPTURED {args.baseline_ref}',flush=True)
    if args.engine_snapshot:
        exec(compile(Path(args.engine_snapshot).read_text(),str(ROOT/'apps/api/training_plan_engine.py'),'exec'),engine.__dict__)
    for _ in range(args.repeat):
        result=run(args.mode,args.profile,args.catalog,args.plan_output);results.append(result);print(json.dumps(result),flush=True)
    if args.output:Path(args.output).write_text(json.dumps(results,indent=2))
