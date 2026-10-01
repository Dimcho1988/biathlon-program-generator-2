from copy import deepcopy
from datetime import date,timedelta
from types import SimpleNamespace
import pytest
from apps.api import speed_load as model
from apps.api.trainability import compute_trainability

TODAY=date(2026,10,1)
BOUNDS=(100,120,140,160,180,200)
SETTINGS=SimpleNamespace(zone_bounds_bpm=BOUNDS,hrmax_bpm=200,timezone="UTC")


def test_sport_specific_speed_converts_to_equal_effort_with_transparent_fallback():
    def mapping(sport,index):
        return model.zone_mapping({"GENERAL":{"index":index,"count":1,"seconds":600}},BOUNDS,200,sport)[0]
    run=mapping("Run",5);ride=mapping("Ride",2.5)
    a=model.convert_speed(run,15);b=model.convert_speed(ride,30)
    assert a["zone"]==b["zone"]=="Z3"
    assert a["coefficient"]==b["coefficient"]==pytest.approx(.7)
    assert b["sport_hr_bpm"]==143 and a["sport_hr_bpm"]==150
    assert run["uses_general_index"] and ride["uses_general_index"]
    assert model.convert_speed(ride,60) is None
    assert model.zone_mapping({},BOUNDS,200,"Ride")== (None,"NO_PRIOR_SPORT_INDEX")
    fallback,_=model.zone_mapping({"GENERAL":{"index":5,"count":1,"seconds":600},"Z2":{"index":50,"count":1,"seconds":600}},BOUNDS,200,"Run")
    assert fallback["mapping_basis"]=="GENERAL_CONFLICT_FALLBACK"
    assert fallback["bounds_kmh"]==[10,12,14,16,18,20]
    assert model.zone_mapping({"Z1":{"index":5,"count":1,"seconds":600},"Z2":{"index":50,"count":1,"seconds":600}},BOUNDS,200,"Run")[1]=="NONMONOTONE_INDEX_BOUNDARIES"


def test_treadmill_can_use_prior_running_index_but_not_same_day_or_future_hr():
    repo=Repository()
    repo.add("treadmill","VirtualRun",TODAY,150,15)
    repo.add("run-today","Run",TODAY,150,15)
    empty=model.history_view(repo,"authorized-athlete",today=TODAY)
    assert empty["classified_minutes"]==0
    repo.add("run-earlier","Run",TODAY-timedelta(days=1),150,15)
    for row in repo.shadows["treadmill"]["shadow_payload"]["speed_test_series"]:
        row["grade_assumed_flat"]=True
    ready=model.history_view(repo,"authorized-athlete",today=TODAY)
    treadmill=next(a for a in ready["activities"] if a["activity_ref"]=="treadmill")
    assert treadmill["classified_minutes"]==pytest.approx(10)
    assert treadmill["mapping"]["index_sport"]=="Run"
    assert "TREADMILL_PRIOR_RUN_INDEX_FALLBACK" in ready["warnings"]
    assert "TREADMILL_GRADE_ASSUMED_FLAT" in ready["warnings"]
    repo.add("treadmill-earlier","VirtualRun",TODAY-timedelta(days=2),150,15)
    own=model.history_view(repo,"authorized-athlete",today=TODAY)
    assert "index_sport" not in next(a for a in own["activities"] if a["activity_ref"]=="treadmill")["mapping"]


class Repository:
    def __init__(self):
        self.activities=[];self.summaries={};self.shadows={}
    def add(self,key,sport,day,hr,speed):
        self.activities.append({"activity_ref":key,"sport":sport,"local_date":day.isoformat(),"duration_min":10.,"latest_shadow_run_key":key})
        series=[{"elapsed_s":t,"dt_s":1.,"vflat_b65_kmh":speed,"grade_smoothed_pct":0.,"grade_raw_pct":0.,"exclusion_reason":None} for t in range(1,601)]
        index=compute_trainability([{"elapsed_s":t,"hr_raw_bpm":hr} for t in range(622)],series,
            zone_bounds_bpm=BOUNDS,hrmax_bpm=200,activity_duration_s=600,hr_reference_offset_bpm=model.reference_offset(sport),
            comparison_key=model.activity_shadow_configuration_fingerprint(BOUNDS,200,sport=sport),
            source_versions={"vflat":model.speed_model_versions(sport)[0]})
        self.summaries[key]={"activity_ref":key,"trainability_index":index}
        self.shadows[key]={"activity_ref":key,"shadow_payload":{"vflat_model_version":model.speed_model_versions(sport)[0],
            "vflat_config_version":model.speed_model_versions(sport)[1],"speed_test_series":series}}
    def athlete_settings(self,alias):return SETTINGS
    def active_trainability_calendar(self,alias,start,end):
        assert alias=="authorized-athlete"
        return {"generation_id":"g","revision":1,"activities":deepcopy(self.activities)}
    def trainability_summaries(self,alias,keys):return deepcopy({k:self.summaries[k] for k in keys})
    def activity_speed_exposure_samples(self,alias,keys):
        assert alias=="authorized-athlete"
        return deepcopy({k:self.shadows[k] for k in keys})


def test_speed_load_is_causal_independent_and_preserves_uncovered_history():
    repo=Repository()
    repo.add("run-first","Run",TODAY-timedelta(days=3),150,15)
    repo.add("ride-first","Ride",TODAY-timedelta(days=3),143,30)
    repo.add("run-later","Run",TODAY-timedelta(days=1),150,15)
    repo.add("ride-later","Ride",TODAY-timedelta(days=1),143,30)
    before=deepcopy(repo.__dict__)
    result=model.history_view(repo,"authorized-athlete",today=TODAY)
    assert result["classified_minutes"]==pytest.approx(20)
    assert result["recorded_minutes"]==pytest.approx(40) and result["coverage_percent"]==pytest.approx(50)
    assert result["status"]=="PARTIAL" and result["load_role"]=="PARALLEL_ESTIMATE_NOT_ADDED_TO_HR"
    z3=result["zones"][2]
    assert z3["minutes"]==pytest.approx(20) and z3["equivalent_minutes"]==pytest.approx(14)
    assert len(result["daily"])==20  # First observed day through today, five zones.
    assert repo.__dict__==before
    # Current activity HR and same-day/future indices cannot explain its own Q.
    repo.summaries["ride-later"]["trainability_index"]["general"]["index"]=100
    repo.add("future","Ride",TODAY,190,15)
    again=model.history_view(repo,"authorized-athlete",today=TODAY)
    old=next(a for a in result["activities"] if a["activity_ref"]=="ride-later")
    new=next(a for a in again["activities"] if a["activity_ref"]=="ride-later")
    assert old==new
    repo.shadows["ride-later"]["shadow_payload"]["vflat_config_version"]="old"
    stale=model.history_view(repo,"authorized-athlete","Ride",today=TODAY)
    assert next(a for a in stale["activities"] if a["activity_ref"]=="ride-later")["reason"]=="SPEED_RECOMPUTATION_REQUIRED"


def test_same_day_index_cannot_bootstrap_load_and_gaps_cannot_fabricate_time():
    repo=Repository();repo.add("one","Run",TODAY,150,15);repo.add("two","Run",TODAY,150,15)
    result=model.history_view(repo,"authorized-athlete",today=TODAY)
    assert result["classified_minutes"]==0
    assert all(z["ratio_7_40"] is None for z in result["zones"])
    repo.activities[0]["local_date"]=(TODAY-timedelta(days=1)).isoformat()
    series=repo.shadows["two"]["shadow_payload"]["speed_test_series"]
    for row in series[:120]:row["exclusion_reason"]="COASTING"
    for row in series[120:240]:row["vflat_b65_kmh"]=100
    result=model.history_view(repo,"authorized-athlete",today=TODAY)
    assert result["classified_minutes"]==pytest.approx(6)
    assert result["activities"][-1]["excluded_speed_minutes"]==2
    assert result["activities"][-1]["outside_mapping_minutes"]==2


def test_route_requires_service_and_selected_athlete_before_reading(monkeypatch):
    from fastapi.testclient import TestClient
    from apps.api import main, dependencies
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN","test-service-secret")
    calls=[]
    monkeypatch.setattr(dependencies,"repository",lambda: "repository")
    monkeypatch.setattr(model,"history_view",lambda repo,alias,sport: calls.append((repo,alias,sport)) or {"status":"UNAVAILABLE"})
    with TestClient(main.app) as client:
        url="/api/v2/athlete/models/speed-load"
        assert client.get(url).status_code==401
        assert not calls
        response=client.get(url+"?sport=Ride&athlete_alias=wrong",headers={"Authorization":"Bearer test-service-secret","X-OnFlows-Athlete-Alias":"authorized-athlete"})
        assert response.status_code==200
        assert calls==[("repository","authorized-athlete","Ride")]


def test_large_histories_are_read_in_bounded_batches_instead_of_loading_all_samples():
    calls=[]
    def reader(alias,keys):
        assert alias=="athlete" and len(keys)<=10
        calls.append(keys)
        return {k:{"activity_ref":k} for k in keys}
    activities=[{"activity_ref":str(i),"latest_shadow_run_key":str(i)} for i in range(25)]
    iterator=model.activity_samples(SimpleNamespace(activity_speed_exposure_samples=reader),"athlete",activities)
    assert not calls
    first=next(iterator)
    assert first[0]["activity_ref"]==first[1]["activity_ref"]=="0" and len(calls)==1
    assert len(list(iterator))==24 and len(calls)==3
