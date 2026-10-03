from copy import deepcopy
from datetime import date

import pytest

from biathlon import speed_duration, speed_zones, race_specific, training_guidance
from apps.api.training_observation_schemas import LactateProfile

TODAY = date(2026, 9, 30)


def real_test(seconds, speed, **kwargs):
    return dict(duration_s=seconds, speed_kmh=speed, maximal=True, comparable=True,
                enabled=True, use_for_cs=True, test_mode="STRICT", day=TODAY.isoformat(), **kwargs)


def test_standardized_cs_keeps_original_anchors_and_labels_derived_coordinates():
    tests = [real_test(165, 1000/165*3.6), real_test(1020, 5000/1020*3.6)]
    original = deepcopy(tests)
    result = speed_duration.standardized_critical_speed(tests)
    assert result["speed_kmh"] == pytest.approx(17.2416287166)
    assert result["direct_test_speed_kmh"] == pytest.approx(16.8421052632)
    assert [p["evidence"] for p in result["points"]] == ["INTERPOLATED"]*2
    assert result["measured"] is False and result["uncertainty"] == "NOT_QUANTIFIED"
    curve = speed_duration.calibrated(tests)
    for t in tests:
        assert curve.speed(t["duration_s"])*3.6 == pytest.approx(t["speed_kmh"])
    assert tests == original


def test_standardization_uses_all_maximal_anchors_and_flags_continuation():
    two = [real_test(300, 20), real_test(600, 18.8)]
    assert speed_duration.standardized_critical_speed(two)["uses_extrapolation"]
    tests = [real_test(180, 22), real_test(480, 20), real_test(720, 18.46)]
    curve = speed_duration.calibrated(tests)
    result = speed_duration.standardized_critical_speed(tests, curve)
    assert result["count"] == 3
    assert [p["speed_kmh"] for p in result["points"]] == pytest.approx([22,18.46])
    assert curve.speed(480)*3.6 == pytest.approx(20)


@pytest.mark.parametrize("change", [{"maximal":False}, {"generated":True}, {"is_estimated":True},
                                      {"source":"MODEL"}, {"enabled":False}, {"use_for_cs":False}])
def test_generated_or_ordinary_efforts_cannot_create_short_long_support(change):
    tests = [real_test(180,22), {**real_test(720,18.46), **change}]
    assert speed_duration.standardized_critical_speed(tests)["status"] == "INSUFFICIENT_SHORT_LONG_TESTS"


def test_personal_speed_lactate_is_protocol_interpolation_and_never_extrapolated():
    personal = LactateProfile(sport="Run",source="TEST",assessed_on=TODAY,protocol="3 min stages",
        stages=[{"speed_kmh":v,"lactate_mmol":l,"duration_min":3} for v,l in [(14,1),(16,2),(18,4)]]).model_dump(mode="json")
    p = {"lactate_profiles":[personal]}
    value = training_guidance.lactate_at_speed(p,"Run",17,TODAY)
    assert value["estimated_mmol"] == 3 and value["measured"] is False
    assert value["protocol"] == "3 min stages" and value["stage_duration_min"] == [3,3]
    assert value["control_role"] == "TEST_PROTOCOL_REFERENCE"
    assert training_guidance.lactate_at_speed(p,"Run",19,TODAY) is None
    assert training_guidance.lactate_at_speed(p,"NordicSki",17,TODAY) is None
    assert training_guidance.lactate_at_speed(p,"Run",17,date(2026,9,29)) is None
    assert training_guidance.lactate_at_speed({},"Run",17,TODAY) is None
    assert training_guidance._interpolate(personal["stages"],160) is None


def test_race_reference_uses_exact_inverse_without_turning_maximum_into_dose():
    from biathlon.training_methods import resolved_methods
    from tests.api.test_load_progression import configured
    tests = [real_test(165,1000/165*3.6),real_test(1020,5000/1020*3.6)]
    profile = configured(discipline="4000 m",age_years=30,training_experience_years=5)
    zones = {"status":"AVAILABLE","zones":[{"zone":f"Z{i+1}","high_kmh":v} for i,v in enumerate([11,13,15,17,None])]}
    view = {"sport":"Run","tests":[{"entry_key":str(i),"payload":t} for i,t in enumerate(tests)],
            "active_test_keys":["0","1"],"speed_zones":zones}
    ref = race_specific.reference(profile,view,{"source":"SPEED_DURATION","distance_m":4000},TODAY)
    assert ref["duration_s"]*ref["speed_kmh"]/3.6 == pytest.approx(4000)
    speeds = [b["speed_kmh"] for b in ref["bands"]]
    assert speeds[0] < speeds[1] < speeds[2]
    assert all(b["lactate_reference"] is None for b in ref["bands"])
    methods = race_specific.methods(profile,ref,resolved_methods(profile))
    assert len(methods) == 3
    assert all(m["interval_template"]["work_seconds"] < m["race_specific"]["maximum_duration_s"] for m in methods)
    assert not ref["dose_from_maximum_time"]
    profile["planning_controls"]["automatic_intervals"] = False
    assert race_specific.methods(profile,ref,resolved_methods(profile)) == []


def test_speed_zones_classify_independently_of_hr_and_keep_boundary_provenance():
    from biathlon.hr_speed import Predictor
    curve = speed_duration.calibrated([real_test(180,22),real_test(720,18.46)])
    predictor = Predictor(curve,(100,120,140,160,180,200),200,{})
    profile = speed_zones.build(curve,predictor,[])
    assert profile["status"] == "AVAILABLE" and len(profile["zones"]) == 5
    assert profile["is_measured_hr"] is False
    assert all(z["source"] == "CURVE_EXPERT_TIME" for z in profile["zones"])
    for row in profile["zones"]:
        middle = (row["low_kmh"]+(row["high_kmh"] or row["low_kmh"]+2))/2
        assert speed_zones.classify(profile,middle) == row["zone"]
