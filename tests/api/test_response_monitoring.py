from apps.api import dependencies
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from apps.api import main, response_service
from apps.api.response_monitoring import (
    FIELDS, DailyReport, SessionReport, ResponseBlock, OptionalTest,
    baselines, build_history, block_summary, session_rows, latest_entries,
)

TODAY = date(2026, 9, 9)
NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
ACTOR = UUID("11111111-1111-4111-8111-111111111111")
HEADERS = {"Authorization":"Bearer service-secret", "X-OnFlows-Athlete-Alias":"ath-test", "X-OnFlows-Actor-Id":str(ACTOR)}


def report(day, value=2, **extra):
    return {"day":str(day), "observed_at":None, **{f:value for f in FIELDS}, "pain_or_illness":False, "note":"", **extra}


def entry(kind, key, payload, revision=1):
    return {"kind":kind, "entry_key":str(key), "payload":payload, "revision":revision, "recorded_at":"2026-09-09T12:00:00Z"}


def activity(day, n=1, sport="RollerSki", zones=None):
    return {"activity_ref":f"act_{n:032x}", "local_date":str(day), "name":"Fixture session", "sport":sport, "elapsed_time_s":3600,
        "canonical_summary":{"zones":[{"zone":f"Z{i+1}","equivalent_time_s":v} for i,v in enumerate(zones or [1,2,4,2,1])]}}


def inputs():
    entries, wellness, activities = [], [], []
    for i in range(1,29):
        day = TODAY-timedelta(days=i)
        entries.append(entry("DAILY",day,report(day)))
        wellness.append({"date":str(day),"metrics":{"resting_hr":{"value":50,"unit":"bpm"},"hrv":{"value":60,"unit":"ms"}}})
    entries.append(entry("DAILY",TODAY,report(TODAY,3)))
    wellness.append({"date":str(TODAY),"metrics":{"resting_hr":{"value":50,"unit":"bpm"},"hrv":{"value":60,"unit":"ms"}}})
    for n,ago in enumerate([10,7,4,1],1):
        a = activity(TODAY-timedelta(days=ago),n)
        activities.append(a)
        entries.append(entry("SESSION",a["activity_ref"],{"rpe":5,"duration_minutes":60,"timing":"DELAYED"}))
    return dict(entries=entries,wellness=wellness,activities=activities,start=TODAY,end=TODAY,today=TODAY)


def test_fixed_weights_exact_contributions_and_no_controller_mutations():
    data = inputs()
    untouched = deepcopy(data)
    result = build_history(**data)
    day = result["days"][0]
    assert day["total"] == 50
    assert [g["contribution"] for g in day["groups"]] == [25,15,10]
    assert day["coverage"] == 100 and day["automatic_action"] == "NONE"
    assert not result["automatic_increase"] and not result["changes_recovery"]
    assert data == untouched


@pytest.mark.parametrize("missing", ["subjective","rpe","physiology"])
def test_missing_components_break_total_never_reweight(missing):
    data = inputs()
    if missing=="subjective":
        data["entries"] = [e for e in data["entries"] if e["entry_key"]!=str(TODAY)]
    elif missing=="rpe":
        data["activities"] = []
    else:
        data["wellness"][-1]["metrics"].pop("hrv")
    d = build_history(**data)["days"][0]
    assert d["total"] is None
    assert d["coverage"] == {"subjective":50,"rpe":70,"physiology":80}[missing]
    assert next(g for g in d["groups"] if g["key"]==missing)["contribution"] is None


def test_baseline_requires_14_prior_days_excludes_present_and_future():
    daily = {str(TODAY-timedelta(days=i)):report(TODAY-timedelta(days=i)) for i in range(1,14)}
    daily[str(TODAY)] = report(TODAY,5)
    daily[str(TODAY+timedelta(days=1))] = report(TODAY+timedelta(days=1),5)
    assert baselines(daily,{},TODAY)["subjective"] is None
    daily[str(TODAY-timedelta(days=14))] = report(TODAY-timedelta(days=14))
    assert baselines(daily,{},TODAY)["subjective"] == {"median":25,"spread":10,"count":14}


def test_sdnn_and_invalid_device_values_do_not_replace_rmssd():
    data = inputs()
    data["wellness"][-1]["metrics"] = {"hrv_sdnn":{"value":80},"resting_hr":{"value":0},"hrv":{"value":-10}}
    d = build_history(**data)["days"][0]
    assert d["physiology"]["resting_hr"]["raw"] is None
    assert d["physiology"]["hrv"]["raw"] is None
    assert d["total"] is None


def test_rpe_manual_overrides_provider_and_unknown_duration_is_not_srpe():
    a = activity(TODAY)
    a["provider_rpe"] = 8
    assert session_rows([a],{})[0]["srpe_load"] is None
    selected = latest_entries([entry("SESSION",a["activity_ref"],{"rpe":0,"duration_minutes":60,"timing":"DELAYED"})])
    r = session_rows([a],selected)[0]
    assert r["rpe"] == 0 and r["provider_rpe"] == 8 and r["srpe_load"] == 0
    assert r["source"] == "ONFLOWS"


@pytest.mark.parametrize("mismatch", ["sport","duration","timing","zones","future"])
def test_rpe_requires_three_prior_comparable_sessions(mismatch):
    data = inputs()
    first = data["activities"][0]
    manual = next(e["payload"] for e in data["entries"] if e["entry_key"]==first["activity_ref"])
    if mismatch=="sport": first["sport"]="Running"
    if mismatch=="duration": manual["duration_minutes"]=120
    if mismatch=="timing": manual["timing"]="NEXT_DAY"
    if mismatch=="zones": first["canonical_summary"]=activity(TODAY,zones=[10,0,0,0,0])["canonical_summary"]
    if mismatch=="future": first["local_date"]=str(TODAY+timedelta(days=1))
    d = build_history(**data)["days"][0]
    assert d["rpe_sessions"][0]["comparable_count"] == 2
    assert d["rpe_sessions"][0]["expected_rpe"] is None and d["total"] is None


def test_low_stress_never_increases_load_and_pain_is_visible():
    data = inputs()
    data["entries"].append(entry("DAILY",TODAY,report(TODAY,1,pain_or_illness=True),2))
    d = build_history(**data)["days"][0]
    assert d["state"] == "REVIEW" and d["automatic_action"] == "NONE"
    assert d["daily_revision"] == 2


def block():
    return {"start":"2026-09-01","load_end":"2026-09-04","recovery_end":"2026-09-09","phase":"BUILD",
        "baseline":{"subjective":{"median":25,"spread":10,"count":14}},"baseline_frozen_on":"2026-08-30"}


def test_block_return_resets_on_relapse_or_missing_day_and_chart_zoom_is_independent():
    b = block()
    daily = {f"2026-09-{i:02}":report(f"2026-09-{i:02}",2 if i>=5 else 4) for i in range(1,10)}
    assert block_summary(b,daily,TODAY)["returned_on"] == "2026-09-06"
    daily[str(TODAY)] = report(TODAY,4)
    s = block_summary(b,daily,TODAY)
    assert s["status"] == "REVIEW" and s["returned_on"] is None
    del daily[str(TODAY)]
    s = block_summary(b,daily,TODAY)
    assert s["status"] == "INSUFFICIENT_DATA" and s["returned_on"] is None
    entries = [entry("DAILY",k,v) for k,v in daily.items()] + [entry("BLOCK",b["start"],b)]
    view = build_history(entries=entries,wellness=[],activities=[],start=TODAY,end=TODAY,today=TODAY)
    assert view["blocks"][0]["summary"] == s
    assert s["tracked_days"] == 9 and s["observed_days"] == 8


def test_high_subjective_response_in_build_does_not_move_recovery_goalposts():
    b = block()
    data = inputs()
    data["start"] = date(2026,9,1)
    data["entries"].append(entry("BLOCK",b["start"],b))
    data["entries"].append(entry("DAILY","2026-09-01",report("2026-09-01",4),2))
    days = build_history(**data)["days"]
    assert days[0]["state"] == "EXPECTED_ELEVATION"
    assert days[0]["baseline_anchor"] == "2026-08-30"
    assert days[-1]["phase"] == "RECOVERY" and days[-1]["state"] == "REVIEW_AFTER_RECOVERY"


class Repository:
    def __init__(self, entries=None):
        self.saved = None
        self.rows = entries or []

    def athlete_settings(self, alias):
        assert alias=="ath-test"
        return SimpleNamespace(timezone="Europe/Sofia")

    def active_activity_calendar(self, alias, start, end):
        return {"revision":22,"generation_id":"pinned", "snapshot_payload":{"wellness_calendar":inputs()["wellness"]},"activities":inputs()["activities"]}

    def activity_detail(self, alias, ref):
        return activity(TODAY) if ref==activity(TODAY)["activity_ref"] else None

    def _request(self, method, path, **kwargs):
        if method=="GET":
            assert "athlete_alias=eq.ath-test" in path
            return self.rows
        self.saved = kwargs["json"]
        return {"saved":True,"revision":1}

    def _json(self, r): return r


def test_service_uses_pinned_wellness_and_athlete_timezone():
    repo = Repository()
    # 22:30 UTC is next day in Bulgaria, including the form's maximum date.
    h = response_service.history(repo,"ath-test",None,None,now=datetime(2026,9,8,22,30,tzinfo=timezone.utc))
    assert h["today"]=="2026-09-09" and h["revision"]==22
    assert h["days"][-1]["physiology"]["hrv"]["raw"]==60


def test_daily_save_separate_namespace_revision_and_no_recompute():
    repo = Repository()
    response_service.save_report(repo,"ath-test","DAILY",DailyReport(**report(TODAY),expected_revision=4),ACTOR,now=NOW)
    assert repo.saved["p_expected_revision"]==4 and repo.saved["p_actor"]==str(ACTOR)
    assert repo.saved["p_key"]==str(TODAY)
    assert repo.saved["p_payload"]["scale_version"]=="wellness-1-5-higher-worse-v1"


def test_block_freezes_pre_creation_history_and_started_blocks_cannot_be_edited():
    repo = Repository(inputs()["entries"])
    body = ResponseBlock(start="2026-09-10",load_end="2026-09-12",recovery_end="2026-09-15",phase="BUILD")
    response_service.save_report(repo,"ath-test","BLOCK",body,ACTOR,now=NOW)
    stored = repo.saved["p_payload"]
    assert stored["baseline"]["subjective"]["median"] == 25
    assert stored["baseline_frozen_on"]=="2026-09-09"
    repo.rows.append(entry("BLOCK","2026-09-10",stored))
    with pytest.raises(HTTPException) as error:
        response_service.save_report(repo,"ath-test","BLOCK",body,ACTOR,now=NOW+timedelta(days=1))
    assert error.value.status_code==409


def test_foreign_activity_and_future_daily_are_rejected():
    repo = Repository()
    with pytest.raises(HTTPException) as error:
        response_service.save_report(repo,"ath-test","SESSION",SessionReport(activity_ref="act_"+"f"*32,rpe=5,duration_minutes=60,timing="DELAYED"),ACTOR,now=NOW)
    assert error.value.status_code==404
    with pytest.raises(HTTPException):
        response_service.save_report(repo,"ath-test","DAILY",DailyReport(**report(TODAY+timedelta(days=1))),ACTOR,now=NOW)
    assert repo.saved is None


def test_write_conflicts_are_not_retried():
    repo = Repository()
    calls = []
    def request(*args,**kwargs):
        calls.append(args)
        return {"conflict":True,"revision":2}
    repo._request = request
    with pytest.raises(HTTPException) as e:
        response_service.ResponseStore(repo).save("ath-test","DAILY",str(TODAY),str(TODAY),{},0,str(ACTOR))
    assert e.value.status_code==409 and len(calls)==1


def test_input_contract_rejects_extra_fields_floats_and_invalid_block():
    with pytest.raises(ValidationError): DailyReport(**report(TODAY,1.5))
    with pytest.raises(ValidationError): DailyReport(**report(TODAY),athlete_alias="someone-else")
    with pytest.raises(ValidationError): ResponseBlock(start=TODAY,load_end=TODAY,recovery_end=TODAY,phase="BUILD")
    with pytest.raises(ValidationError): OptionalTest(day=TODAY,protocol="CMJ",protocol_version="1",value=float("nan"),unit="cm",direction="HIGHER",conditions="same")


def test_learning_block_fits_two_load_windows_and_allows_two_recovery_observations():
    end = TODAY + timedelta(days=37)
    accepted = ResponseBlock(start=TODAY, load_end=end-timedelta(days=2), recovery_end=end, phase="BUILD")
    first_required = accepted.start-timedelta(days=38)
    latest_outcome = accepted.recovery_end+timedelta(days=14)
    assert (latest_outcome-first_required).days == 89
    with pytest.raises(ValidationError, match="38 days"):
        ResponseBlock(start=TODAY, load_end=end-timedelta(days=2), recovery_end=end+timedelta(days=1), phase="BUILD")
    with pytest.raises(ValidationError, match="two days"):
        ResponseBlock(start=TODAY, load_end=end-timedelta(days=1), recovery_end=end, phase="BUILD")


def test_api_requires_service_alias_actor_and_validates_request(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN","service-secret")
    monkeypatch.setattr(dependencies,"repository",lambda:Repository())
    with TestClient(main.app) as c:
        path = "/api/v2/athlete/response"
        assert c.get(path).status_code==401
        assert c.get(path,headers={"Authorization":"Bearer service-secret"}).status_code==401
        assert c.get(path,headers=HEADERS).status_code==200
        assert c.get(path+"?period_start=2026-01-01&period_end=2026-09-01",headers=HEADERS).status_code==422
        assert c.put(path+"/daily",json=report(TODAY),headers={k:v for k,v in HEADERS.items() if k!="X-OnFlows-Actor-Id"}).status_code==401
        assert c.put(path+"/daily",json={**report(TODAY),"fatigue":9},headers=HEADERS).status_code==422
