from copy import deepcopy
import math

import pytest

from biathlon import hrmax, speed_equivalence, unified_load, zone_profile
from biathlon.hr_speed import ZonalPredictor


class TestCurve:
    __test__ = False
    times = (10., 43200.)
    model_version = "test-continuous-curve"
    def __init__(self, v6=20.):
        self.v6 = v6
    def speed(self, t):
        return self.v6/3.6*(t/360.)**-.1
    def inverse(self, speed):
        return 360.*(speed/(self.v6/3.6))**-10


BOUNDS = (90, 144, 158, 178, 188, 200)


def profile(sport="Run", v6=20.):
    return zone_profile.build(TestCurve(v6), BOUNDS, 200, {}, sport=sport)


def streams(hr=160., speed=16., seconds=60):
    return {"timeseries": [{"elapsed_s": t, "hr_raw_bpm": hr} for t in range(seconds)],
            "speed_test_series": [{"elapsed_s": t+1, "dt_s": 1., "vflat_b65_kmh": speed,
                                   "grade_smoothed_pct": 0., "exclusion_reason": None}
                                  for t in range(seconds)]}


@pytest.mark.parametrize("sport", ["Run", "NordicSki", "RollerSki", "Ride"])
def test_initial_profiles_have_relative_floor_and_ordered_shared_edges(sport):
    p = profile(sport)
    assert p["status"] == "AVAILABLE"
    assert p["bounds_kmh"][0] == 4.
    assert p["bounds_kmh"][-1] == pytest.approx(TestCurve().speed(10)*3.6)
    assert all(a < b for a,b in zip(p["bounds_kmh"], p["bounds_kmh"][1:]))
    assert p["calibration_eligible"] is False
    parameters = speed_equivalence.prepare(p, sport=sport)
    assert all(r["status"] == "AVAILABLE" for r in parameters["zones"])


def test_measured_ti_edges_survive_unusual_continuous_capacity():
    speeds = [8., 10., 12., 14.]
    indices = {z: {"index": 100*BOUNDS[i+1]/200/v, "count": 2, "seconds": 900.}
               for i,(z,v) in enumerate(zip(zone_profile.ZONES, speeds))}
    predictor = ZonalPredictor(TestCurve(), BOUNDS, 200, indices)
    assert [a["speed_kmh"] for a in predictor.anchors] == pytest.approx(speeds)
    assert predictor.anchors[0]["duration_s"] > 18000
    assert all(a["source"] == "INDEX" for a in predictor.anchors)


def test_cycling_reference_offset_is_applied_exactly_once():
    indices = {"GENERAL": {"index": 5., "count": 3, "seconds": 1200.}}
    running = ZonalPredictor(TestCurve(), BOUNDS, 200, indices, sport="Run")
    cycling = ZonalPredictor(TestCurve(), [v-7 for v in BOUNDS], 193, indices,
                             sport="Ride", normalization_offset_bpm=7.)
    for v in [10., 15., 18.]:
        assert running.hr_for_speed(v)-cycling.hr_for_speed(v) == pytest.approx(7.)


def test_missing_or_generated_index_cannot_replace_the_initial_profile():
    for row in ({"index":5.,"count":None,"seconds":None},
                {"index":5.,"count":3,"seconds":900.,"generated":True}):
        result=zone_profile.build(TestCurve(),BOUNDS,200,{"GENERAL":row})
        assert result["bounds_kmh"] == profile()["bounds_kmh"]
        assert result["zones"][0]["source"] == "INITIAL_V50_V6_ESTIMATE"


def test_hr_coordinate_respects_the_explicit_control_coefficients_once():
    p=profile()
    parameters=speed_equivalence.prepare(p,settings={"version":"c1-control","origin":"EXPERT_DEFINED",
        "coefficients":dict.fromkeys(speed_equivalence.ZONES,1.)})
    for i in (3,4):
        v=sum(p["bounds_kmh"][i:i+2])/2
        h=sum(BOUNDS[i:i+2])/2
        assert speed_equivalence.evaluate_hr(parameters,h,bounds=BOUNDS)["coefficient"] == pytest.approx(
            speed_equivalence.evaluate(parameters,v)["coefficient"])


@pytest.mark.parametrize("sport", ["Run", "NordicSki", "Ride"])
def test_hr_and_speed_use_the_same_q_for_corresponding_positions(sport):
    p = profile(sport)
    parameters = speed_equivalence.prepare(p, sport=sport)
    offset = 7 if sport == "Ride" else 0
    for i in range(5):
        for u in [.001, .25, .5, .999]:
            v = p["bounds_kmh"][i]+u*(p["bounds_kmh"][i+1]-p["bounds_kmh"][i])
            h = BOUNDS[i]+u*(BOUNDS[i+1]-BOUNDS[i])-offset
            by_speed = speed_equivalence.evaluate(parameters, v)
            by_hr = speed_equivalence.evaluate_hr(parameters, h, bounds=BOUNDS, offset_bpm=offset)
            assert by_speed["zone"] == by_hr["zone"]
            assert by_speed["coefficient"] == pytest.approx(by_hr["coefficient"], abs=1e-12)


def test_primary_choice_changes_dose_without_double_counting_and_is_reversible():
    parameters = speed_equivalence.prepare(profile())
    shadow = streams(160., 19.)
    saved = deepcopy(shadow)
    hr = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=parameters, leading_indicator="HEART_RATE")
    speed = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=parameters, leading_indicator="SPEED")
    assert hr["active_seconds"] == pytest.approx(60.)
    assert speed["active_seconds"] == pytest.approx(60.)
    assert hr["source_seconds"] == {"HEART_RATE": 60., "SPEED": 0.}
    assert speed["source_seconds"] == {"HEART_RATE": 0., "SPEED": 60.}
    assert hr["zones"] != speed["zones"]
    assert shadow == saved
    assert hr == unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=parameters)


def test_missing_raw_hr_does_not_become_measured_from_hrmod():
    parameters = speed_equivalence.prepare(profile())
    shadow = streams()
    for row in shadow["timeseries"][20:30]:
        row.update(hr_raw_bpm=None, hrmod_final_bpm=190.)
    result = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=parameters)
    assert result["source_seconds"] == {"HEART_RATE": 50., "SPEED": 10.}
    assert result["fallback_seconds"] == 10.
    assert result["active_seconds"] == pytest.approx(60.)


def test_relative_rest_differs_between_athletes_and_is_not_missing_data():
    shadow = streams(None, 4.5)
    slower = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run",
        parameters=speed_equivalence.prepare(profile(v6=20)), leading_indicator="SPEED")
    faster = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run",
        parameters=speed_equivalence.prepare(profile(v6=40)), leading_indicator="SPEED")
    assert slower["active_seconds"] == pytest.approx(60.) and slower["rest_seconds"] == 0
    assert faster["rest_seconds"] == 60 and faster["unknown_seconds"] == 0
    missing = unified_load.integrate(streams(None, None), bounds=BOUNDS, sport="Run",
        parameters=speed_equivalence.prepare(profile()), duration_s=60.)
    assert missing["unknown_seconds"] == 60 and missing["rest_seconds"] == 0


def test_hr_below_45_percent_is_known_low_load_even_when_speed_exists():
    result = unified_load.integrate(streams(89., 15.), bounds=BOUNDS, sport="Run",
        parameters=speed_equivalence.prepare(profile()))
    assert result["rest_seconds"] == 60.
    assert result["fallback_seconds"] == 0.


def test_speed_above_estimated_v10_saturates_without_losing_time():
    result = unified_load.integrate(streams(None, 40.), bounds=BOUNDS, sport="Run",
        parameters=speed_equivalence.prepare(profile()), leading_indicator="SPEED")
    assert result["active_seconds"] == pytest.approx(60.)
    assert result["saturated_above_v10_seconds"] == 60.
    assert result["unknown_seconds"] == 0


def test_hrmax_requires_a_complete_stable_raw_window():
    assert hrmax.tanaka(40) == 180
    rows = [{"elapsed_s": t, "hr_raw_bpm": 195.+(t % 2)} for t in range(15)]
    assert hrmax.observed_peak(rows)["reference_hrmax_bpm"] == 195
    assert hrmax.observed_peak(rows[:14]) is None
    assert hrmax.observed_peak([{**r, "is_estimated": True} for r in rows]) is None
    assert hrmax.observed_peak([{**r, "hr_source": "MODEL"} for r in rows]) is None
    rows[7]["hr_raw_bpm"] = 230.
    assert hrmax.observed_peak(rows) is None
    rows[7]["hr_raw_bpm"] = None
    assert hrmax.observed_peak(rows) is None


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


def test_ten_second_extension_preserves_the_original_curve_and_join_slope():
    from biathlon import speed_duration
    original = speed_duration.calibrated([{"duration_s": 360., "speed_kmh": 20.}])
    extended = speed_duration.with_ten_second_tail(original)
    assert extended.times[0] == 10.
    for t in original.times:
        assert extended.speed(t) == original.speed(t)
    assert extended.log_slope(10.8) == original.log_slope(10.8)
    for t in (10., 10.4, 10.8):
        assert extended.inverse(extended.speed(t)) == pytest.approx(t)
    assert extended.point_metadata(10.)["extrapolated"]


def test_settings_change_does_not_reuse_hrmod_clipped_to_an_old_maximum():
    shadow = streams(190., None)
    shadow["trainability_index"] = {"zone_bounds_bpm": [80, 130, 150, 170, 180, 185]}
    for row in shadow["timeseries"]:
        row["hrmod_final_bpm"] = 180.
    result = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run",
        parameters=speed_equivalence.prepare(profile()))
    assert result["zones"][-1]["raw_time_min"] == pytest.approx(1.)
    assert result["hr_coordinate"] == "RAW_HR_SETTINGS_CHANGED"


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
    from apps.api.component_load_context import context_from_speed_view
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


def test_final_timestamp_does_not_add_an_extra_recorded_second():
    shadow=streams(160.,16.,seconds=61)
    result=unified_load.integrate(shadow,bounds=BOUNDS,sport="Run",duration_s=60.,
                                 parameters=speed_equivalence.prepare(profile()))
    assert result["recorded_seconds"] == 60.
    assert result["active_seconds"] == pytest.approx(60.)


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
