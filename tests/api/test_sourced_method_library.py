"""Numerical recipe resolution and load-preserving scheduler execution."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from biathlon import method_library as lib, dosing_curve, speed_duration, hr_speed, speed_zones
from apps.api import training_plan_engine as engine
from tests.api.test_training_plan_engine import TODAY, BOUNDS


def view(sport="Run"):
    tests = [{"duration_s":180., "speed_kmh":22., "maximal":True, "use_for_cs":True},
             {"duration_s":1200., "speed_kmh":18., "maximal":True, "use_for_cs":True}]
    curve = speed_duration.calibrated(tests)
    predictor = hr_speed.Predictor(curve, BOUNDS, 190, {})
    return {"status":"CALIBRATED", "sport":sport, "model_version":speed_duration.VERSION,
            "active_test_keys":["a","b"], "tests":[{"entry_key":k,"payload":t} for k,t in zip(("a","b"),tests)],
            "index_summary":{}, "speed_zones":speed_zones.build(curve,predictor,tests)}


def recipe(**changes):
    r = {"id":"TEST", "title":"Source recipe", "sports":["Run","NordicSki","RollerSki"],
         "periods":["GENERAL_PREPARATION","SPECIAL_PREPARATION","PRECOMPETITION"],
         "source":{"authors":"Published source"}, "guidance":{"execution":"Keep the prescribed rhythm."},
         "prescription":{"anchor":{"type":"RACE_DISTANCE","distance_m":5000},
             "speed_multiplier":[1.,1.02],"work":{"unit":"meters","range":[400.,400.]},
             "repetitions":[1,8],"recovery":{"seconds":[90,90],"type":"PASSIVE"}}}
    r.update(changes)
    return r


def athlete(**changes):
    return {"sport":"Run", "discipline":"5000 m", "planning_controls":{"capacity_policy":"MODEL_WITH_PRIOR"}, **changes}


def adapter(monkeypatch, r, p=None, v=None):
    p, v = p or athlete(), v or view()
    monkeypatch.setattr(lib, "entries", lambda:[deepcopy(r)])
    method = lib.executable_methods(p,v,phase="SPECIAL_PREPARATION")[0]
    method["actual_sport"] = p.get("actual_sport",p["sport"])
    settings = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=190)
    context = engine._capacity_context(v,settings)
    cap = engine.capacity_for(method,settings,v,context,TODAY,use_model_prior=True)
    assert cap
    return method,cap,settings


def test_race_distance_inverted_on_chosen_blend_not_test_duration(monkeypatch):
    v = view(); test_curve,tests,_=lib.selected_curve(v)
    index_curve = speed_duration.ScaledCurve(test_curve,300.,test_curve.speed(300.)*.8)
    blend = dosing_curve.Curve(index_curve,test_curve)
    v["dosing_model"]={"speed_zones":v["speed_zones"]}
    monkeypatch.setattr(dosing_curve,"from_view",lambda _:blend)
    result = lib.resolve(recipe(),athlete(),v,phase="SPECIAL_PREPARATION")
    assert result["status"] == "READY"
    anchor = result["anchor"]
    assert anchor["duration_seconds"] * anchor["speed_kmh"]/3.6 == pytest.approx(5000.)
    assert anchor["duration_seconds"] != pytest.approx(test_curve.inverse(5000,distance=True))
    assert result["target_speed_kmh"] == pytest.approx(anchor["speed_kmh"]*1.01)
    assert result["work_duration_seconds"] == pytest.approx(400/(result["target_speed_kmh"]/3.6))
    assert result["curve_source"] == "BLENDED_DOSING_CURVE"


def test_ski_primary_discipline_uses_actual_rollers_curve_and_explicit_ranges():
    r=recipe();r["prescription"].update(anchor={"type":"PRIMARY_RACE"},work={"unit":"seconds","range":[30,90]},speed_multiplier=[1.01,1.05])
    p=athlete(sport="NordicSki",actual_sport="RollerSki",discipline="10 km")
    first=lib.resolve(r,p,view("RollerSki"),phase="GENERAL_PREPARATION")
    final=lib.resolve(r,p,view("RollerSki"),phase="PRECOMPETITION")
    assert first["status"] == final["status"] == "READY"
    assert first["actual_sport"] == "RollerSki"
    assert first["anchor"]["distance_m"] == 10000
    assert first["anchor"]["speed_kmh"] == final["anchor"]["speed_kmh"]
    assert first["work_duration_seconds"] == final["work_duration_seconds"] == 30
    assert first["speed_multiplier"] == final["speed_multiplier"] == 1.05
    assert not final["curve_improvement_assumed"]


def test_budget_only_source_does_not_manufacture_hundred_repeat_ceiling():
    r=recipe();r["prescription"].update(anchor={"type":"V5"},work={"unit":"seconds","range":[30,30]},speed_multiplier=[1,1],repetitions=None,repetitions_from_budget=True)
    result=lib.resolve(r,athlete(),view(),phase="SPECIAL_PREPARATION")
    assert result["status"] == "READY"
    assert result["maximum_work_seconds"] == pytest.approx(300.)
    assert result["work_duration_seconds"] == 30


def test_recovery_scales_whole_repeats_once_and_passive_time_is_not_work(monkeypatch):
    m,e,settings=adapter(monkeypatch,recipe())
    fraction=engine._nominal_fraction(m,"BUILDING",athlete(),"SPECIAL_PREPARATION",e,{})
    requested=e["capacity_minutes"]*fraction*.5
    blocks=engine._blocks(m,requested,e,settings)
    work=[b for b in blocks if b["kind"]=="WORK"]
    rests=[b for b in blocks if b["kind"]=="RECOVERY"]
    assert len(work)==4 and len(rests)==3
    assert all(b["passive_rest"] for b in rests)
    assert all(b["method_library"]["id"]=="TEST" for b in work)
    assert engine._dose_usage(blocks,e,m["zone"])==pytest.approx(.5)
    direct,_,_=engine._canonical_load(blocks,settings,[],TODAY)
    without_rest,_,_=engine._canonical_load([b for b in blocks if not b.get("passive_rest")],settings,[],TODAY)
    assert direct==without_rest


def test_compound_recovery_speed_has_actual_zone_and_single_integrated_budget(monkeypatch):
    r=recipe();r["prescription"].update(anchor={"type":"V5"},speed_multiplier=[1,1],structure="COMPOUND",repetitions=[1,4],
        pattern=[{"work":{"unit":"seconds","range":[30,30]},"speed_multiplier":[1,1]},
                 {"work":{"unit":"seconds","range":[30,30]},"speed_multiplier":[.7,.7],"kind":"RECOVERY"}])
    r["prescription"]["recovery"]={"seconds":[0,0],"type":"ACTIVE_Z1"}
    m,e,settings=adapter(monkeypatch,r)
    blocks=engine._blocks(m,m["max_work_min"]*.5,e,settings)
    work=[b for b in blocks if b["kind"]=="WORK"]
    rest=[b for b in blocks if b["kind"]=="RECOVERY"]
    assert len(work)==2 and len(rest)==1
    assert rest[0]["target_speed_kmh"]==pytest.approx(work[0]["target_speed_kmh"]*.7,abs=.002)
    assert engine._dose_usage(blocks,e,m["zone"])==pytest.approx(.5)


def test_power_reference_and_missing_cs_never_silently_become_speed():
    r=recipe(prescription=None,non_executable_reason="Power anchor requires a separate executor")
    assert lib.resolve(r,athlete(),view())["status"]=="REFERENCE_ONLY"
    r=recipe();r["prescription"]["anchor"]={"type":"CV"}
    v=view()
    for t in v["tests"]:t["payload"]["use_for_cs"]=False
    result = lib.resolve(r,athlete(),v)
    assert result["status"] == "READY"
    assert result["anchor"]["evidence"]["source"] == "CURRENT_CURVE_V3_V12"
    assert result["anchor"]["evidence"]["measured"] is False


def test_continuous_recipe_uses_continuous_tmax_denominator(monkeypatch):
    r=recipe();r["prescription"].update(anchor={"type":"CV"},speed_multiplier=[.95,.95],structure="CONTINUOUS",work={"unit":"seconds","range":[300,600]},repetitions=[1,1])
    m,e,settings=adapter(monkeypatch,r)
    blocks=engine._blocks(m,m["min_work_min"],e,settings)
    assert blocks
    assert engine._dose_usage(blocks,e,m["zone"]) == pytest.approx(m["min_work_min"]/e["capacity_minutes"])


def test_explicit_repeat_override_preserves_coach_minimum():
    r=recipe();r["prescription"].update(allow_fewer_repetitions=True,source_repetitions_range=[8,8])
    result=lib.resolve(r,athlete(method_library={"overrides":{"TEST":{"repetitions":[4,4]}}}),view(),phase="SPECIAL_PREPARATION")
    assert result["repetitions"]==[4,4]
    assert result["source_repetitions"]==[8,8]


def test_library_minimum_uses_independent_continuous_tmax(monkeypatch):
    m,e,settings=adapter(monkeypatch,recipe())
    minimum=engine._minimum_work(m,e,settings)
    blocks=engine._blocks(m,minimum,e,settings)
    assert engine._minimum_dose_usage(blocks,e,m["zone"]) >= .25
    smaller=engine._blocks(m,minimum-m["method_library"]["resolution"]["cycle_work_seconds"]/60,e,settings)
    assert not smaller or engine._minimum_dose_usage(smaller,e,m["zone"]) < .25


def test_disabling_automatic_intervals_keeps_library_continuous(monkeypatch):
    r=recipe();r["prescription"].update(anchor={"type":"CV"},speed_multiplier=[.95,.95],structure="CONTINUOUS",work={"unit":"seconds","range":[300,600]},repetitions=[1,1])
    monkeypatch.setattr(lib,"entries",lambda:[r,recipe(id="INTERVAL")])
    p=athlete(planning_controls={"capacity_policy":"MODEL_WITH_PRIOR","automatic_intervals":False})
    methods=lib.executable_methods(p,view(),phase="SPECIAL_PREPARATION")
    assert len(methods)==1 and methods[0]["structure"]=="LIBRARY_CONTINUOUS"


def test_internal_passive_recovery_not_inherited_from_active_series_rest(monkeypatch):
    r=recipe();r["prescription"].update(anchor={"type":"V5"},speed_multiplier=[1,1],structure="COMPOUND",repetitions=[1,2],
        recovery={"seconds":[120,120],"type":"ACTIVE_Z1"},
        pattern=[{"work":{"unit":"seconds","range":[30,30]},"recovery_after_seconds":[60,60],"recovery_after_type":"PASSIVE"},
                 {"work":{"unit":"seconds","range":[30,30]}}])
    m,e,settings=adapter(monkeypatch,r)
    blocks=engine._blocks(m,m["max_work_min"],e,settings)
    rests=[b for b in blocks if b["kind"]=="RECOVERY"]
    assert [b["passive_rest"] for b in rests]==[True,False,True]


def test_exact_distance_recipe_survives_whole_week_generation(monkeypatch):
    from datetime import timedelta
    from tests.api.test_method_library_integration_review import source, SETTINGS
    from tests.api.test_load_progression import configured, observed
    from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness, assert_rolling_budgets
    from tests.test_dosing_curve import example
    from tests.api.test_training_plan_engine import NOW
    row=deepcopy(source("R01"))
    row["periods"]=["GENERAL_PREPARATION"]
    row["prescription"].update(repetitions=[4,4],allow_fewer_repetitions=False)
    monkeypatch.setattr(lib,"entries",lambda:[deepcopy(row)])
    v=example();v.update(source_generation_id="generation-one",source_revision=1,
                        index_window={"last_activity_date":TODAY.isoformat()})
    method=lib.executable_methods(athlete(),v,phase="GENERAL_PREPARATION")[0]
    assert method["max_work_min"]*2 != pytest.approx(round(method["max_work_min"]*2))
    profile=configured(age_years=25,training_experience_years=5,discipline="5000 m",
        building_fraction=.65,available_minutes=[180]*7,horizon_mode="MANUAL",
        program_end=(TODAY+timedelta(days=6)).isoformat(),component_targets_weekly={"Z1":100.,method["zone"]:40.})
    profile["planning_controls"].update(accents=[method["zone"]],sessions_per_week=1,
        sessions_by_day=[0,1,0,0,0,0,0],threshold_days=[1])
    fixed_readiness(monkeypatch,100.)
    monkeypatch.setattr(engine,"resolved_methods",lambda _:[])
    monkeypatch.setattr(engine.model_service,"speed_view",lambda *args,**kwargs:deepcopy(v))
    repo,_,_=observed();repo.settings=SETTINGS
    plan=engine.generate_plan(repo,"athlete",profile,start_date=TODAY,now=NOW)
    sessions=[s for day in plan["days"] for s in day["sessions"]]
    assert sessions, [r for d in plan["days"] for r in d.get("rejected_alternatives",[])]
    blocks=[b for b in sessions[0]["blocks"] if b["kind"]=="WORK"]
    assert len(blocks)==4 and sum(b["target_distance_m"] for b in blocks)==4000
    assert_rolling_budgets(plan)
