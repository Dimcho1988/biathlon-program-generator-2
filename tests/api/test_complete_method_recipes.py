"""Completed recipes must work in the executor, not only have a numeric card."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from biathlon import method_library as lib, hr_speed, speed_duration, speed_zones
from apps.api import training_plan_engine as engine
from tests.api.test_sourced_method_library import athlete, view, adapter, recipe
from tests.api.test_training_plan_engine import BOUNDS, TODAY


ROWS = {r["id"]: r for r in lib.entries()}
NEW = [r for r in ROWS.values() if r.get("completion_version") == 1]


def complete_view(sport="Run"):
    v=view(sport)
    v["tests"].append({"entry_key":"short", "payload":{"duration_s":20., "speed_kmh":36., "maximal":True,"test_mode":"STRICT"}})
    v["active_test_keys"].append("short")
    curve,tests,_=lib.selected_curve(v)
    predictor=hr_speed.Predictor(curve,BOUNDS,190,{})
    v["hr_model"]=predictor.summary()
    v["speed_zones"]=speed_zones.build(curve,predictor,tests)
    v["sport_hr_policy"]={"sport_zone_bounds_bpm":list(BOUNDS),"sport_hrmax_bpm":190,"offset_bpm":0}
    return v


def context(row):
    p=athlete(sport=row["sports"][0],discipline=f"{(row.get('disciplines') or [5000])[0]} m")
    return p,complete_view(p["sport"]),row["periods"][0]


def test_every_open_source_has_an_explicit_separate_working_recipe():
    payload=json.loads(lib.CATALOG_PATH.read_text())
    links=payload["completion_audit"]["source_to_working_methods"]
    assert len(links)==57
    for source,targets in links.items():
        assert ROWS[source]["related_method_ids"]==targets
        for identifier in targets:
            row=ROWS[identifier]
            assert row["prescription"] and row["automatic_selection"]
            assert source in row["derived_from"]
            if row.get("completion_version"):
                assert row["is_source_equivalent"] is False
                assert all(row["guidance"][k] for k in ["purpose","execution","recovery","success","errors","adjustment","progression"])
    assert ROWS["S06"]["prescription"] is None
    assert ROWS["AO17"]["prescription"]["speed_multiplier"]==[1.025,1.025]


@pytest.mark.parametrize("row",NEW,ids=lambda r:r["id"])
def test_all_completed_recipes_resolve_and_execute_on_compatible_profile(row,monkeypatch):
    p,v,phase=context(row)
    r=lib.resolve(row,p,v,phase=phase)
    assert r["status"]=="READY",r
    monkeypatch.setattr(lib,"entries",lambda:[deepcopy(row)])
    methods=lib.executable_methods(p,v,phase=phase)
    assert len(methods)==1
    m=methods[0]; settings=SimpleNamespace(zone_bounds_bpm=BOUNDS,hrmax_bpm=190)
    e=engine.capacity_for(m,settings,v,engine._capacity_context(v,settings),TODAY,use_model_prior=True)
    assert e
    minimum=engine._minimum_work(m,e,settings)
    assert minimum is not None
    assert engine._minimum_dose_usage(engine._blocks(m,minimum,e,settings),e,m["zone"])+1e-9>=engine.MIN_AEROBIC_DOSE_FRACTION
    for seconds in [r["minimum_work_seconds"],r["maximum_work_seconds"]]:
        blocks=engine._blocks(m,seconds/60,e,settings)
        work=[b for b in blocks if b["kind"]=="WORK"]
        assert work
        assert sum(b["duration_min"]*60 for b in work)==pytest.approx(seconds,abs=.002)
        assert all(b["target_speed_kmh"]>0 and b.get("hr_reference_range_bpm") for b in work)
        assert all(b["target_hr_bpm"] is None for b in work)


def test_short_end_needs_real_test_and_parent_disable_is_respected():
    row=ROWS["OF-XC10"]
    p=athlete(sport="NordicSki")
    phase=row["periods"][0]
    assert lib.resolve(row,p,view("NordicSki"),phase=phase)["reason"]=="SHORT_SPEED_TEST_REQUIRED"
    v=complete_view("NordicSki")
    assert lib.resolve(row,p,v,phase=phase)["status"]=="READY"
    p["method_library"]={"overrides":{"XC10":{"enabled":False}}}
    assert lib.resolve(row,p,v,phase=phase)["status"]=="DISABLED"
    p["method_library"]["overrides"][row["id"]]={"enabled":True}
    assert lib.resolve(row,p,v,phase=phase)["status"]=="READY"


def test_exact_seconds_offset_is_not_a_constant_speed_percentage():
    row=ROWS["OF-AO10"];p,v,phase=context(row)
    r=lib.resolve(row,p,v,phase=phase);first=r["segments"][0]
    assert first["work_duration_seconds"]==pytest.approx(400/(first["anchor"]["speed_kmh"]/3.6)-4)
    assert r["segments"][1]["work_distance_m"]==400


def test_variable_recipe_unlocks_longer_work_only_after_two_confirmed_dates():
    from biathlon import method_progression
    row=ROWS["OF-AO10"];p,v,phase=context(row)
    phase="SPECIAL_PREPARATION"
    first=lib.resolve(row,p,v,phase=phase)
    stage=first["progression"]
    descriptor={"sport":p["sport"],"progression":{"key":stage["key"],"version":method_progression.VERSION,"step":0}}
    p["_method_progression"]={"observations":[{"id":str(n),"day":f"2026-09-{n}","success":True,"rpe":7,"method":deepcopy(descriptor)} for n in [10,14]]}
    advanced=lib.resolve(row,p,v,phase=phase)
    assert advanced["progression"]["step"]==1
    assert advanced["segments"][1]["work_distance_m"]==600
    assert first["segments"][1]["work_distance_m"]==400


def test_dynamic_tmax_work_and_recovery_are_resolved_together():
    row=ROWS["OF-R08"];p,v,phase=context(row)
    r=lib.resolve(row,p,v,phase=phase)
    assert r["work_duration_seconds"]==pytest.approx(.65*r["continuous_capacity_seconds"])
    assert r["recovery_seconds"]==pytest.approx(2*r["work_duration_seconds"])
    assert r["recovery_resolution"]["speed_multiplier"]==.7


def test_ski_40_20_is_a_speed_adaptation_and_exactly_preserves_cycles(monkeypatch):
    row=ROWS["OF-S06"];p,v,_=context(row)
    m,e,settings=adapter(monkeypatch,row,p,v)
    blocks=engine._blocks(m,4,e,settings)
    core=[b for b in blocks if b["kind"] in {"WORK","RECOVERY"}]
    assert [b["kind"] for b in core]==["RECOVERY","WORK"]*6
    assert [b["duration_s"] for b in core]==[20,40]*6
    assert core[1]["target_speed_kmh"]==pytest.approx(lib.selected_curve(v)[0].speed(300)*3.6*1.025,abs=.001)
    assert all(b["hr_role"]=="SECONDARY_OBSERVATION_WITH_LAG" for b in core)


def test_decreasing_sequence_keeps_prefix_and_quantizes_only_tail(monkeypatch):
    row=ROWS["OF-R18"];p,v,_=context(row)
    m,e,settings=adapter(monkeypatch,row,p,v)
    assert engine._quantized_work(m,8.4)==8.25
    assert engine._previous_work(m,8.25)==7.75
    blocks=engine._blocks(m,8.4,e,settings)
    assert [b["duration_s"] for b in blocks if b["kind"]=="WORK"]==[180,120,60,45,30,30,30]
    assert [b["duration_s"] for b in blocks if b["kind"]=="RECOVERY"]==[120,80,40,30,20,20]


def test_progressive_run_scales_every_phase_and_uses_continuous_capacity(monkeypatch):
    row=ROWS["OF-RD10"];p,v,_=context(row)
    monkeypatch.setattr(lib,"entries",lambda:[deepcopy(row)])
    m=lib.executable_methods(p,v,phase=row["periods"][0])[0]
    settings=SimpleNamespace(zone_bounds_bpm=BOUNDS,hrmax_bpm=190)
    e=engine.capacity_for(m,settings,v,engine._capacity_context(v,settings),TODAY,use_model_prior=True)
    blocks=engine._blocks(m,63,e,settings)
    assert len(blocks)==6
    assert [b["duration_min"] for b in blocks]==pytest.approx([21,16.8,10.5,8.4,4.2,2.1])
    assert engine._dose_usage(blocks,e,m["zone"])==pytest.approx(63/e["capacity_minutes"])
    assert engine._interval_budget_profile(m) is None
    assert not engine._blocks(m,29,e,settings)


@pytest.mark.parametrize("identifier,seconds",[("OF-MR07",360),("OF-MR08",600),("OF-MR09",30),("OF-MR10",300),("OF-MR11",300),("OF-MR15",480)])
def test_internal_bursts_and_reliefs_do_not_extend_the_block(identifier,seconds):
    row=ROWS[identifier];p,v,phase=context(row)
    r=lib.resolve(row,p,v,phase=phase)
    assert sum(s["work_duration_seconds"] for s in r["segments"])==seconds


def test_hr_range_is_individual_and_never_percent_cs_equals_percent_hrmax():
    row=ROWS["OF-MR02"];p,v,phase=context(row)
    r=lib.resolve(row,p,v,phase=phase)
    prepared,predictor=lib._prepare(v)
    predicted=[predictor.hr_for_speed(s) for s in r["target_speed_range_kmh"]]
    lo,hi=r["hr_reference_range_bpm"]
    assert lo<=min(predicted)<=max(predicted)<=hi
    assert r["hr_reference_basis"]=="INDIVIDUAL_SPEED_HR_MODEL_ESTIMATE"
    assert r["hr_is_measured"] is False
    assert lo != int(.95*190)


def test_cached_curve_preserves_exact_values_and_does_not_repeat_inverse():
    curve,_,_=lib.selected_curve(view())
    calls=[]; original=curve.inverse
    curve.inverse=lambda *a,**k: (calls.append((a,k)),original(*a,**k))[1]
    memo=lib._CachedCurve(curve)
    first=memo.inverse(5.)
    assert all(memo.inverse(5.)==first for _ in range(100))
    assert len(calls)==1


def test_coach_cards_use_the_engine_for_personal_speed_hr_and_time():
    from apps.api.method_library_service import _coach_previews
    from biathlon import method_registry
    p=athlete();v=complete_view()
    cards=method_registry.catalog(p,v,phase="GENERAL_PREPARATION")
    settings=SimpleNamespace(zone_bounds_bpm=BOUNDS,hrmax_bpm=190)
    _coach_previews(cards,p,v,settings,TODAY,"GENERAL_PREPARATION")
    ready=[m for m in cards["methods"] if m.get("registry_kind")=="COACH_PROFILE" and m["resolution"]["status"]=="READY"]
    assert ready
    aerobic=[m for m in ready if m["resolution"]["zone"]!="STR"]
    assert aerobic and all(m["resolution"].get("hr_reference_range_bpm") for m in aerobic)
    assert all(m["resolution"].get("work_duration_range_seconds") for m in ready)
    assert all(m["resolution"]["minimum_work_seconds"]<=m["resolution"]["maximum_work_seconds"]+1e-6 for m in ready)


def test_completed_ski_method_enters_the_actual_weekly_generator(monkeypatch):
    from datetime import timedelta
    from tests.api.test_load_progression import configured, observed
    from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness, assert_rolling_budgets
    from tests.api.test_training_plan_engine import NOW
    row=ROWS["OF-S06"];p,v,phase=context(row)
    v.update(source_generation_id="generation-one", source_revision=1,
             index_window={"last_activity_date":TODAY.isoformat()})
    monkeypatch.setattr(lib,"entries",lambda:[deepcopy(row)])
    method=lib.executable_methods(p,v,phase=phase)[0];zone=method["zone"]
    profile=configured(age_years=25,training_experience_years=5,sport="NordicSki",actual_sport="NordicSki",discipline="10 km",
        building_fraction=.65,available_minutes=[180]*7,horizon_mode="MANUAL",program_end=(TODAY+timedelta(days=6)).isoformat(),
        component_targets_weekly={"Z1":100.,zone:40.})
    profile["planning_controls"].update(accents=[zone],sessions_per_week=1,sessions_by_day=[0,1,0,0,0,0,0],threshold_days=[1])
    fixed_readiness(monkeypatch,100.)
    monkeypatch.setattr(engine,"resolved_methods",lambda _:[])
    monkeypatch.setattr(engine.model_service,"speed_view",lambda *args,**kwargs:deepcopy(v))
    repo,_,_=observed();repo.settings=SimpleNamespace(zone_bounds_bpm=BOUNDS,hrmax_bpm=190,timezone="Europe/Sofia")
    for activity in repo.envelope["activities"] + repo.envelope["snapshot_payload"]["load_history"]["activities"]:
        activity["sport"]="NordicSki"
    plan=engine.generate_plan(repo,"athlete",profile,start_date=TODAY,now=NOW)
    sessions=[s for d in plan["days"] for s in d["sessions"]]
    assert sessions,[r for d in plan['days'] for r in d.get('rejected_alternatives',[])]
    assert sessions[0]["method_library"]["id"]=="OF-S06"
    work=[b for b in sessions[0]["blocks"] if b["kind"]=="WORK"]
    assert work and all(b["duration_s"]==40 and b["hr_reference_range_bpm"] for b in work)
    assert_rolling_budgets(plan)
