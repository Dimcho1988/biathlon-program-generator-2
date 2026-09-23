from datetime import timedelta

import pytest
from pydantic import ValidationError

from apps.api import training_plan_engine as engine, load_adaptation
from apps.api.management_schemas import LoadProgression, PlanningControls
from biathlon import load_progression as policy, planning_controls
from tests.api.test_training_plan_engine import Repository, TODAY, NOW, profile, reference_speed


def configured(**patch):
    return profile(reentry_days=0, load_progression=LoadProgression().model_dump(),
                   planning_controls=PlanningControls(mesocycle_anchor=TODAY, accent_mode="MANUAL", accents=["Z3"]).model_dump(mode="json"), **patch)


def observed():
    repo = Repository()
    source = repo.envelope["snapshot_payload"]["load_history"]
    for a in source["activities"]:
        for z in a["zones"]:
            z["equivalent_time_min"] = z["raw_time_min"]*.8
    return repo, source, engine._daily_rows(source, TODAY)


def test_curve_uses_separate_q_bounds_and_preserves_observed_outliers():
    c=policy.DEFAULTS
    for z,(low,high) in policy.WEEKLY_Q_BOUNDS.items():
        values=[policy.annual_rate(q,z,c) for q in [0,.7*low,low,(low+high)/2,high,1.15*high,1.3*high,2*high]]
        assert values == sorted(values,reverse=True)
        assert values[:3] == [30]*3
        assert values[4] == 10 and values[-2:] == [0,0]
    assert policy.annual_rate(56,"STR",c) is None
    with pytest.raises(ValidationError): LoadProgression(max_dose_fraction=.81)
    with pytest.raises(ValidationError): LoadProgression(low_volume_annual_percent=5,upper_volume_annual_percent=10)
    _,source,rows=observed()
    for a in source["activities"]:
        for z in a["zones"]: z["equivalent_time_min"] = 1000
    ctx=policy.context(configured(),source,rows,TODAY)
    assert ctx["components"]["Z3"]["weekly_q"] > 1.3*120
    assert ctx["components"]["Z3"]["annual_rate_percent"] == 0
    assert ctx["reference_is_clamped"] is False


def test_complete_wave_including_recovery_has_bounded_growth_not_mean_ratio_growth():
    p=configured();_,source,rows=observed();ctx=policy.context(p,source,rows,TODAY)
    base=planning_controls.reference(rows,TODAY)
    values=[]
    for i in range(4):
        day=TODAY+timedelta(days=7*i)
        goals,_,_=engine._goals(p,day,"GENERAL_PREPARATION",False,{},None,i,4,rows,TODAY,False,1,ctx)
        values.append(goals["Z3"])
    reference=ctx["components"]["Z3"]["weekly_effective"]
    rate=ctx["components"]["Z3"]["governed_annual_rate_percent"]
    assert sum(v["target"] for v in values)/4 == pytest.approx(reference*(1+rate/100)**(28/365.25))
    assert values[-1]["target"] < reference < values[2]["target"]
    for v in values:
        assert v["target_index"] == pytest.approx((base["Z3"]["b50"]+v["target"]/7)/(base["Z3"]["b50"]+base["Z3"]["c40"]))
    assert policy.context(p,source,rows,TODAY)==ctx


def test_phase_and_data_gates_never_invent_growth_or_strength_capacity():
    p=configured();_,source,rows=observed();ctx=policy.context(p,source,rows,TODAY)
    rates={}
    for period in ["GENERAL_PREPARATION","SPECIAL_PREPARATION","PRECOMPETITION","COMPETITION","TRANSITION","RE_ENTRY"]:
        g,_,_=engine._goals(p,TODAY,period,False,{},None,0,4,rows,TODAY,False,1,ctx)
        rates[period]=g["Z3"]["progression"]["effective_annual_percent"]
    assert rates["GENERAL_PREPARATION"] == rates["SPECIAL_PREPARATION"] > rates["PRECOMPETITION"] > rates["COMPETITION"] > 0
    assert rates["TRANSITION"] == rates["RE_ENTRY"] == 0
    g,_,_=engine._goals(p,TODAY,"PRECOMPETITION",True,{},None,0,4,rows,TODAY,False,.5,ctx)
    assert g["Z3"]["progression"]["effective_annual_percent"]==0
    assert ctx["components"]["STR"]["annual_rate_percent"] is None
    del source["activities"][0]["zones"][0]["equivalent_time_min"]
    # This missing activity is outside the frozen completed reference.
    assert policy.context(p,source,rows,TODAY)["components"]==ctx["components"]
    for a in source["activities"]:
        for z in a["zones"]: z.pop("equivalent_time_min",None)
    assert policy.context(p,source,rows,TODAY)["components"]["Z3"]["annual_rate_percent"] is None


def test_duration_accents_and_manual_choices():
    assert policy.accents(configured(race_duration_min=4),"SPECIAL_PREPARATION",[])==["Z4","Z5"]
    assert policy.accents(configured(race_duration_min=150),"SPECIAL_PREPARATION",[])==["Z2","Z3"]
    p=configured(race_duration_min=4);_,source,rows=observed()
    _,focus,_=engine._goals(p,TODAY,"SPECIAL_PREPARATION",False,{},None,0,4,rows,TODAY,False,1,policy.context(p,source,rows,TODAY))
    assert focus==["Z3"]


def response_fixture(positive=False):
    start=TODAY-timedelta(days=14);end=TODAY-timedelta(days=1)
    block={"start":start.isoformat(),"load_end":(TODAY-timedelta(days=7)).isoformat(),"recovery_end":end.isoformat(),"phase":"BUILD","components":["Z3"],"baseline":{"subjective":{"median":25,"spread":10,"count":28}}}
    entries=[{"kind":"BLOCK","entry_key":start.isoformat(),"revision":1,"payload":block}]
    for n in range(14):
        d=start+timedelta(days=n);value=2 if positive and n>=12 else 4
        entries.append({"kind":"DAILY","entry_key":d.isoformat(),"revision":1,"payload":{**dict.fromkeys(["sleep_quality","fatigue","soreness","stress","motivation"],value),"pain_or_illness":False}})
    protocol={"protocol":"same test","protocol_version":"1","unit":"W","direction":"HIGHER","conditions":"same indoor protocol","comparable":True,"components":["Z3"],"meaningful_change_percent":1}
    for d,value in [(start-timedelta(days=1),100),(TODAY,110 if positive else 90)]:
        entries.append({"kind":"TEST","entry_key":d.isoformat(),"revision":1,"payload":{**protocol,"day":d.isoformat(),"value":value}})
    rows=[{"date":(start+timedelta(days=n)).isoformat(),"zone":z,"effective_load":12 if n>=0 else 10}
          for n in range(-14,14) for z in engine.COMPONENTS]
    return entries,rows


def test_learning_replays_once_and_attributes_only_observed_component():
    entries,rows=response_fixture()
    a=load_adaptation.assess(entries,TODAY,rows=rows)
    assert a==load_adaptation.assess(entries,TODAY,rows=rows)
    assert a["components"]["Z3"]["growth_factor"]==.75
    assert a["components"]["Z3"]["load_factor"]==.9
    assert a["global"]["growth_factor"]==1 and "Z4" not in a["components"]
    assert a["recovery_is_input"] is False
    # No corresponding real load increase: no dose-response learning.
    assert not load_adaptation.assess(entries,TODAY,rows=[])["components"]
    entries[-1]["payload"]["conditions"]="different weather"
    assert not load_adaptation.assess(entries,TODAY,rows=rows)["components"]


def test_expected_stress_is_not_failure_and_missing_feedback_is_not_success():
    entries,rows=response_fixture(positive=True)
    a=load_adaptation.assess(entries,TODAY,rows=rows)
    assert a["evidence"][0]["status"]=="POSITIVE"
    assert a["components"]["Z3"]["load_factor"]==1
    assert load_adaptation.assess([],TODAY,rows=rows)["evidence"]==[]
    entries=entries[:2]+entries[-2:]
    assert not load_adaptation.assess(entries,TODAY,rows=rows)["components"]


def test_learned_response_survives_the_rolling_import_window():
    entries,rows=response_fixture()
    entries[-1]["payload"]["observed_load_windows"]=load_adaptation.load_observations(entries,rows,TODAY)
    before=load_adaptation.assess(entries,TODAY,rows=rows)
    after=load_adaptation.assess(entries,TODAY+timedelta(days=100),rows=[])
    assert after["components"]["Z3"]["growth_factor"] == before["components"]["Z3"]["growth_factor"]
    assert after["components"]["Z3"]["load_factor"]==1  # Temporary relief has expired.


def test_saving_outcome_preserves_actual_load_in_the_same_revision():
    from tests.api.test_response_monitoring import Repository as ReportRepository, ACTOR
    from apps.api.response_monitoring import OptionalTest
    from apps.api import response_service
    entries,rows=response_fixture()
    repo=ReportRepository(entries[:-1])
    source={"daily":[r for r in rows if r["zone"] != "STR"],
            "strength":{"daily":[r for r in rows if r["zone"] == "STR"]}}
    repo.active_activity_calendar=lambda *args:{"generation_id":"observed-generation","revision":3,"snapshot_payload":{"load_history":source}}
    response_service.save_report(repo,"ath-test","TEST",OptionalTest(**entries[-1]["payload"]),ACTOR,now=NOW)
    saved=repo.saved["p_payload"]
    assert saved["observed_load_windows"][0]["current"]["Z3"]==14*12
    assert saved["observed_load_windows"][0]["previous"]["Z3"]==14*10
    assert saved["load_source"]=={"generation_id":"observed-generation","revision":3}
    assert saved["automatic_weight"]==0  # Daily stress score remains independent.


@pytest.mark.parametrize("quality", [{}, {"excluded_activities": 1}])
def test_editing_old_outcome_retains_observed_windows_and_original_source(quality):
    from tests.api.test_response_monitoring import Repository as ReportRepository, ACTOR
    from apps.api.response_monitoring import OptionalTest
    from apps.api import response_service
    entries,rows=response_fixture()
    body=OptionalTest(**entries[-1]["payload"],expected_revision=1)
    frozen=load_adaptation.load_observations(entries,rows,TODAY)
    entries[-1]["payload"].update(observed_load_windows=frozen,
        load_source={"generation_id":"original-generation","revision":3})
    repo=ReportRepository(entries)
    repo.active_activity_calendar=lambda *args:{"generation_id":"new-generation","revision":5,
        "snapshot_payload":{"load_history":{"daily":[],"quality":quality}}}
    response_service.save_report(repo,"ath-test","TEST",body,ACTOR,now=NOW+timedelta(days=50))
    saved=repo.saved["p_payload"]
    window=saved["observed_load_windows"][0]
    assert window["current"]==frozen[0]["current"]
    assert window["previous"]==frozen[0]["previous"]
    assert window["source"]=={"generation_id":"original-generation","revision":3}
    assert window["retained_from"]=={"entry_key":entries[-1]["entry_key"],"revision":1}
    assert saved["load_observation_status"]=="ARCHIVED"
    entries[-1]["payload"]=saved
    assert load_adaptation.assess(entries,TODAY+timedelta(days=50),rows=[])["components"]["Z3"]["growth_factor"]==.75


def test_backdated_outcome_without_retained_history_is_explicitly_unavailable():
    from tests.api.test_response_monitoring import Repository as ReportRepository, ACTOR
    from apps.api.response_monitoring import OptionalTest
    from apps.api import response_service
    entries,_=response_fixture()
    repo=ReportRepository(entries[:-1])
    response_service.save_report(repo,"ath-test","TEST",OptionalTest(**entries[-1]["payload"]),ACTOR,now=NOW+timedelta(days=50))
    saved=repo.saved["p_payload"]
    assert saved["value"]==90
    assert saved["observed_load_windows"]==[]
    assert saved["load_observation_status"]=="UNAVAILABLE"
    entries[-1]["payload"]=saved
    assert not load_adaptation.assess(entries,TODAY+timedelta(days=50),rows=[])["components"]


def test_editing_earlier_outcome_prefers_its_own_frozen_evidence():
    from copy import deepcopy
    from hashlib import sha256
    from tests.api.test_response_monitoring import Repository as ReportRepository, ACTOR
    from apps.api.response_monitoring import OptionalTest
    from apps.api import response_service
    entries,rows=response_fixture()
    body=OptionalTest(**entries[-1]["payload"],expected_revision=1)
    key=sha256(f"{body.day}:{body.protocol}:{body.protocol_version}".encode()).hexdigest()[:32]
    entries[-1]["entry_key"]=key
    entries[-1]["payload"].update(observed_load_windows=load_adaptation.load_observations(entries,rows,TODAY),
        load_source={"generation_id":"original","revision":1})
    later=deepcopy(entries[-1])
    later["entry_key"]="later-outcome"
    later["payload"]["day"]=(TODAY+timedelta(days=1)).isoformat()
    later["payload"]["load_source"]={"generation_id":"later-correction","revision":2}
    later["payload"]["observed_load_windows"][0]["current"]["Z3"]=200
    repo=ReportRepository([*entries,later])
    response_service.save_report(repo,"ath-test","TEST",body,ACTOR,now=NOW+timedelta(days=50))
    window=repo.saved["p_payload"]["observed_load_windows"][0]
    assert window["current"]["Z3"]==168
    assert window["source"]=={"generation_id":"original","revision":1}
    assert window["retained_from"]["entry_key"]==key


def test_archived_window_is_not_reused_for_different_block_dates():
    entries,rows=response_fixture()
    entries[-1]["payload"]["observed_load_windows"]=load_adaptation.load_observations(entries,rows,TODAY)
    entries[0]["payload"]["recovery_end"]=(TODAY-timedelta(days=2)).isoformat()
    assert load_adaptation.load_observations(entries,[],TODAY)==[]


def test_new_week_obeys_whole_dose_time_and_no_catchup(monkeypatch):
    monkeypatch.setattr(engine.model_service,"speed_view",reference_speed)
    repo,_,_=observed();p=configured()
    p["planning_controls"].update(sessions_per_week=3,sessions_by_day=[0,1,0,1,0,1,0])
    p["available_minutes"]=[0,80,0,80,0,80,0]
    result=engine.generate_plan(repo,"athlete",p,start_date=TODAY+timedelta(days=1),now=NOW)
    sessions=[s for d in result["days"] for s in d["sessions"]]
    assert sessions and len(sessions)<=3
    for s in sessions:
        assert s["total_minutes"]<=80
        assert s["dose_evidence"]["applied_structure_fraction"]<=.8005
    assert result["parameters"]["load_progression"]["requires_catchup"] is False
    assert result["allocation"]["requires_catchup"] is False


def test_reported_illness_blocks_new_tasks_even_with_good_recovery(monkeypatch):
    monkeypatch.setattr(engine.model_service,"speed_view",reference_speed)
    repo,_,_=observed()
    repo.entries=[{"kind":"DAILY","entry_key":TODAY.isoformat(),"revision":1,"payload":{"pain_or_illness":True}}]
    result=engine.generate_plan(repo,"athlete",configured(),start_date=TODAY+timedelta(days=1),now=NOW)
    assert result["status"]=="BLOCKED" and not result["activation_eligible"]
    assert not any(d["sessions"] for d in result["days"])
    assert any(w["code"]=="REPORTED_ILLNESS_OR_PAIN" for w in result["warnings"])


def test_residual_z3_uses_supporting_work_without_extra_key_sessions(monkeypatch):
    monkeypatch.setattr(engine.model_service,"speed_view",reference_speed)
    repo,_,_=observed();p=configured()
    p["planning_controls"].update(intensity_days=[1,3],accent_index=1.5)
    result=engine.generate_plan(repo,"athlete",p,start_date=TODAY+timedelta(days=1),now=NOW)
    sessions=[(d,s) for d in result["days"] for s in d["sessions"]]
    keys=[d["date"] for d,s in sessions if s.get("is_key_session")]
    support=[(d,s) for d,s in sessions if s["purpose"]=="SUPPORTING"]
    assert len(keys)==2 and support
    for d,s in support:
        assert d["date"]>max(keys)
        assert d["readiness_before"]["Z3"]>=90
        assert sum(b["duration_min"] for b in s["blocks"] if b["kind"]=="WORK" and b["zone"]=="Z3")<=20
        assert s["dose_evidence"]["applied_structure_fraction"]<=p["maintenance_fraction"]+.001
    assert result["summary"]["key_sessions"]==2
    assert result["allocation"]["components"]["Z3"]["planned_effective"] <= result["allocation"]["components"]["Z3"]["target_effective"]+.001


def test_conditional_outlook_accumulates_only_in_eligible_phases():
    p=configured();_,source,rows=observed();ctx=policy.context(p,source,rows,TODAY)
    boundary=TODAY+timedelta(days=28)
    phases={"phases":[{"start_date":TODAY.isoformat(),"end_date":(boundary-timedelta(days=1)).isoformat(),"kind":"GENERAL_PREPARATION"},
                       {"start_date":boundary.isoformat(),"end_date":(boundary+timedelta(days=28)).isoformat(),"kind":"TRANSITION"}],"taper_windows":[]}
    projected=policy.projected_cycle_bases(ctx,phases,boundary+timedelta(days=28))
    assert projected[boundary.isoformat()]["Z3"]>1
    assert projected[(boundary+timedelta(days=28)).isoformat()]==projected[boundary.isoformat()]
    assert projected[boundary.isoformat()]["STR"]==1


def test_mixed_double_threshold_has_distinct_capacities_and_one_dose_budget():
    settings=Repository().settings
    first=next(m for m in engine.resolved_methods(configured()) if m["id"]=="END-THR-LONG-01")
    other={**first,"id":"individual-Z4","zone":"Z4","min_work_min":3,"warmup_min":10,"cooldown_min":5}
    method={**first,"double_threshold":True,"paired_method":other,"single_min_work_min":15,"min_work_min":30}
    cap={"capacity_minutes":100,"target_hr_bpm":150,"target_speed_kmh":None,
         "paired_capacity":{"zone":"Z4","capacity_minutes":20,"target_hr_bpm":170,"target_speed_kmh":None}}
    blocks=engine._blocks(method,50,cap,settings)
    assert sum(b["duration_min"] for b in blocks if b["kind"]=="WORK" and b["session_index"]==1)==25
    assert sum(b["duration_min"] for b in blocks if b["kind"]=="WORK" and b["session_index"]==2)==5
    assert engine._dose_usage(blocks,cap,"Z3")==pytest.approx(.5)


def test_interval_usage_counts_all_repetitions_against_structure_capacity():
    settings=Repository().settings
    p={"zone":"Z4","continuous_capacity_min":10,"assessed_on":TODAY.isoformat(),"effort":"Repeatable effort", "work_seconds":180,"recovery_seconds":180,"min_repetitions":3,"max_repetitions":6,"total_capacity_ratio":1.2,"reserve_repetitions":2}
    method={"structure":"METABOLIC_INTERVALS","zone":"Z4","warmup_min":12,"cooldown_min":8,"interval_profile":p,"instructions":"Controlled"}
    cap={"capacity_minutes":10,"effort_profile":p}
    assert engine._dose_usage(engine._blocks(method,12,cap,settings),cap,"Z4")==1
    assert engine._dose_usage(engine._blocks(method,9.6,cap,settings),cap,"Z4")==.75


def test_mixed_double_threshold_integration_uses_the_two_requested_zones(monkeypatch):
    from tests.api.test_management_schedule import body
    from tests.api.test_management_v2 import interval
    monkeypatch.setattr(engine.model_service,"speed_view",reference_speed)
    original=engine.recovery_v2.simulate
    def recovered(*args,**kwargs):
        result=original(*args,**kwargs)
        for row in result["current"]: row["readiness_percent"]=100
        return result
    # Isolate bundle construction; readiness rejection has separate integration coverage.
    monkeypatch.setattr(engine.recovery_v2,"simulate",recovered)
    p=body(sessions_per_week=12,double_threshold_days=[0],double_threshold_components=["Z3","Z4"],
           accent_mode="MANUAL",accents=["Z3","Z4"],accent_index=1.5)
    p["interval_profiles"]=[interval(goal="THRESHOLD",continuous_capacity_min=40,total_capacity_ratio=1.5,min_repetitions=3,max_repetitions=6)]
    p["load_progression"]=LoadProgression(feedback_enabled=False).model_dump()
    result=engine.generate_plan(Repository(),"athlete",p,start_date=TODAY,now=NOW)
    pair=result["days"][0]["sessions"]
    assert [s["zone"] for s in pair]==["Z3","Z4"]
    assert pair[0]["dose_evidence"]["capacity_minutes"] != pair[1]["dose_evidence"]["capacity_minutes"]
    assert all(s["dose_evidence"]["shared_day_structure_fraction"] <= .8 for s in pair)
    for s in pair:
        assert any(b["kind"]=="WARMUP" for b in s["blocks"])
        assert s["dose_evidence"]["prescribed_work_minutes"]==s["main_work_minutes"]


def test_illness_flag_needs_a_new_report_to_clear():
    entries=[{"kind":"DAILY","entry_key":(TODAY-timedelta(days=10)).isoformat(),"revision":1,"payload":{"pain_or_illness":True}}]
    assert load_adaptation.assess(entries,TODAY)["hold_for_reported_illness_or_pain"]
    entries.append({"kind":"DAILY","entry_key":TODAY.isoformat(),"revision":1,"payload":{"pain_or_illness":False}})
    assert not load_adaptation.assess(entries,TODAY)["hold_for_reported_illness_or_pain"]


def test_interval_capacity_uses_volume_correction_between_preserved_test_anchors():
    from tests.api.test_training_plan_engine import supported_speed
    from tests.api.test_management_v2 import interval
    settings=Repository().settings
    speed=supported_speed(settings)
    curve=engine.speed_duration.calibrated([t["payload"] for t in speed["tests"]])
    effort=interval(target_speed_kmh=curve.speed(1800)*3.6,speed_basis="FLAT_EQUIVALENT")
    method=next(m for m in engine.resolved_methods(profile(interval_profiles=[effort])) if m["structure"]=="METABOLIC_INTERVALS")
    uncorrected=engine.capacity_for(method,settings,speed,(None,[],[]),TODAY)
    speed["zone_corrections"]={z:.1 for z in policy.WEEKLY_Q_BOUNDS}
    corrected=engine.capacity_for(method,settings,speed,(None,[],[]),TODAY)
    assert corrected["capacity_source"]=="SPEED_DURATION"
    assert corrected["capacity_minutes"] != pytest.approx(uncorrected["capacity_minutes"])
