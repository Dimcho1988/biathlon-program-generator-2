import base64
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from apps.api import speed_zone_history as history
from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from apps.api.shadow_storage import encode_shadow_payload


TODAY = date(2026, 9, 29)
SETTINGS = SimpleNamespace(zone_bounds_bpm=(100,120,140,160,180,200), hrmax_bpm=200)
KEY = "a"*64


def inputs():
    day = (TODAY-timedelta(days=1)).isoformat()
    recorded = {"activity_ref":"run", "date":day, "sport":"Run", "duration_min":2.,
                "quality_status":"limited", "hr_coverage_percent":50.,
                "zones":[{"zone":"Z2", "raw_time_min":1., "equivalent_time_min":.6}]}
    calendar = [{**recorded, "local_date":day, "latest_shadow_run_key":KEY, "elapsed_time_s":120}]
    speeds = {"Run":{"sport":"Run", "index_window":{"last_activity_date":day,"days":40},
        "index_summary":{z:{"index":5.,"count":2,"seconds":900} for z in history.ZONES}}}
    shadow = {"configuration_fingerprint":history.activity_shadow_configuration_fingerprint(SETTINGS.zone_bounds_bpm,200,sport="Run"),
        "trainability_index":{"comparison_key":history.activity_shadow_configuration_fingerprint(SETTINGS.zone_bounds_bpm,200,sport="Run")},
        "vflat_model_version":history.speed_model_versions("Run")[0],"vflat_config_version":history.speed_model_versions("Run")[1],
        "timeseries":[{"elapsed_s":t,"hr_raw_bpm":130 if t<=60 else None} for t in range(121)],
        "speed_test_series":[{"elapsed_s":t,"dt_s":1.,"vflat_b65_kmh":15 if 40<t<=100 else 20,
                              "grade_smoothed_pct":0.,"exclusion_reason":None} for t in range(1,121)]}
    return {"activities":[recorded]}, calendar, speeds, shadow


class Repository:
    def __init__(self, shadow): self.shadow=shadow; self.calls=[]
    def activity_speed_history_samples(self, alias, keys):
        self.calls.append((alias,keys))
        return {KEY:{"activity_ref":"run","shadow_payload":self.shadow}}


def test_only_missing_hr_spans_use_measured_speed_and_paired_index_q():
    source, calendar, speeds, shadow = inputs()
    original = deepcopy((source,calendar,speeds,shadow))
    repo = Repository(shadow)
    estimates, diagnostic = history.prepare_history(repo,"athlete",source,calendar,speeds,SETTINGS,TODAY)
    estimate = estimates["run"]
    assert repo.calls == [("athlete",(KEY,))]
    assert estimate["covered_missing_minutes"] == pytest.approx(59/60)
    z3 = next(row for row in estimate["zones"] if row["zone"]=="Z3")
    # 15 km/h maps to estimated 150 bpm, hence Q=.7 per Z3 minute.
    assert z3["raw_time_min"] == pytest.approx(59/60)
    assert z3["equivalent_time_min"] == pytest.approx(59/60*.7)
    assert sum(row["raw_time_min"] for row in estimate["zones"] if row["zone"]!="Z3") == 0
    assert estimate["provenance"]["load"] == "ESTIMATED_NOT_MEASURED_HR"
    assert estimate["paired_lag_seconds"] == 20
    assert diagnostic["estimated_activity_count"] == 1
    assert (source,calendar,speeds,shadow) == original
    assert "tests" not in estimate and "index_summary" not in estimate


@pytest.mark.parametrize("change,reason", [
    (lambda v:v["Run"]["index_summary"].pop("Z5"),"INCOMPLETE_PAIRED_ZONE_BOUNDARIES"),
    (lambda v:v["Run"]["index_summary"]["Z2"].update(source="EXPERT_MIDPOINT"),"INCOMPLETE_PAIRED_ZONE_BOUNDARIES"),
    (lambda v:v["Run"]["index_summary"]["Z2"].update(index=50),"NONMONOTONE_PAIRED_ZONE_BOUNDARIES"),
    (lambda v:v["Run"]["index_window"].update(last_activity_date="2026-08-01"),"NO_RECENT_PAIRED_INDEX"),
    (lambda v:v["Run"].update(sport="Ride"),"NO_SPORT_SPECIFIC_INDEX")])
def test_unsupported_map_uses_no_raw_reads_and_leaves_expert_fallback(change,reason):
    source,calendar,speeds,shadow=inputs();change(speeds);repo=Repository(shadow)
    estimates,info=history.prepare_history(repo,"athlete",source,calendar,speeds,SETTINGS,TODAY)
    assert estimates == {} and repo.calls == []
    assert info["skipped"][0]["reason"] == reason


@pytest.mark.parametrize("change,reason", [
    (lambda s:s.update(vflat_config_version="old"),"INCOMPATIBLE_SPEED_CONFIGURATION"),
    (lambda s:s["trainability_index"].update(comparison_key="other-athlete-profile"),"INCOMPATIBLE_SPEED_CONFIGURATION"),
    (lambda s:s.pop("speed_test_series"),"INDEPENDENT_SPEED_SERIES_REQUIRED"),
    (lambda s:s.update(timeseries=[]),"PARTIAL_HR_MASK_UNAVAILABLE"),
    (lambda s:[r.update(hr_raw_bpm=500) for r in s["timeseries"] if r["hr_raw_bpm"] is None],"NO_SUPPORTED_MISSING_HR_SPEED_INTERVALS"),
    (lambda s:[r.update(grade_smoothed_pct=-4) for r in s["speed_test_series"]],"NO_SUPPORTED_MISSING_HR_SPEED_INTERVALS"),
    (lambda s:[r.update(vflat_b65_kmh=30) for r in s["speed_test_series"]],"NO_SUPPORTED_MISSING_HR_SPEED_INTERVALS")])
def test_incompatible_or_missing_signals_never_become_observed_load(change,reason):
    source,calendar,speeds,shadow=inputs();change(shadow)
    result,info=history.prepare_history(Repository(shadow),"athlete",source,calendar,speeds,SETTINGS,TODAY)
    assert result == {} and info["skipped"][0]["reason"] == reason


def test_no_hr_entire_record_still_preserves_uncovered_time_and_rejects_conflicts():
    source,calendar,speeds,shadow=inputs()
    source["activities"][0].update(hr_coverage_percent=0,zones=[])
    shadow["timeseries"]=[]
    result,_=history.prepare_history(Repository(shadow),"athlete",source,calendar,speeds,SETTINGS,TODAY)
    assert result["run"]["covered_missing_minutes"] == pytest.approx(100/60)
    shadow["timeseries"]=[{"elapsed_s":1,"hr_raw_bpm":130}]
    result,info=history.prepare_history(Repository(shadow),"athlete",source,calendar,speeds,SETTINGS,TODAY)
    assert not result and info["skipped"][0]["reason"]=="HR_MASK_COVERAGE_CONFLICT"


def test_complete_history_and_pinned_identity_fail_closed():
    source,calendar,speeds,shadow=inputs();repo=Repository(shadow)
    source["activities"][0]["quality_status"]="valid"
    assert history.prepare_history(repo,"athlete",source,calendar,speeds,SETTINGS,TODAY)[0]=={}
    assert repo.calls==[]
    source["activities"][0]["quality_status"]="limited"
    repo.activity_speed_history_samples=lambda alias,keys:{KEY:{"activity_ref":"other","shadow_payload":shadow}}
    result,info=history.prepare_history(repo,"athlete",source,calendar,speeds,SETTINGS,TODAY)
    assert not result and info["skipped"][0]["reason"]=="PINNED_SPEED_ACTIVITY_MISMATCH"


def test_batch_reader_uses_exact_keys_alias_scope_and_decodes_without_n_plus_one():
    keys=tuple(f"{i:064x}" for i in range(21));calls=[]
    _,_,_,shadow=inputs()
    encoded=encode_shadow_payload({**shadow,"speed_test_series":shadow["speed_test_series"]*20})
    class Client:
        def request(self,method,url,**kwargs):
            query=parse_qs(urlparse(url).query);calls.append(query)
            assert method=="GET" and query["athlete_alias"]==["eq.athlete"]
            assert "result_payload->speed_test_series" in query["select"][0]
            selected=query["run_key"][0][4:-1].split(",")
            assert len(selected)<=10
            return httpx.Response(200,json=[{"run_key":key,"activity_ref":"run",**encoded} for key in selected])
    repo=SupabasePilotRepository(supabase_url="https://project.supabase.co",secret_key="sb_secret_server-key",
        encryption_key=base64.urlsafe_b64encode(bytes(range(32))).decode(),client=Client())
    rows=repo.activity_speed_history_samples("athlete",keys)
    assert set(rows)==set(keys) and len(calls)==3
    assert len(rows[keys[0]]["shadow_payload"]["speed_test_series"])==2400
    with pytest.raises(PersistentStoreFailure):repo.activity_speed_history_samples("athlete",("unsafe,query",))
