from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
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


def test_custom_report_dates_include_both_boundaries_and_prior_calibration():
    repo=Repository()
    start=TODAY-timedelta(days=65); end=start+timedelta(days=2)
    repo.add("prior","Run",start-timedelta(days=1),150,15)
    repo.add("first","Run",start,150,15)
    repo.add("last","Run",end,150,15)
    repo.add("after","Run",end+timedelta(days=1),150,15)
    result=model.history_view(repo,"authorized-athlete",today=TODAY,period_start=start,period_end=end)
    assert result["start_date"]==start.isoformat() and result["end_date"]==end.isoformat()
    assert [a["activity_ref"] for a in result["activities"]]==["first","last"]
    assert result["classified_minutes"]==pytest.approx(20)
    assert result["zones"][2]["equivalent_minutes"]==pytest.approx(14)
    assert len(result["daily"])==15
    assert sum(d["effective_load"] for d in result["daily"] if d["zone"]=="Z3")==pytest.approx(result["zones"][2]["effective_load"])
    one=model.history_view(repo,"authorized-athlete",today=TODAY,period_start=end,period_end=end)
    assert one["classified_minutes"]==pytest.approx(10) and len(one["daily"])==5


@pytest.mark.parametrize("start,end", [(TODAY,None),(None,TODAY),(TODAY,TODAY-timedelta(days=1)),(TODAY,TODAY+timedelta(days=1)),(TODAY-timedelta(days=366),TODAY)])
def test_custom_report_rejects_incomplete_reversed_future_or_excessive_periods(start,end):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        model.history_view(Repository(),"authorized-athlete",today=TODAY,period_start=start,period_end=end)
    assert error.value.status_code==422


@pytest.mark.parametrize("offsets", [(3, 1), (65, 3, 1)])
def test_analysis_date_preserves_default_report_exactly_for_short_and_long_history(offsets):
    repo=Repository()
    for offset in offsets:
        repo.add(f"run-{offset}","Run",TODAY-timedelta(days=offset),150,15)
    original=model.history_view(repo,"authorized-athlete",today=TODAY)
    anchored=model.history_view(repo,"authorized-athlete",today=TODAY,as_of=TODAY)
    assert anchored==original
    # An explicit analysis date retains the observed-history denominator; it
    # must not turn a short history into a padded custom 40-day calendar.
    assert len(anchored["daily"])==len(original["daily"])


def test_analysis_date_matches_prior_day_default_and_excludes_later_activities():
    repo=Repository()
    end=TODAY-timedelta(days=1)
    repo.add("prior","Run",end-timedelta(days=3),150,15)
    repo.add("boundary","Run",end,150,15)
    repo.add("after","Run",TODAY,150,15)
    original=model.history_view(repo,"authorized-athlete",today=end)
    anchored=model.history_view(repo,"authorized-athlete",today=TODAY,as_of=end)
    assert anchored==original
    assert anchored["start_date"]==(end-timedelta(days=39)).isoformat()
    assert anchored["end_date"]==end.isoformat()
    assert [a["activity_ref"] for a in anchored["activities"]]==["prior","boundary"]
    assert anchored["classified_minutes"]==pytest.approx(10)
    assert anchored["zones"][2]["equivalent_minutes"]==pytest.approx(7)


@pytest.mark.parametrize("timezone_name,instant,local_day", [
    ("Europe/Sofia", "2026-10-06T22:30:00+00:00", date(2026,10,7)),
    ("America/Los_Angeles", "2026-10-07T00:30:00+00:00", date(2026,10,6)),
])
def test_analysis_date_validation_uses_athlete_local_day_at_utc_boundary(monkeypatch,timezone_name,instant,local_day):
    from fastapi import HTTPException
    fixed=datetime.fromisoformat(instant)
    class FixedClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return fixed.astimezone(tz or timezone.utc)
    monkeypatch.setattr(model,"datetime",FixedClock)
    settings=deepcopy(SETTINGS);settings.timezone=timezone_name
    repo=Repository()
    monkeypatch.setattr(repo,"athlete_settings",lambda _:settings)
    original=model.history_view(repo,"authorized-athlete")
    assert original["end_date"]==local_day.isoformat()
    assert model.history_view(repo,"authorized-athlete",as_of=local_day)==original
    with pytest.raises(HTTPException) as error:
        model.history_view(repo,"authorized-athlete",as_of=local_day+timedelta(days=1))
    assert error.value.status_code==422


@pytest.mark.parametrize("as_of,start,end", [
    (date.min,None,None),
    (TODAY+timedelta(days=1),None,None),
    (TODAY,TODAY-timedelta(days=1),TODAY),
    (TODAY,TODAY,None),
    (TODAY,None,TODAY),
])
def test_analysis_date_rejects_future_or_mixed_custom_period(as_of,start,end):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        model.history_view(Repository(),"authorized-athlete",today=TODAY,
            as_of=as_of,period_start=start,period_end=end)
    assert error.value.status_code==422


def test_route_passes_parsed_analysis_date_after_authorizing_selected_athlete(monkeypatch):
    from fastapi.testclient import TestClient
    from apps.api import main, dependencies
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN","test-service-secret")
    calls=[]
    monkeypatch.setattr(dependencies,"repository",lambda: "repository")
    monkeypatch.setattr(model,"history_view",lambda repo,alias,sport,**dates:
        calls.append((repo,alias,sport,dates)) or {"status":"UNAVAILABLE"})
    with TestClient(main.app) as client:
        url=f"/api/v2/athlete/models/speed-load?as_of={TODAY.isoformat()}"
        assert client.get(url).status_code==401
        assert client.get(url,headers={"Authorization":"Bearer wrong-token",
            "X-OnFlows-Athlete-Alias":"authorized-athlete"}).status_code==401
        assert client.get(url,headers={"Authorization":"Bearer test-service-secret"}).status_code==401
        assert client.get(url,headers={"Authorization":"Bearer test-service-secret",
            "X-OnFlows-Athlete-Alias":"invalid/alias"}).status_code==400
        assert client.get("/api/v2/athlete/models/speed-load?as_of=not-a-date",
            headers={"Authorization":"Bearer test-service-secret",
                "X-OnFlows-Athlete-Alias":"authorized-athlete"}).status_code==422
        assert not calls
        response=client.get(url,headers={"Authorization":"Bearer test-service-secret",
            "X-OnFlows-Athlete-Alias":"authorized-athlete"})
        assert response.status_code==200
        assert calls==[("repository","authorized-athlete",None,{"as_of":TODAY})]


def test_rolling_chart_ratios_match_original_daily_statistics_for_short_and_full_history():
    import numpy as np
    import pandas as pd
    parameters = model.fresh_parameters()
    rng = np.random.default_rng(121)
    dates = pd.date_range(TODAY-timedelta(days=89), TODAY)
    summaries = pd.DataFrame({"date": dates,
        **{f"q_{z}": rng.uniform(0, 800, len(dates)) for z in model.COMPONENTS}})
    # Include rest days and high loads to exercise both base-load regimes and
    # causal spill, plus windows with fewer than 7/40/50 observed dates.
    summaries.loc[::3, [f"q_{z}" for z in model.COMPONENTS]] = 0.
    loads = model.compute_daily_load_history(summaries, parameters, TODAY)
    rolling = model.rolling_load_statistics(loads, parameters).pivot(
        index="date", columns="component", values="index_7_40")
    for day in dates:
        previous = model.compute_load_statistics(loads.loc[:day], parameters, day)
        for zone in model.ZONES:
            assert rolling.loc[day, zone] == pytest.approx(
                previous.loc[zone, "index_7_40"], rel=1e-12, abs=1e-12)


@pytest.mark.parametrize("sport,index", [("Run", 5.), ("Ride", 2.5), ("VirtualRun", 5.37)])
def test_prepared_integration_converter_matches_reference_exactly_at_zone_edges_and_finite_speeds(sport, index):
    import math
    import numpy as np
    mapping, _ = model.zone_mapping({"GENERAL": {"index": index, "count": 1, "seconds": 600}}, BOUNDS, 200, sport)
    convert = model._integration_converter(mapping)
    edges = mapping["bounds_kmh"]
    speeds = [None, False, -1., 0., float("nan"), float("inf"), -float("inf")]
    speeds += [candidate for edge in edges for candidate in
               (math.nextafter(edge, -math.inf), edge, math.nextafter(edge, math.inf))]
    speeds += list(np.random.default_rng(121).uniform(edges[0]*.9, edges[-1]*1.1, 1000))
    for speed in speeds:
        reference = model.convert_speed(mapping, speed)
        actual = convert(speed)
        assert actual == ((reference["zone"], reference["coefficient"]) if reference else None)
