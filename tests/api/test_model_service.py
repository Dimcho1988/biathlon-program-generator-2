from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
from types import SimpleNamespace
from uuid import UUID
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from apps.api import main, model_service as m
from apps.api.model_schemas import RecoveryConfigInput,SpeedTestInput,initial_settings
from apps.api.speed_segments import preview

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


def test_saved_anchor_unlocks_all_three_prediction_directions_and_errors_stay_inline():
    repo=SpeedStore()
    empty=m.speed_view(repo,"ath-test","Run",duration_s=720)
    assert empty["status"]=="REFERENCE_ONLY" and empty["prediction"] is None
    assert empty["prediction_error"]=="MAXIMAL_TEST_REQUIRED"
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
