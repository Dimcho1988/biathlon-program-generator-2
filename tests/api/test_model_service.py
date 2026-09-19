from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
from types import SimpleNamespace
from uuid import UUID
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from apps.api import main, model_service as m
from apps.api.model_schemas import RecoveryConfigInput,SpeedTestInput,initial_settings
from apps.api.speed_segments import preview, measure

NOW=datetime(2026,9,12,12,tzinfo=timezone.utc)
REF="act_"+"1"*32

class Store:
    def __init__(self): self.saved=[]; self.rows=[]
    def athlete_settings(self,alias):
        assert alias=="ath-test"
        return SimpleNamespace(timezone="Europe/Sofia",hrmax_bpm=190,zone_bounds_bpm=(100,125,145,160,175,190))
    def _request(self,method,path,**kwargs):
        if method=="GET":
            assert "athlete_alias=eq.ath-test" in path
            return self.rows
        self.saved.append(kwargs["json"])
        return {"saved":True,"revision":1}
    def _json(self,value): return value
    def active_activity_view(self,alias,ref):
        assert alias=="ath-test" and ref==REF
        return {"catalog_payload":{"sport":"Run","local_date":datetime.now(timezone.utc).date().isoformat()},
            "shadow_run_key":"a"*64,"shadow_payload":{"input_hash":"b"*64,"configuration_fingerprint":"c"*64,
            "vflat_model_version":"v-test","hrmod_model_version":"hr-test",
            "timeseries":[{"elapsed_s":t,"vflat_b65_kmh":20.,"grade_smoothed_pct":0.} for t in range(1000)]}}

def source():
    rows=[{"date":(NOW.date()-timedelta(days=i)).isoformat(),"zone":z,"effective_load":20 if i else 60}
          for i in range(41) for z in ("Z1","Z2","Z3","Z4","Z5")]
    return {"load_history":{"period_start":rows[-1]["date"],"period_end":NOW.date().isoformat(),"daily":rows},
        "training_status":{"zones":[{"zone":z} for z in ("Z1","Z2","Z3","Z4","Z5")]}}

def test_projection_is_causal_uses_e_and_does_not_mutate_source(monkeypatch):
    monkeypatch.setenv("ONFLOWS_RECOVERY_V2_ENABLED","true")
    original=source();copy=deepcopy(original)
    projected=m.project_recovery(Store(),"ath-test",original,now=NOW)
    h=projected["recovery_history"]
    assert original==copy
    assert h["schema_version"]=="recovery-history-v2" and h["ready_threshold_percent"]==90
    assert h["model"]["algorithm_version"]=="recovery-daily-e-biexponential-v2.2"
    assert h["current"][1]["baseline_daily_min"]==40
    assert h["current"][1]["baseline_raw_daily_min"]==20
    assert h["current"][1]["days_to_practical_recovery"]>1.5
    assert h["current"][1]["readiness_percent"]==projected["training_status"]["zones"][1]["recovery_readiness_percent"]
    cfg={"expected_revision":2,"zones":initial_settings()}
    cfg["zones"]["Z2"]["duration_coefficient"]=2
    changed=m.project_recovery(Store(),"ath-test",original,now=NOW,config=cfg)
    assert changed["recovery_history"]["model"]["parameter_fingerprint"]!=h["model"]["parameter_fingerprint"]
    assert changed["recovery_history"]["current"][1]["days_to_practical_recovery"]>h["current"][1]["days_to_practical_recovery"]
    cfg["zones"]["Z2"]["duration_coefficient"]=1
    cfg["zones"]["Z2"]["initial_daily_min"]=40
    with_base=m.project_recovery(Store(),"ath-test",original,now=NOW,config=cfg)
    assert with_base["recovery_history"]["current"][1]["baseline_daily_min"]==60
    assert with_base["recovery_history"]["current"][1]["days_to_practical_recovery"]<h["current"][1]["days_to_practical_recovery"]
    assert with_base["recovery_history"]["model"]["parameter_fingerprint"]!=h["model"]["parameter_fingerprint"]
    assert with_base["load_history"]==copy["load_history"]

def test_stale_source_is_explicit(monkeypatch):
    monkeypatch.setenv("ONFLOWS_RECOVERY_V2_ENABLED","true")
    projected=m.project_recovery(Store(),"ath-test",source(),now=NOW+timedelta(days=2))
    assert projected["recovery_history"]["source_stale"]
    assert "SOURCE_STALE_NO_NEW_LOAD_ASSUMPTION" in projected["recovery_history"]["warnings"]

def test_segment_uses_same_continuous_window_and_rejects_gaps_and_downhill():
    shadow=Store().active_activity_view("ath-test",REF)["shadow_payload"]
    result=m.segment_measurement(shadow,120,720)
    assert result["duration_s"]==720 and result["speed_kmh"]==20
    assert result["distance_m"]==pytest.approx(4000)
    for row in shadow["timeseries"][500:600]: row["vflat_b65_kmh"]=None
    with pytest.raises(HTTPException) as e: m.segment_measurement(shadow,120,720)
    assert e.value.status_code==422
    for row in shadow["timeseries"]: row["grade_smoothed_pct"]=-4
    with pytest.raises(HTTPException): m.segment_measurement(shadow,0,120)

def test_saved_test_has_pinned_measurement_and_actor():
    repo=Store()
    body=SpeedTestInput(activity_ref=REF,start_s=0,duration_s=720,maximal=True,comparable=True,conditions="same course")
    m.save_test(repo,"ath-test",body,UUID("11111111-1111-4111-8111-111111111111"))
    write=repo.saved[0]
    assert write["p_payload"]["speed_kmh"]==20
    assert write["p_payload"]["source_run_key"]=="a"*64
    assert write["p_alias"]=="ath-test" and write["p_actor"]=="11111111-1111-4111-8111-111111111111"
    assert write["p_payload"]["distance_basis"]=="VFLAT_EQUIVALENT"

def test_api_rejects_missing_session_actor_and_invalid_coefficients(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN","service-secret")
    repo=Store();monkeypatch.setattr(main,"_repository",lambda:repo)
    client=TestClient(main.app)
    headers={"Authorization":"Bearer service-secret","X-OnFlows-Athlete-Alias":"ath-test","X-OnFlows-Actor-Id":"11111111-1111-4111-8111-111111111111"}
    payload={"expected_revision":0,"zones":initial_settings()}
    assert client.put("/api/v2/athlete/models/recovery",json=payload).status_code==401
    assert client.put("/api/v2/athlete/models/recovery",json=payload,headers={k:v for k,v in headers.items() if k!="X-OnFlows-Actor-Id"}).status_code==401
    bad=deepcopy(payload);bad["zones"]["Z2"]["shape"]=0
    assert client.put("/api/v2/athlete/models/recovery",json=bad,headers=headers).status_code==422
    assert client.put("/api/v2/athlete/models/recovery",json=payload,headers=headers).status_code==200
    assert len(repo.saved)==1

def test_unknown_settings_keys_and_incomplete_zones_rejected():
    cfg=initial_settings();cfg["Z2"]["tau_days"]=2
    with pytest.raises(ValueError): RecoveryConfigInput(zones=cfg)
    cfg=initial_settings();cfg.pop("STR")
    with pytest.raises(ValueError): RecoveryConfigInput(zones=cfg)


def test_independent_vflat_ignores_hr_exclusions_and_obeys_active_interval_bounds():
    shadow={"timeseries":[{"elapsed_s":t,"exclusion_reason":"MISSING_HR"} for t in range(721)],
        "speed_test_series":[{"elapsed_s":t,"dt_s":1 if t else 0,"vflat_b65_kmh":18 if t<=120 else 21.6,
        "grade_smoothed_pct":0.,"exclusion_reason":None} for t in range(721)]}
    result=m.segment_measurement(shadow,120,600)
    assert result["coverage_percent"]==100
    assert result["speed_kmh"]==pytest.approx(21.6)
    assert result["distance_m"]==pytest.approx(3600)
    for r in shadow["speed_test_series"][300:400]:r["dt_s"]=0
    with pytest.raises(HTTPException):m.segment_measurement(shadow,120,600)


def test_preview_measures_without_writing_and_matches_saved_test():
    repo=Store()
    result=preview(repo,"ath-test",REF,120,720)
    assert repo.saved==[]
    assert result["status"]=="READY" and len(result["series"])<=360
    # 1000 seconds / 360 buckets is not an integer. Splitting sample overlaps
    # must still show full coverage in every display bucket of this clean trace.
    assert all(p["eligible_fraction"]==pytest.approx(1) for p in result["series"])
    selection=result["selection"]
    assert selection["eligible"] and selection["coverage_percent"]==100
    body=SpeedTestInput(activity_ref=REF,start_s=120,duration_s=720,maximal=True,comparable=True,
        conditions="same course",expected_source_run_key=result["source_run_key"])
    m.save_test(repo,"ath-test",body,UUID("11111111-1111-4111-8111-111111111111"))
    for key in ("speed_kmh","distance_m","coverage_percent","duration_s"):
        assert repo.saved[0]["p_payload"][key]==selection[key]


def test_preview_reports_downhill_pauses_and_missing_analysis_without_a_prediction():
    repo=Store();row=repo.active_activity_view("ath-test",REF)
    for sample in row["shadow_payload"]["timeseries"][100:200]: sample["grade_smoothed_pct"]=-4
    row["shadow_payload"]["timeseries"]=row["shadow_payload"]["timeseries"][:500]+row["shadow_payload"]["timeseries"][600:]
    repo.active_activity_view=lambda alias,ref:row
    result=preview(repo,"ath-test",REF,0,720)["selection"]
    assert not result["eligible"] and result["speed_kmh"] is None
    assert result["coverage_percent"]==pytest.approx(100*520/720)
    assert result["excluded_seconds"]=={"downhill":100,"invalid":0,"missing_or_paused":100}
    row["shadow_payload"]=None
    assert preview(repo,"ath-test",REF)["status"]=="ANALYSIS_REQUIRED"
    assert repo.saved==[]


def test_preview_and_save_reject_outside_bounds_and_stale_analysis():
    repo=Store()
    assert preview(repo,"ath-test",REF,900,120)["selection"]["status"]=="OUTSIDE_ACTIVITY"
    body=SpeedTestInput(activity_ref=REF,start_s=0,duration_s=720,maximal=True,comparable=True,
        conditions="same course",expected_source_run_key="e"*64)
    with pytest.raises(HTTPException) as error:
        m.save_test(repo,"ath-test",body,UUID("11111111-1111-4111-8111-111111111111"))
    assert error.value.status_code==409 and repo.saved==[]
    with pytest.raises(HTTPException):m.segment_measurement(repo.active_activity_view("ath-test",REF)["shadow_payload"],900,120)


def test_fractional_last_sample_intersects_the_selected_window():
    shadow={"speed_test_series":[{"elapsed_s":t+.5,"dt_s":1,"vflat_b65_kmh":18 if t<720 else 36,
        "grade_smoothed_pct":0} for t in range(1000)]}
    result=m.segment_measurement(shadow,120,600)
    assert result["coverage_percent"]==100
    assert result["speed_kmh"]==pytest.approx((599.5*18+.5*36)/600)


def test_preview_endpoint_requires_athlete_session_and_valid_window(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN","service-secret")
    repo=Store();monkeypatch.setattr(main,"_repository",lambda:repo)
    client=TestClient(main.app)
    url=f"/api/v2/athlete/models/speed-preview?activity_ref={REF}"
    headers={"Authorization":"Bearer service-secret","X-OnFlows-Athlete-Alias":"ath-test"}
    assert client.get(url).status_code==401
    assert client.get(url,headers={"Authorization":"Bearer service-secret"}).status_code==401
    for suffix in ("&start_s=-1&duration_s=120","&start_s=1","&start_s=0&duration_s=10","&start_s=0&duration_s=nan"):
        assert client.get(url+suffix,headers=headers).status_code==422
    assert client.get(url+"&start_s=120&duration_s=720",headers=headers).json()["selection"]["eligible"]
    assert repo.saved==[]


class SpeedStore(Store):
    def active_activity_calendar(self,alias,start,today):
        return {"activities":[{"activity_ref":REF,"sport":"Run","local_date":today.isoformat()}],"snapshot_payload":{}}


@pytest.mark.parametrize("history_days",[7,40])
def test_shared_zone_volume_keeps_tests_and_hr_mapping_sport_specific(monkeypatch,history_days):
    from apps.api.activity_shadow_pipeline import activity_shadow_configuration_fingerprint
    from vflat_b65 import MODEL_VERSION as VF_VERSION, CONFIG_VERSION as VF_CONFIG
    class FixedDatetime(datetime):
        @classmethod
        def now(cls,tz=None): return NOW.astimezone(tz)
    monkeypatch.setattr(m,"datetime",FixedDatetime)
    today=NOW.date();first=today-timedelta(days=history_days)
    sports={"Run":(100,60,15),"NordicSki":(900,540,25),"Ride":(1400,1200,30)}
    repo=SpeedStore()
    activities=[];loads=[]
    for i,(sport,(z1,z2,speed)) in enumerate(sports.items()):
        day=(max(first,today-timedelta(days=39)) if i==0 else today-timedelta(days=1)).isoformat()
        activities.append({"activity_ref":sport,"sport":sport,"local_date":day,"latest_shadow_run_key":sport})
        loads.append({"date":day,"sport":sport,"zones":[
            {"zone":zone,"equivalent_time_min":q,"effective_load":99999}
            for zone,q in (("Z1",z1),("Z2",z2),("STR",99999))]})
        repo.rows.append({"kind":"SPEED_TEST","entry_key":sport,"revision":1,"payload":{
            "sport":sport,"day":day,"enabled":True,"duration_s":600,"speed_kmh":speed,
            "vflat_version":VF_VERSION,"vflat_config_version":VF_CONFIG}})
    for day in (first-timedelta(days=1),today,today+timedelta(days=1)):
        loads.append({"date":day.isoformat(),"sport":"Run","zones":[{"zone":"Z1","equivalent_time_min":99999}]})
    calendar={"activities":activities,"snapshot_payload":{"load_history":{
        "period_start":first.isoformat(),"period_end":(today+timedelta(days=1)).isoformat(),"activities":loads}}}
    original=deepcopy(calendar)
    repo.active_activity_calendar=lambda alias,start,end:calendar
    requested=[]
    def summaries(alias,keys):
        assert alias=="ath-test"
        requested.append(keys)
        from apps.api.trainability import MODEL_VERSION,SCHEMA_VERSION
        return {sport:{"activity_ref":sport,"trainability_index":{
            "schema_version":SCHEMA_VERSION,"model_version":MODEL_VERSION,
            "comparison_key":activity_shadow_configuration_fingerprint([100,125,145,160,175,190],190),
            "hrmax_bpm":190,"zone_bounds_bpm":[100,125,145,160,175,190],
            "signal_quality":{"status":"PASSED_SCREEN","reason":None},
            "source_versions":{"vflat":VF_VERSION},"zones":[
                {"name":zone,"valid":True,"hr_seconds":600,"index":100*hr/190/v}
                for zone,v,hr in (("Z1",sports[sport][2]*.6,115),("Z2",sports[sport][2],135),
                                  ("Z3",sports[sport][2]*1.2,152))],
            "general":{"name":"GENERAL","valid":False}}} for sport in keys}
    repo.trainability_summaries=summaries
    expected={"Z1":2400*7/history_days,"Z2":1800*7/history_days,"Z3":0,"Z4":0,"Z5":0}
    models=[]
    for sport,(_,_,speed) in sports.items():
        model=m.speed_view(repo,"ath-test",sport,duration_s=600)
        models.append(model)
        assert model["volume_scope"]=="ALL_SPORTS" and model["history_days"]==history_days
        assert model["volume_weekly_min"]==pytest.approx(expected)
        assert model["active_test_keys"]==[sport]
        assert model["prediction"]["speed_kmh"]==pytest.approx(speed)
        assert model["index_summary"]["Z2"]["index"]==pytest.approx(100*135/190/speed)
        assert model["volume_position_basis"]=="EXPERT_DURATION"
        hr_model=m.speed_view(repo,"ath-test",sport,hr_bpm=155)
        inverse=m.speed_view(repo,"ath-test",sport,speed_kmh=hr_model["prediction"]["speed_kmh"])
        assert inverse["prediction"]["estimated_hr_bpm"]==pytest.approx(155)
        assert requested[-1]==tuple(sports)
    assert all(model["zone_corrections"]==models[0]["zone_corrections"] for model in models)
    assert models[0]["zone_corrections"]["Z1"]==pytest.approx(-.04 if history_days==40 else .1)
    assert calendar==original and repo.saved==[]


def test_saved_anchor_unlocks_all_three_prediction_directions_and_errors_stay_inline():
    repo=SpeedStore()
    empty=m.speed_view(repo,"ath-test","Run",duration_s=720)
    assert empty["status"]=="REFERENCE_ONLY" and empty["prediction"] is None
    assert empty["prediction_error"]=="MAXIMAL_TEST_REQUIRED"
    assert empty["volume_weekly_min"]==dict.fromkeys(("Z1","Z2","Z3","Z4","Z5"))
    assert set(empty["zone_corrections"].values())=={0}
    body=SpeedTestInput(activity_ref=REF,start_s=0,duration_s=720,maximal=True,comparable=True,conditions="same course")
    m.save_test(repo,"ath-test",body,UUID("11111111-1111-4111-8111-111111111111"))
    write=repo.saved[0]
    repo.rows=[{"kind":"SPEED_TEST","entry_key":write["p_key"],"revision":1,"payload":write["p_payload"]}]
    for kwargs in ({"duration_s":720},{"distance_m":4000},{"speed_kmh":20}):
        model=m.speed_view(repo,"ath-test","Run",**kwargs)
        assert model["status"]=="CALIBRATED" and model["active_test_count"]==1
        assert model["prediction"]["duration_s"]==pytest.approx(720)
        assert model["prediction"]["speed_kmh"]==pytest.approx(20)
        assert model["prediction"]["distance_m"]==pytest.approx(4000)
    outside=m.speed_view(repo,"ath-test","Run",duration_s=2)
    assert outside["prediction"] is None and outside["prediction_error"]=="OUTSIDE_PREDICTION_RANGE"
    assert outside["points"] and outside["status"]=="CALIBRATED"
    assert m.speed_view(repo,"ath-test","Ride")["status"]=="REFERENCE_ONLY"


class ComplexSpeedStore(SpeedStore):
    def active_activity_view(self,alias,ref):
        row=super().active_activity_view(alias,ref)
        row["catalog_payload"]["sport"]="Walk"
        row["shadow_payload"]["speed_test_series"]=[
            {"elapsed_s":t,"dt_s":0 if t%10==9 else 1,
             "vflat_b65_kmh":80 if t%10==8 else 20,"grade_smoothed_pct":-4 if t%10==8 else 0}
            for t in range(1,1001)]
        return row


def exploratory_input(**extra):
    return SpeedTestInput(**{"activity_ref":REF,"start_s":0,"duration_s":1000,"test_mode":"EXPLORATORY",
        "maximal":False,"exploratory_confirmed":True,"comparable":True,"conditions":"Complex synthetic control",
        **extra})


def test_exploratory_walk_preview_save_and_prediction_share_coverage_and_elapsed_basis():
    repo=ComplexSpeedStore()
    strict=preview(repo,"ath-test",REF,0,1000)["selection"]
    assert not strict["eligible"] and strict["speed_kmh"] is None
    trial=preview(repo,"ath-test",REF,0,1000,"EXPLORATORY")["selection"]
    assert trial["eligible"] and trial["minimum_coverage_percent"]==70
    assert trial["coverage_percent"]==80
    assert trial["duration_s"]==1000 and trial["measured_duration_s"]==800
    assert trial["speed_kmh"]==20
    assert trial["distance_m"]==pytest.approx(20*1000/3.6)
    assert trial["measured_distance_m"]==pytest.approx(20*800/3.6)
    assert trial["excluded_seconds"]=={"downhill":100,"invalid":0,"missing_or_paused":100}
    assert repo.saved==[]
    strict_body=SpeedTestInput(activity_ref=REF,start_s=0,duration_s=1000,maximal=True,comparable=True,conditions="same course")
    actor=UUID("11111111-1111-4111-8111-111111111111")
    with pytest.raises(HTTPException):m.save_test(repo,"ath-test",strict_body,actor)
    assert repo.saved==[]
    m.save_test(repo,"ath-test",exploratory_input(),actor)
    write=repo.saved[0];payload=write["p_payload"]
    for key in ("test_mode","speed_kmh","coverage_percent","duration_s","measured_duration_s","measured_distance_m","excluded_seconds"):
        assert payload[key]==trial[key]
    assert payload["sport"]=="Walk" and payload["maximal"] is False and payload["use_for_cs"] is False
    repo.rows=[{"kind":"SPEED_TEST","entry_key":write["p_key"],"revision":1,"payload":payload}]
    result=m.speed_view(repo,"ath-test","Walk",duration_s=1000)
    assert result["status"]=="CALIBRATED" and result["exploratory_test_count"]==1
    assert "EXPLORATORY_CALIBRATION" in result["warnings"]
    assert result["prediction"]["speed_kmh"]==pytest.approx(20)
    assert result["critical_speed"]["count"]==0
    assert m.speed_view(repo,"ath-test","NordicSki")["status"]=="REFERENCE_ONLY"


def test_exploratory_floor_and_attestations_do_not_relax_strict_tests():
    assert measure([(0,700,20,None)],0,1000,"EXPLORATORY")["eligible"]
    assert not measure([(0,699.9,20,None)],0,1000,"EXPLORATORY")["eligible"]
    assert not measure([(0,700,20,None)],0,1000,"STRICT")["eligible"]
    assert measure([],0,1000,"EXPLORATORY")["speed_kmh"] is None
    for change in ({"exploratory_confirmed":False},{"maximal":True},{"use_for_cs":True},{"test_mode":"ANYTHING"}):
        with pytest.raises(ValueError):exploratory_input(**change)
    with pytest.raises(ValueError):exploratory_input(test_mode="STRICT")
    with pytest.raises(HTTPException):preview(ComplexSpeedStore(),"ath-test",REF,0,1000,"ANYTHING")


def test_preview_api_respects_explicit_mode_and_existing_auth(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN","service-secret")
    repo=ComplexSpeedStore();monkeypatch.setattr(main,"_repository",lambda:repo)
    client=TestClient(main.app)
    url=f"/api/v2/athlete/models/speed-preview?activity_ref={REF}&start_s=0&duration_s=1000"
    headers={"Authorization":"Bearer service-secret","X-OnFlows-Athlete-Alias":"ath-test"}
    assert not client.get(url,headers=headers).json()["selection"]["eligible"]
    assert client.get(url+"&test_mode=EXPLORATORY",headers=headers).json()["selection"]["eligible"]
    assert client.get(url+"&test_mode=EXPLORATORY").status_code==401
    assert client.get(url+"&test_mode=OPEN",headers=headers).status_code==422
    assert repo.saved==[]
