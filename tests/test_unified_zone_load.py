from copy import deepcopy

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


def test_final_timestamp_does_not_add_an_extra_recorded_second():
    shadow=streams(160.,16.,seconds=61)
    result=unified_load.integrate(shadow,bounds=BOUNDS,sport="Run",duration_s=60.,
                                 parameters=speed_equivalence.prepare(profile()))
    assert result["recorded_seconds"] == 60.
    assert result["active_seconds"] == pytest.approx(60.)


@pytest.mark.parametrize("step", [2., 2.5, 5., 8., 10.])
@pytest.mark.parametrize("annotated", [False, True])
def test_smart_recording_preserves_valid_duration_without_speed_fallback(step, annotated):
    offsets = [i*step for i in range(int(60/step)+1)]
    if offsets[-1] < 60.:
        offsets.append(60.)
    rows = [{"elapsed_s": t, "hr_raw_bpm": 160., "speed_raw_kmh": 18.} for t in offsets]
    if annotated:
        for row, following in zip(rows, rows[1:]):
            row.update(hr_interval_classification="smart_recording", hr_interval_end_s=following["elapsed_s"])
    parameters = speed_equivalence.prepare(profile())
    result = unified_load.integrate({"timeseries": rows, "speed_test_series": []},
        bounds=BOUNDS, sport="Run", parameters=parameters, duration_s=60.)
    regular = unified_load.integrate(streams(160., None), bounds=BOUNDS, sport="Run",
        parameters=parameters, duration_s=60.)
    for actual, expected in zip(result["zones"], regular["zones"]):
        assert actual["zone"] == expected["zone"]
        assert actual["raw_time_min"] == pytest.approx(expected["raw_time_min"], abs=1e-12)
        assert actual["equivalent_time_min"] == pytest.approx(expected["equivalent_time_min"], abs=1e-12)
    assert result["source_seconds"] == {"HEART_RATE": 60., "SPEED": 0.}
    assert result["unknown_seconds"] == 0.


def test_smart_recording_splits_crossed_hr_zones_and_keeps_q_exact():
    parameters = speed_equivalence.prepare(profile())
    shadow = {"timeseries": [
        {"elapsed_s": 0., "hr_raw_bpm": 140., "hr_interval_classification": "smart_recording", "hr_interval_end_s": 5.},
        {"elapsed_s": 5., "hr_raw_bpm": 190.}], "speed_test_series": []}
    result = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=parameters, duration_s=5.)
    durations = [.4, 1.4, 2., 1., .2]
    assert [z["raw_time_min"]*60 for z in result["zones"]] == pytest.approx(durations)
    for z, seconds, pulse in zip(result["zones"], durations, [142., 151., 168., 183., 189.]):
        coefficient = speed_equivalence.evaluate_hr(parameters, pulse, bounds=BOUNDS)["coefficient"]
        assert z["equivalent_time_min"] == pytest.approx(seconds/60*coefficient)


def test_smart_recording_uses_actual_hr_position_for_partial_speed_fallback():
    parameters = speed_equivalence.prepare(profile())
    shadow = {"timeseries": [
        {"elapsed_s": 0., "hr_raw_bpm": 160., "hr_interval_classification": "smart_recording", "hr_interval_end_s": 5.},
        {"elapsed_s": 5., "hr_raw_bpm": 170.}],
        "speed_test_series": streams(None, 16., seconds=2)["speed_test_series"]}
    result = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", parameters=parameters,
        duration_s=5., leading_indicator="SPEED")
    assert result["source_seconds"] == {"HEART_RATE": 3., "SPEED": 2.}
    assert result["fallback_seconds"] == 3.
    expected = (2*speed_equivalence.evaluate(parameters, 16.)["coefficient"]
                +3*speed_equivalence.evaluate_hr(parameters, 167., bounds=BOUNDS)["coefficient"])/60
    assert sum(z["equivalent_time_min"] for z in result["zones"]) == pytest.approx(expected)


def test_recording_stops_are_not_filled_as_smart_hr_intervals():
    rows = [{"elapsed_s": t, "hr_raw_bpm": 160.} for t in [0., 5., 40., 45.]]
    for row, following, kind in zip(rows, rows[1:], ["smart_recording", "recording_stop", "smart_recording"]):
        row.update(hr_interval_classification=kind, hr_interval_end_s=following["elapsed_s"])
    result = unified_load.integrate({"timeseries": rows, "speed_test_series": []},
        bounds=BOUNDS, sport="Run", parameters=speed_equivalence.prepare(profile()), duration_s=45.)
    assert result["active_seconds"] == 10.
    assert result["unknown_seconds"] == 35.


@pytest.mark.parametrize("rejected_vflat", [0., 16., 30.])
@pytest.mark.parametrize("has_hr", [False, True])
def test_explicitly_unavailable_speed_uses_hr_or_remains_unknown(rejected_vflat, has_hr):
    shadow = streams(160. if has_hr else None, 16., seconds=10)
    for row in shadow["speed_test_series"][4:7]:
        row.update(load_status="UNAVAILABLE", load_speed_kmh=None,
                   load_reason="NEGATIVE_DESCENT_RESIDUAL", vflat_b65_kmh=rejected_vflat)
    result = unified_load.integrate(shadow, bounds=BOUNDS, sport="NordicSki",
        parameters=speed_equivalence.prepare(profile("NordicSki")),
        leading_indicator="SPEED", duration_s=10.)
    assert result["source_seconds"] == {"HEART_RATE": 3. if has_hr else 0., "SPEED": 7.}
    assert result["fallback_seconds"] == (3. if has_hr else 0.)
    assert result["unknown_seconds"] == (0. if has_hr else 3.)
    assert result["active_seconds"] == pytest.approx(10. if has_hr else 7.)
    assert result["rest_seconds"] == 0.
