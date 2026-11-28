"""Initial raw-speed estimates prescribe only under the model-prior policy."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from apps.api import method_capacity, plan_speed_comparison, training_plan_engine as engine
from biathlon import execution_ranges, hr_speed, initial_speed_profile, initial_threshold
from biathlon import method_anchors, method_library, race_course, race_specific, zone_profile
from biathlon.sport_heart_rate import policy
from biathlon.training_methods import METHODS
from tests.api.test_sourced_method_library import recipe, athlete


TODAY = date(2026, 10, 29)
HRMAX = 190.
BOUNDS = tuple(HRMAX*p/100 for p in zone_profile.HR_PERCENTAGES)
SETTINGS = SimpleNamespace(hrmax_bpm=HRMAX, zone_bounds_bpm=BOUNDS)


def view():
    estimate = initial_threshold.estimate(8, 12, 4)
    built = initial_speed_profile.build(estimate, BOUNDS, HRMAX, sport="Run")
    predictor = hr_speed.ZonalPredictor(built["curve"], BOUNDS, HRMAX, {}, profile=built["speed_zones"])
    return {"status": "INITIAL_HEURISTIC", "sport": "Run", "speed_role": "RAW_MOVING_SPEED",
            "model_version": initial_threshold.VERSION,
            "initial_threshold": {**estimate, "used_for_zones": True, "use_status": "ZONES"},
            "initial_speed_profile": initial_speed_profile.summary(built),
            "speed_zones": built["speed_zones"], "hr_model": predictor.summary(),
            "sport_hr_policy": policy("Run", BOUNDS, HRMAX),
            "tests": [], "active_test_keys": [], "active_test_count": 0,
            "index_summary": {}, "index_window": {"last_activity_date": None}}


def continuous_method():
    return {**deepcopy(METHODS[-1]), "structure": "CONTINUOUS", "zone": "Z3", "actual_sport": "Run"}


def test_basic_capacity_uses_exact_raw_curve_without_recent_index_or_fake_tests():
    speed = view()
    before = deepcopy(speed)
    context = engine._capacity_context(speed, SETTINGS)
    assert context[0] is not None and context[1] == []
    result = engine.capacity_for(continuous_method(), SETTINGS, speed, context, TODAY, use_model_prior=True)
    assert result is not None
    assert result["capacity_source"] == "INITIAL_HEURISTIC"
    assert result["capacity_confidence"] == "INITIAL_HEURISTIC_UNVALIDATED"
    assert result["calibration_eligible"] is False and result["initial_heuristic"] is True
    assert result["speed_role"] == result["effort_anchor"]["speed_role"] == "RAW_MOVING_SPEED"
    expected = context[0].duration(result["target_hr_bpm"])/60
    assert result["capacity_minutes"] == pytest.approx(expected, abs=.001)
    assert result["target_speed_kmh"] == pytest.approx(context[0].speed_for_hr(result["target_hr_bpm"]), abs=.001)
    assert "STALE_HR_SPEED_INDEX" not in result["fallback_reasons"]
    assert speed == before
    assert engine.capacity_for(continuous_method(), SETTINGS, speed, context, TODAY,
                               use_model_prior=False) is None


@pytest.mark.parametrize("zone,position", [("Z3", .5), ("Z1", 0.)])
def test_raw_block_frame_survives_course_and_execution_distance_annotations(zone, position):
    speed = view()
    context = engine._capacity_context(speed, SETTINGS)
    method = {**continuous_method(), "zone": zone, "position": position}
    result = engine.capacity_for(method, SETTINGS, speed, context, TODAY, use_model_prior=True)
    if zone == "Z1":
        assert result["capacity_source"] == "EXPERT_CONTINUOUS_TREF"
    blocks = engine._blocks(method, 12, result, SETTINGS)
    session = {"sport": "Run", "purpose": "BUILDING", "zone": "Z3", "method_id": method["id"],
               "blocks": blocks, "dose_evidence": result}
    days = [{"date": TODAY.isoformat(), "period": "SPECIAL_PREPARATION", "sessions": [session]}]
    course = {"distance_m": 5000, "surface": "TRAIL", "profile": "ROLLING", "specific_sessions_percent": 100}
    event = {"event_id": "race", "event_type": "MAIN_RACE", "name": "Race", "start_date": "2026-11-10",
             "end_date": "2026-11-10", "race_course": course}
    race_course.annotate_days(days, [event])
    execution_ranges.annotate(session)
    for block in blocks:
        if block.get("target_speed_kmh") is not None:
            assert block["speed_basis"] == block["speed_role"] == "RAW_MOVING_SPEED"
            assert block["distance_basis"] == "RAW_MOVING_SPEED"
            assert block["initial_heuristic"] is True


def test_expert_duration_fallback_keeps_independent_initial_raw_speed_origin():
    speed = view()
    context = engine._capacity_context(speed, SETTINGS)
    method = {**continuous_method(), "zone": "Z1", "position": 0.}
    result = engine.capacity_for(method, SETTINGS, speed, context, TODAY, use_model_prior=True)
    assert result["capacity_source"] == "EXPERT_CONTINUOUS_TREF"
    assert result["capacity_reference"] == "EXPERT_CONTINUOUS_TMAX"
    assert result["capacity_confidence"] == "EXPERT_REFERENCE"
    assert result["model_version"] == "expert-continuous-capacity-v1"
    assert "BOUNDARY_OUTSIDE_CURVE_DOMAIN" in result["fallback_reasons"]
    assert result["target_speed_kmh"] == pytest.approx(context[0].speed_for_hr(result["target_hr_bpm"]), abs=.001)
    assert result["target_speed_source"] == "INITIAL_HEURISTIC"
    assert result["target_speed_model_version"] == initial_threshold.VERSION
    assert result["speed_role"] == result["effort_anchor"]["speed_role"] == "RAW_MOVING_SPEED"
    assert result["initial_heuristic"] is True and result["calibration_eligible"] is False


def test_source_recipe_reuses_published_zone_profile_and_provenance(monkeypatch):
    speed = view()
    prepared, predictor = method_library._prepare(speed)
    assert isinstance(predictor, hr_speed.ZonalPredictor)
    assert predictor.profile == speed["speed_zones"]
    r = recipe()
    resolved = method_library.resolve(r, athlete(), speed, phase="SPECIAL_PREPARATION")
    assert resolved["status"] == "READY"
    assert resolved["curve_source"] == "INITIAL_HEURISTIC"
    assert resolved["active_test_count"] == 0
    assert resolved["speed_role"] == resolved["anchor"]["speed_role"] == "RAW_MOVING_SPEED"
    assert all(s["speed_role"] == "RAW_MOVING_SPEED" for s in resolved["segments"])
    monkeypatch.setattr(method_library, "entries", lambda: [r])
    method = method_library.executable_methods(athlete(), speed, phase="SPECIAL_PREPARATION")[0]
    method["actual_sport"] = "Run"
    context = engine._capacity_context(speed, SETTINGS)
    capacity = engine.capacity_for(method, SETTINGS, speed, context, TODAY, use_model_prior=True)
    assert capacity["capacity_source"] == "INITIAL_HEURISTIC"
    blocks = engine._blocks(method, resolved["standard_work_seconds"]/60, capacity, SETTINGS)
    assert blocks and all(b["speed_basis"] == "RAW_MOVING_SPEED" for b in blocks if b.get("target_speed_kmh"))


def test_measured_only_and_explicit_short_test_requirements_still_reject_estimate():
    speed = view()
    strict = athlete(planning_controls={"capacity_policy": "OBSERVED_ONLY"})
    assert method_library.resolve(recipe(), strict, speed, phase="SPECIAL_PREPARATION")["status"] == "UNAVAILABLE"
    constrained = recipe(requirements={"short_test_max_seconds": 180})
    assert method_library.resolve(constrained, athlete(), speed, phase="SPECIAL_PREPARATION")["reason"] == "SHORT_SPEED_TEST_REQUIRED"


def test_initial_model_does_not_reinterpret_manual_flat_targets():
    speed = view()
    method = {**continuous_method(), "structure": "METABOLIC_INTERVALS",
              "interval_profile": {"target_speed_kmh": 15., "speed_basis": "FLAT_EQUIVALENT"}}
    assert engine.capacity_for(method, SETTINGS, speed, engine._capacity_context(speed, SETTINGS),
                               TODAY, use_model_prior=True) is None
    profile = athlete(ant_references=[{"sport": "Run", "source": "MANUAL", "speed_kmh": 15.}])
    with pytest.raises(ValueError, match="THRESHOLD_SPEED_FRAME_REQUIRED"):
        method_anchors.resolve_speed({"type": "ANT"}, profile, speed,
                                     initial_speed_profile.curve_from_view(speed), [])


def test_threshold_recipe_uses_approved_threshold_before_cs_fallback():
    speed = view()
    result = method_anchors.resolve_speed({"type": "ANT"}, athlete(), speed,
                                         initial_speed_profile.curve_from_view(speed), [])
    assert result["speed_kmh"] == pytest.approx(speed["initial_threshold"]["v_threshold_estimated_kmh"])
    assert result["source"] == "INITIAL_HEURISTIC_THRESHOLD"
    assert result["is_estimate"] is True and result["calibration_eligible"] is False


def test_generic_z5_model_capacity_retains_zero_measured_anchors():
    speed = view()
    method = {**continuous_method(), "structure": "MODEL_INTERVALS", "zone": "Z5",
              "interval_template": {"work_seconds": 15}}
    context = engine._capacity_context(speed, SETTINGS)
    result = method_capacity.model_interval_curve_capacity(method, SETTINGS, speed, context, use_model_prior=True)
    assert result and result["capacity_source"] == "INITIAL_HEURISTIC"
    assert result["speed_role"] == "RAW_MOVING_SPEED"
    assert "test_anchor" not in result
    assert result["supported_test_duration_s"] is None
    assert method_capacity.model_interval_curve_capacity(method, SETTINGS, speed, context, use_model_prior=False) is None
    reference = race_specific.reference(athlete(), speed, {"source": "COURSE_EXPECTATION"}, TODAY)
    assert reference["reason"] == "REAL_TESTS_REQUIRED_FOR_RACE_REFERENCE"
    assert race_specific.methods(athlete(), reference, METHODS) == []


def test_plan_comparison_does_not_reclassify_flat_targets_on_raw_profile():
    speed = view()
    context = engine._capacity_context(speed, SETTINGS)[0].component_load_context
    parameters = context["comparison_parameters"]
    target = sum(speed["speed_zones"]["bounds_kmh"][2:4])/2
    raw = {"kind": "WORK", "zone": "Z3", "duration_s": 300., "target_speed_kmh": target,
           "speed_basis": "RAW_MOVING_SPEED"}
    assert plan_speed_comparison._block_comparison(raw, parameters)["coverage_percent"] == 100
    flat = {**raw, "speed_basis": "FLAT_EQUIVALENT"}
    result = plan_speed_comparison._block_comparison(flat, parameters)
    assert result["coverage_percent"] == 0
    assert result["range_reason"] == "SPEED_FRAME_MISMATCH"
    assert "PRESCRIBED_SPEED_FRAME_DIFFERS_FROM_PROFILE" in result["warnings"]


def test_real_test_curve_initial_mapping_also_works_without_recent_hr_index():
    from tests.api.test_sourced_method_library import view as measured_view
    from tests.api.test_training_plan_engine import BOUNDS as measured_bounds
    speed = measured_view()
    for row in speed["tests"]:
        row["payload"]["comparable"] = True
    settings = SimpleNamespace(hrmax_bpm=190., zone_bounds_bpm=measured_bounds)
    context = engine._capacity_context(speed, settings)
    result = engine.capacity_for(continuous_method(), settings, speed, context, TODAY, use_model_prior=True)
    assert result is not None
    assert result["capacity_source"] == "TEST_CURVE_INITIAL_HR_ESTIMATE"
    assert result["capacity_confidence"] == "TEST_CURVE_WITH_ESTIMATED_HR_MAPPING"
    assert result["speed_role"] == "FLAT_EQUIVALENT_REFERENCE_NOT_TERRAIN_PACE"
    assert "STALE_HR_SPEED_INDEX" not in result["fallback_reasons"]
    assert engine.capacity_for(continuous_method(), settings, speed, context, TODAY, use_model_prior=False) is None


@pytest.mark.parametrize("change", [{"comparable": False}, {"source": "MODEL"}, {"is_estimated": True}])
def test_unaccepted_tests_cannot_bypass_recent_index_gate(change):
    from tests.api.test_sourced_method_library import view as measured_view
    from tests.api.test_training_plan_engine import BOUNDS as measured_bounds
    speed = measured_view()
    for row in speed["tests"]:
        row["payload"].update({"comparable": True, **change})
    settings = SimpleNamespace(hrmax_bpm=190., zone_bounds_bpm=measured_bounds)
    context = engine._capacity_context(speed, settings)
    assert engine.capacity_for(continuous_method(), settings, speed, context, TODAY, use_model_prior=True) is None
