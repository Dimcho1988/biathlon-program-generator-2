"""All admission scenarios share one continuous prescription/capacity curve."""
from copy import deepcopy
from datetime import datetime

import pytest

from apps.api import component_load_context, model_service, training_plan_engine as engine
from biathlon import dosing_curve, method_anchors, method_library, method_registry
from biathlon import preliminary_capacity, speed_duration, speed_zones, training_methods
from tests.api.test_speed_history_window import Repository, NOW
from tests.api.test_initial_threshold_integration import Repository as InitialRepository, NOW as INITIAL_NOW
from tests.api.test_training_plan_engine import profile


def freeze(monkeypatch, now):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)
    monkeypatch.setattr(model_service, "datetime", Fixed)


@pytest.mark.parametrize("sport", ["Run", "NordicSki", "RollerSki", "Ride"])
@pytest.mark.parametrize("count", [0, 1, 2, 3])
@pytest.mark.parametrize("index_age", [None, 1, 28])
def test_admission_scenarios_agree_across_display_recipes_planning_and_capacity(monkeypatch, sport, count, index_age):
    freeze(monkeypatch, NOW)
    repo = Repository()
    for seconds, speed in [(165., 21.8), (1020., 17.65), (600., 19.)][:count]:
        repo.test(source="MANUAL", sport=sport, duration_s=seconds, speed_kmh=speed)
    if index_age is not None:
        repo.add("paired", -index_age, sport=sport)
        from biathlon.sport_heart_rate import reference_offset
        repo.summaries["paired"]["trainability_index"]["hr_reference_offset_bpm"] = reference_offset(sport)
    original = deepcopy(repo.rows)
    view = model_service.speed_view(repo, "ath-test", sport)
    fast = model_service.speed_view(repo, "ath-test", sport, _capacity_only=True)
    curve, tests, source = dosing_curve.selected(view)
    if curve is None:
        assert count == 0 and index_age != 1
        assert view["dosing_model"]["status"] == "UNAVAILABLE"
        assert engine._capacity_context(view, repo.athlete_settings("ath-test"))[0] is None
        with pytest.raises(ValueError, match="NO_INDIVIDUAL_SPEED_CURVE"):
            method_library.selected_curve(view)
        return
    assert source == ("BLENDED_DOSING_CURVE" if count and index_age == 1 else
                      "INDIVIDUAL_TEST_CURVE" if count else "INDIVIDUAL_PRIOR_CURVE")
    recipe_curve, recipe_tests, recipe_source = method_library.selected_curve(view)
    load_curve, _, _ = component_load_context._selected_curve(view)
    predictor, planned_tests, _ = engine._capacity_context(view, repo.athlete_settings("ath-test"))
    assert predictor is not None
    assert recipe_tests == planned_tests == tests
    assert recipe_source == source
    assert component_load_context.context_from_speed_view(view) == component_load_context.context_from_speed_view(fast)
    for seconds in [10., 30., 180., 300., 360., 720., 3000.]:
        expected = curve.speed(seconds)
        for selected in [recipe_curve, load_curve, predictor.curve]:
            assert selected.speed(seconds) == pytest.approx(expected, rel=1e-12)
            assert selected.inverse(expected) == pytest.approx(seconds, rel=1e-8)
        if source == "BLENDED_DOSING_CURVE":
            assert expected == pytest.approx(.7*curve.test_curve.speed(seconds)+.3*curve.index_curve.speed(seconds))
    assert repo.rows == original
    assert repo.saved == []


def test_initial_raw_speed_has_the_same_curve_without_invented_blend(monkeypatch):
    freeze(monkeypatch, INITIAL_NOW)
    repo = InitialRepository()
    view = model_service.speed_view(repo, "ath-test", "Run")
    curve, tests, source = dosing_curve.selected(view)
    predictor, _, _ = engine._capacity_context(view, repo.athlete_settings("ath-test"))
    assert source == "INITIAL_HEURISTIC" and tests == []
    assert view["dosing_model"]["status"] == "UNAVAILABLE"
    assert view["speed_role"] == "RAW_MOVING_SPEED"
    for seconds in (30., 300., 360., 3000.):
        assert predictor.curve.speed(seconds) == pytest.approx(curve.speed(seconds))
        assert method_library.selected_curve(view)[0].speed(seconds) == pytest.approx(curve.speed(seconds))
    methods = method_registry.executable_methods(profile(sport="Run", actual_sport="Run",
        planning_controls={"automatic_intervals": True}), {"Run": view})
    native = [m for m in methods if (m.get("interval_template") or {}).get("anchor") and not m.get("race_specific")]
    assert native
    for method in native:
        cap = engine.capacity_for(method, repo.athlete_settings("ath-test"), view,
            (predictor, [], ["INSUFFICIENT_INDEPENDENT_TEST_DURATIONS"]), INITIAL_NOW.date(), use_model_prior=True)
        assert cap and cap["speed_role"] == "RAW_MOVING_SPEED"
        assert cap["capacity_source"] == "INITIAL_HEURISTIC"


def test_conflicting_tests_can_use_independent_index_without_mixing_rejected_tests(monkeypatch):
    freeze(monkeypatch, NOW)
    repo = Repository()
    repo.add("paired", -1)
    repo.test(source="MANUAL", duration_s=180., speed_kmh=18.)
    repo.test(source="MANUAL", duration_s=720., speed_kmh=23.)
    view = model_service.speed_view(repo, "ath-test", "Run")
    curve, tests, source = dosing_curve.selected(view)
    assert curve is not None and tests == [] and source == "INDIVIDUAL_PRIOR_CURVE"
    assert view["active_test_keys"] == []
    assert view["dosing_model"]["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("change", [{"maximal": False}, {"test_mode": "EXPLORATORY"},
    {"sport": "NordicSki"}, {"generated": True}, {"source": "MODEL"}, {"enabled": False}])
def test_consumers_reject_invalid_active_provenance_instead_of_falling_back(change):
    from tests.test_dosing_curve import example
    view = example()
    view["tests"][0]["payload"].update(change)
    for select in [dosing_curve.selected, method_library.selected_curve, component_load_context._selected_curve]:
        with pytest.raises(ValueError):
            select(view)


def running_view():
    bounds = (92, 144, 164, 182, 190, 205)
    indices = {"Z1": {"index": 6.997, "count": 9, "seconds": 7236},
               "Z2": {"index": 6.724, "count": 14, "seconds": 12709},
               "Z3": {"index": 6.061, "count": 8, "seconds": 5449},
               "GENERAL": {"index": 6.185, "count": 13, "seconds": 14721}}
    tests = [{"duration_s": t, "speed_kmh": v, "maximal": True, "comparable": True,
              "sport": "Run", "source": "MANUAL", "enabled": True, "use_for_cs": True}
             for t, v in [(165., 1000/165*3.6), (1020., 5000/1020*3.6)]]
    prior = preliminary_capacity.build(bounds, 205, indices, total_weekly_minutes=300.)
    dose = dosing_curve.describe(prior["curve"], speed_duration.calibrated(tests), tests,
        bounds, 205, indices, prior["anchors"])
    return {"sport": "Run", "status": "CALIBRATED", "model_version": speed_duration.VERSION,
            "tests": [{"entry_key": str(i), "payload": t} for i, t in enumerate(tests)],
            "active_test_keys": ["0", "1"], "preliminary_capacity": preliminary_capacity.summary(prior),
            "index_summary": indices, "dosing_model": dose}, bounds


def test_short_vo2_recipe_uses_v5_and_charges_its_actual_zone_without_clipping():
    from types import SimpleNamespace
    view, bounds = running_view()
    settings = SimpleNamespace(zone_bounds_bpm=bounds, hrmax_bpm=205)
    body = profile(age_years=30, training_experience_years=5,
        planning_controls={"automatic_intervals": True, "mixed_sessions_enabled": True})
    methods = method_registry.executable_methods(body, {"Run": view})
    method = next(m for m in methods if m["id"] == "ONFLOWS-CONTROLLED-Z4-V2-MIX-REPETITIONS-SHORT")
    assert method["protocol_zone"] == "Z4" and method["zone"] == "Z5"
    assert "Z4" not in method["title"]
    assert method["interval_template"]["work_seconds"] == 30.
    context = engine._capacity_context(view, settings)
    cap = engine.capacity_for(method, settings, view, context, NOW.date(), use_model_prior=True)
    assert cap["capacity_minutes"] == pytest.approx(5.)
    assert cap["target_speed_kmh"] == pytest.approx(context[0].curve.speed(300.)*3.6)
    assert cap["target_speed_kmh"] > 18.
    # The scheduler supplies the independent easy-component budget before
    # serializing a mixed session; it must not change the primary effort.
    cap["secondary_capacity"] = {"target_hr_bpm": 131., "target_speed_kmh": None}
    cap["easy_work_cap_minutes"] = 0.
    cap["easy_to_primary_ratio"] = 0.
    blocks = engine._blocks(method, 3., cap, settings)
    work = [b for b in blocks if b["kind"] == "WORK" and b["zone"] != "Z1"]
    assert work and len(work) == 6
    for block in work:
        assert block["zone"] == "Z5"
        assert block["target_speed_range_kmh"][0] <= block["target_speed_kmh"] <= block["target_speed_range_kmh"][1]
        assert block["target_speed_kmh"] == pytest.approx(cap["target_speed_kmh"], abs=.0005)
    raw = next(m for m in training_methods.resolved_methods(body) if m["id"] == method["id"])
    assert engine.capacity_for(raw, settings, view, context, NOW.date(), use_model_prior=True) is None


def test_cv_prescription_uses_the_selected_working_curve_without_changing_test_cs():
    view, _ = running_view()
    curve, tests, _ = dosing_curve.selected(view)
    original = deepcopy(tests)
    direct = speed_duration.critical_speed(tests)
    anchor = method_anchors.resolve_speed({"type": "CV"}, profile(), view, curve, tests)
    expected = (curve.distance(720.)-curve.distance(180.))/540.*3.6
    assert anchor["speed_kmh"] == pytest.approx(expected)
    assert anchor["evidence"]["source"] == "CURRENT_CURVE_V3_V12"
    assert anchor["speed_kmh"] != pytest.approx(direct["speed_kmh"])
    assert tests == original and speed_duration.critical_speed(tests) == direct


def test_explicit_speed_outside_a_nominal_zone_never_produces_an_inverted_execution_range():
    from biathlon.method_dosing import target_ranges
    result = target_ranges(20., None, "Z4", speed_zones={"zones": [
        {"zone": "Z4", "low_kmh": 14., "high_kmh": 15.}]})
    assert result["target_speed_range_kmh"] == pytest.approx([19.6, 20.4])


def test_v5_parent_subdivision_and_short_variants_share_effort_and_preserve_minimum_work():
    from types import SimpleNamespace
    view, bounds = running_view()
    settings = SimpleNamespace(zone_bounds_bpm=bounds, hrmax_bpm=205)
    body = profile(age_years=30, training_experience_years=5,
        planning_controls={"automatic_intervals": True})
    methods = method_registry.executable_methods(body, {"Run": view})
    context = engine._capacity_context(view, settings)
    parent = next(m for m in methods if m["id"] == "ONFLOWS-CONTROLLED-Z4-V2")
    child = next(m for m in methods if m["id"] == parent["id"]+"-SPLIT-V1")
    for method, reps, seconds in [(parent, 2, 180.), (child, 4, 90.)]:
        cap = engine.capacity_for(method, settings, view, context, NOW.date(), use_model_prior=True)
        assert cap and cap["capacity_minutes"] == pytest.approx(5.)
        assert cap["target_speed_kmh"] == pytest.approx(context[0].curve.speed(300.)*3.6)
        engine._nominal_fraction(method, "BUILDING", body, "GENERAL_PREPARATION", cap, {})
        assert method["interval_template"]["min_repetitions"] == reps
        assert method["interval_template"]["work_seconds"] == seconds
        assert engine._minimum_work(method, cap, settings) == 6.
