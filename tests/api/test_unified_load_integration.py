"""API integration checks for the common profile and shared load ledger."""

from copy import deepcopy

import pytest

from biathlon import speed_equivalence, unified_load, zone_profile
from tests.test_unified_zone_load import BOUNDS, profile, streams


def test_synthetic_pulse_and_speed_are_not_admitted_to_ti():
    from apps.api.trainability import compute_trainability
    hr = [{"elapsed_s": t, "hr_raw_bpm": 135.} for t in range(650)]
    speed = [{"elapsed_s": t, "dt_s": 1., "vflat_b65_kmh": 12., "grade_raw_pct": 0.} for t in range(1, 601)]
    kwargs = dict(zone_bounds_bpm=BOUNDS, hrmax_bpm=200, activity_duration_s=650., comparison_key="test", source_versions={})
    measured = compute_trainability(hr, speed, **kwargs)
    assert measured["zones"][0]["valid"]
    fake_hr = compute_trainability([{**r, "is_estimated": True} for r in hr], speed, **kwargs)
    fake_speed = compute_trainability(hr, [{**r, "is_estimated": True} for r in speed], **kwargs)
    assert not any(r["valid"] for r in fake_hr["zones"])
    assert not any(r["valid"] for r in fake_speed["zones"])



@pytest.mark.parametrize("sport", ["Run", "NordicSki", "Ride"])
@pytest.mark.parametrize("indicator", ["HEART_RATE", "SPEED"])
def test_plan_and_recording_use_identical_common_q(sport, indicator):
    from types import SimpleNamespace
    from datetime import date
    from apps.api.training_plan_engine import _canonical_load
    p = profile(sport)
    parameters = speed_equivalence.prepare(p, sport=sport)
    offset = 7. if sport == "Ride" else 0.
    velocity = (p["bounds_kmh"][2]+p["bounds_kmh"][3])/2
    pulse = (BOUNDS[2]+BOUNDS[3])/2-offset
    measured = unified_load.integrate(streams(pulse, velocity), bounds=BOUNDS, sport=sport,
        parameters=parameters, leading_indicator=indicator)
    settings = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=200,
        leading_indicator=indicator, load_contexts_by_sport={sport:{"comparison_parameters":parameters}})
    blocks = [{"zone":"Z3", "duration_min":1., "target_hr_bpm":pulse,
               "target_speed_kmh":velocity, "sport_hr_offset_bpm":offset, "load_sport":sport}]
    planned, _, _ = _canonical_load(blocks, settings, [], date(2026,10,24))
    assert planned["Z3"] == pytest.approx(measured["zones"][2]["equivalent_time_min"])
    assert sum(planned.values()) == pytest.approx(planned["Z3"])
    if indicator == "HEART_RATE":
        # Short speed prescriptions have no target HR. Their known speed is
        # the fallback; HRmax must not be substituted for an absent pulse.
        blocks[0]["target_hr_bpm"]=None
        fallback,_,_=_canonical_load(blocks,settings,[],date(2026,10,24))
        assert fallback["Z3"] == pytest.approx(planned["Z3"])



def test_history_reconstruction_uses_prior_date_and_keeps_unknown_separate(monkeypatch):
    from types import SimpleNamespace
    from datetime import date
    from apps.api import actual_load_projection, model_service
    settings = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=200, leading_indicator="SPEED")
    recorded = streams(None, 16., seconds=30)
    rows = {"pinned-key": {"activity_ref":"one", "shadow_payload":recorded}}
    repo = SimpleNamespace(athlete_settings=lambda _:settings, activity_speed_history_samples=lambda a,k:rows)
    inputs = {"alias":"test", "calendar":{"activities":[{"activity_ref":"one", "sport":"Run",
              "local_date":"2026-10-23", "latest_shadow_run_key":"pinned-key"}]}, "entries":[]}
    days = []
    def view(repository, alias, sport, *, _inputs, _capacity_only):
        days.append(_inputs["today"])
        return {"sport":sport, "speed_zones":profile(sport), "status":"UNAVAILABLE"}
    monkeypatch.setattr(model_service,"speed_view",view)
    source = {"period_start":"2026-10-23", "period_end":"2026-10-23", "activities":[
        {"activity_ref":"one", "sport":"Run", "date":"2026-10-23", "duration_min":1.,
         "quality_status":"limited", "zones":[{"zone":z,"effective_load":0.} for z in speed_equivalence.ZONES]}]}
    saved = deepcopy(source)
    result = actual_load_projection.reconstruct_history(repo,"test",source,speed_inputs=inputs)
    assert days == [date(2026,10,22)]
    assert result["activities"][0]["load_source"]["source_seconds"]["SPEED"] == 30.
    assert result["load_selection"]["unknown_seconds"] == 30.
    assert result["activities"][0]["quality_status"] == "limited"
    assert source == saved



def test_provider_replay_downloads_each_stream_only_once():
    from types import SimpleNamespace
    from apps.api.provider_replay import ProviderReplay
    calls=[]
    def stream(key):
        calls.append(key)
        return SimpleNamespace(payload=[{"hr":180.}])
    replay=ProviderReplay(SimpleNamespace(get_streams_result=stream))
    try:
        assert replay.get_streams_result("one").payload == replay.get_streams_result("one").payload
        assert calls == ["one"]
    finally:
        replay.close()



def test_accounting_q_is_not_reused_as_curve_calibration():
    from datetime import date
    from apps.api.model_service import _speed_volume_evidence
    recorded={"activity_ref":"one","date":"2026-10-23","sport":"Run","duration_min":60.,
        "load_source":{"calibration_eligible":False},
        "zones":[{"zone":z,"equivalent_time_min":9999.} for z in speed_equivalence.ZONES]}
    result=_speed_volume_evidence({"period_start":"2026-10-01","period_end":"2026-10-23","activities":[recorded]},[],date(2026,10,24))
    assert result["basis"] == "TOTAL_DURATION"
    assert all(v is None for v in result["zone_weekly_q"].values())
    assert result["total_weekly_minutes"] == pytest.approx(60*7/23)



def test_refresh_raises_maximum_from_raw_peak_and_replays_without_second_download(monkeypatch):
    from datetime import date
    from apps.api import component_load_context
    from apps.api.cloud import AthleteModelSettings, InMemorySnapshotRepository
    from apps.api.real_service import refresh
    from intervals_inspector.intervals_client import IntervalsResponse
    from tests.api.test_real_cloud_pipeline import Client, ENV
    class PeakClient(Client):
        def __init__(self):
            super().__init__(); self.stream_reads=0; self.detail_reads=0
        def get_activity_result(self,key,*,include_intervals=False):
            self.detail_reads+=1
            return super().get_activity_result(key,include_intervals=include_intervals)
        def get_streams_result(self,key):
            self.stream_reads+=1
            return IntervalsResponse(200,[{"type":"time","data":list(range(61))},
                                         {"type":"heartrate","data":[200.]*61}])
    repo=InMemorySnapshotRepository()
    current=[AthleteModelSettings((86,137,150,169,179,190),"UTC",190,"AUTOMATIC_HRMAX",zone_profile.HR_PERCENTAGES)]
    repo.athlete_settings=lambda alias:current[0]
    observations=[]
    def increase(alias,observation,*,expected_hrmax):
        assert expected_hrmax == 190
        observations.append(observation)
        current[0]=AthleteModelSettings(BOUNDS,"UTC",200,"AUTOMATIC_HRMAX",zone_profile.HR_PERCENTAGES,
            hrmax_source="OBSERVED",hrmax_observation=observation)
        return True
    repo.increase_athlete_hrmax=increase
    monkeypatch.setattr(component_load_context,"read_contexts",lambda repository,alias,sports:{
        sport:component_load_context.context_from_speed_view({"sport":sport}) for sport in sports})
    client=PeakClient()
    refresh(repo,environ=ENV,client=client,period_end=date(2026,8,15),athlete_settings=current[0])
    assert len(observations) == 1
    assert client.stream_reads == client.detail_reads == 1
    assert repo.latest("pilot")["load_history"]["hrmax_bpm"] == 200
    assert repo.latest("pilot")["load_history"]["zone_bounds_bpm"] == list(BOUNDS)
    assert repo.latest("pilot")["load_history"]["activities"][0]["load_source"]["recorded_seconds"] == 60.



def test_real_refresh_without_hr_produces_q_740_and_recovery_from_speed(monkeypatch):
    from datetime import date
    from apps.api import component_load_context
    from apps.api.cloud import AthleteModelSettings, InMemorySnapshotRepository
    from apps.api.real_service import refresh
    from intervals_inspector.intervals_client import IntervalsResponse
    from tests.api.test_real_cloud_pipeline import Client, ENV
    class NoPulseClient(Client):
        def get_streams_result(self,key):
            return IntervalsResponse(200,[{"type":"time","data":list(range(61))},
                {"type":"velocity_smooth","data":[5.]*61},
                {"type":"gradient","data":[0.]*61}])
    settings=AthleteModelSettings(BOUNDS,"UTC",200,"AUTOMATIC_HRMAX",zone_profile.HR_PERCENTAGES)
    repo=InMemorySnapshotRepository()
    context=component_load_context.context_from_speed_view({"sport":"Run","status":"UNAVAILABLE","speed_zones":profile()})
    monkeypatch.setattr(component_load_context,"read_contexts",lambda repository,alias,sports:{s:context for s in sports})
    repo.athlete_settings=lambda alias:settings
    refresh(repo,environ=ENV,client=NoPulseClient(),period_end=date(2026,8,15),athlete_settings=settings)
    result=repo.latest("pilot")
    activity=result["load_history"]["activities"][0]
    assert activity["hr_coverage_percent"] == 0.
    assert activity["load_source"]["source_seconds"]["SPEED"] == 60.
    assert activity["load_source"]["fallback_seconds"] == 60.
    assert any(z["equivalent_time_min"]>0 for z in activity["zones"])
    assert any(z["status_7_40"]>1 for z in result["load_history"]["zones"])
    assert any(z["recovery_readiness_percent"]<100 for z in result["training_status"]["zones"])

