"""Available curves dose high-zone work without a second short-test gate."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from apps.api import training_plan_engine as engine
from apps.api.method_capacity import model_interval_curve_capacity
from biathlon import dosing_curve, hr_speed, speed_duration


SETTINGS = SimpleNamespace(hrmax_bpm=180, zone_bounds_bpm=(80, 137, 148, 160, 170, 180))


def method(zone="Z5", **changes):
    return {"zone": zone, "structure": "MODEL_INTERVALS", "actual_sport": "NordicSki",
            "interval_template": {"work_seconds": 30}, **changes}


def single_anchor_context(*, blended=False):
    test = {"duration_s": 1050., "speed_kmh": 20.571428571428573, "maximal": True,
            "test_mode": "STRICT", "day": "2026-09-19"}
    calibrated = speed_duration.calibrated([test])
    curve = dosing_curve.Curve(calibrated, calibrated) if blended else calibrated
    predictor_cls = dosing_curve.Predictor if blended else hr_speed.Predictor
    predictor = predictor_cls(curve, SETTINGS.zone_bounds_bpm, SETTINGS.hrmax_bpm, {})
    speed = {"status": "CALIBRATED", "model_version": speed_duration.VERSION}
    return speed, (predictor, [test], ["INSUFFICIENT_INDEPENDENT_TEST_DURATIONS"])


@pytest.mark.parametrize("blended", [False, True])
def test_single_long_anchor_supplies_z5_curve_effort_without_fake_short_test(blended):
    speed, context = single_anchor_context(blended=blended)
    original = deepcopy(context[1])
    evidence = model_interval_curve_capacity(method(), SETTINGS, speed, context, use_model_prior=True)
    reference = min(600., context[0].duration(SETTINGS.zone_bounds_bpm[4])*.5)
    assert evidence["capacity_minutes"] == reference/60
    assert evidence["target_speed_kmh"] == pytest.approx(context[0].curve.speed(reference)*3.6)
    assert evidence["target_speed_kmh"] > evidence["boundary_speed_kmh"]
    assert evidence["capacity_reference"] == "BOUNDARY_HALF_TMAX_MAX600"
    assert evidence["capacity_is_estimate"] and not evidence["within_observed_test_window"]
    assert evidence["supported_test_duration_s"] == [1050., 1050.]
    assert "test_anchor" not in evidence
    assert context[1] == original
    assert evidence["capacity_source"] == ("BLENDED_DOSING_CURVE" if blended else "SPEED_DURATION_MODEL_CURVE")


def test_active_short_anchor_is_preserved_without_recency_cutoff():
    speed, context = single_anchor_context()
    tests = [{"duration_s": 165., "speed_kmh": 25., "maximal": True,
              "test_mode": "STRICT", "day": "2025-01-01"}]
    curve = speed_duration.calibrated(tests)
    predictor = hr_speed.Predictor(curve, SETTINGS.zone_bounds_bpm, SETTINGS.hrmax_bpm, {})
    evidence = model_interval_curve_capacity(method(), SETTINGS, speed, (predictor, tests, []), use_model_prior=True)
    assert evidence["capacity_source"] == "SPEED_DURATION_TEST_ANCHOR"
    assert evidence["capacity_minutes"] == 165/60
    assert evidence["test_anchor"] == tests[0]
    assert evidence["within_observed_test_window"]


def test_z5_reference_scales_with_actual_boundary_before_ten_minute_cap():
    speed, (predictor, tests, _) = single_anchor_context()
    predictor.duration = lambda _: 800.
    evidence = model_interval_curve_capacity(method(), SETTINGS, speed, (predictor, tests, []), use_model_prior=True)
    assert evidence["boundary_capacity_seconds"] == 800.
    assert evidence["capacity_minutes"] == pytest.approx(400/60)
    assert evidence["target_speed_kmh"] == pytest.approx(predictor.curve.speed(400)*3.6)
    assert evidence["target_speed_kmh"] > evidence["boundary_speed_kmh"]


def test_available_preliminary_curve_is_visible_estimate_without_measured_anchors():
    speed, (predictor, _, _) = single_anchor_context()
    speed["status"] = "PRELIMINARY"
    evidence = model_interval_curve_capacity(method(), SETTINGS, speed,
        (predictor, [], ["INSUFFICIENT_INDEPENDENT_TEST_DURATIONS"]), use_model_prior=True)
    assert evidence["capacity_is_estimate"]
    assert evidence["supported_test_duration_s"] is None
    assert "test_anchor" not in evidence


@pytest.mark.parametrize("blended", [False, True])
def test_race_specific_single_anchor_uses_actual_curve_inverse_at_prescribed_speed(blended):
    speed, context = single_anchor_context(blended=blended)
    target = context[0].curve.speed(900)*3.6
    band = {"speed_kmh": target, "maximum_duration_s": 1200., "zone": "Z5"}
    evidence = model_interval_curve_capacity(method(race_specific=band), SETTINGS, speed, context, use_model_prior=True)
    assert evidence["capacity_minutes"] == pytest.approx(15.)
    assert evidence["target_speed_kmh"] == target
    assert evidence["race_specific"] == band
    assert evidence["capacity_reference"] == "CURVE_INVERSE_AT_PRESCRIBED_RACE_SPEED"


@pytest.mark.parametrize("speed,context", [
    (None, (None, [], ["NO_INDIVIDUAL_SPEED_CURVE"])),
    ({"status": "REFERENCE_ONLY"}, (None, [], ["NO_INDIVIDUAL_SPEED_CURVE"])),
    ({"status": "CONFLICTING_TESTS"}, (None, [], ["NO_INDIVIDUAL_SPEED_CURVE"])),
])
def test_no_available_individual_curve_cannot_invent_z5_capacity(speed, context):
    assert model_interval_curve_capacity(method(), SETTINGS, speed, context, use_model_prior=True) is None


def test_invalid_context_and_interval_longer_than_capacity_still_fail():
    speed, (predictor, tests, _) = single_anchor_context()
    assert model_interval_curve_capacity(method(), SETTINGS, speed,
        (predictor, tests, ["EXPLORATORY_OR_NONMAXIMAL_TESTS"]), use_model_prior=True) is None
    reference = min(600., predictor.duration(SETTINGS.zone_bounds_bpm[4])*.5)
    assert model_interval_curve_capacity(method(interval_template={"work_seconds": reference}),
        SETTINGS, speed, (predictor, tests, []), use_model_prior=True) is None


def test_z5_model_reference_must_remain_above_actual_z4_boundary():
    speed, context = single_anchor_context()
    # The declared Z4 boundary cannot silently become generic Z5 effort.
    reference = min(600., context[0].duration(SETTINGS.zone_bounds_bpm[4])*.5)
    context[0].speed_for_hr = lambda _: context[0].curve.speed(reference)*3.6 + .01
    assert model_interval_curve_capacity(method(), SETTINGS, speed, context, use_model_prior=True) is None


def metabolic_method(target, *, basis="FLAT_EQUIVALENT", assessed_on="2026-01-01"):
    return {"structure": "METABOLIC_INTERVALS", "zone": "Z5", "actual_sport": "NordicSki",
            "warmup_min": 15., "cooldown_min": 10., "instructions": "Controlled repeatable effort",
            "interval_profile": {"zone": "Z5", "continuous_capacity_min": 20., "target_speed_kmh": target,
                "speed_basis": basis, "work_seconds": 30., "assessed_on": assessed_on,
                "min_repetitions": 4, "max_repetitions": 20, "recovery_seconds": 30., "reserve_repetitions": 2}}


def single_anchor_view():
    speed, context = single_anchor_context()
    test = context[1][0]
    speed.update(sport="NordicSki", active_test_keys=["race"],
                 tests=[{"entry_key": "race", "payload": test}])
    return speed, context


def test_individual_flat_speed_profile_uses_single_anchor_curve_inverse_outside_measured_window():
    speed, context = single_anchor_view()
    target = context[0].curve.speed(600)*3.6
    original = deepcopy(speed)
    profile = metabolic_method(target)
    evidence = engine.capacity_for(profile, SETTINGS, speed, context, date(2026, 10, 8), use_model_prior=True)
    assert evidence["capacity_source"] == "SPEED_DURATION"
    assert evidence["capacity_minutes"] == pytest.approx(10.)
    assert evidence["target_speed_kmh"] == target
    assert evidence["capacity_reference"] == "CURVE_INVERSE_AT_PRESCRIBED_SPEED"
    assert evidence["supported_test_duration_s"] == [1050., 1050.]
    assert not evidence["within_observed_test_window"]
    assert evidence["effort_profile"] == profile["interval_profile"]
    assert speed == original


def test_individual_flat_speed_profile_uses_actual_blended_curve_inverse(monkeypatch):
    speed, context = single_anchor_view()
    plain = context[0].curve
    # Deliberately different index/test scales ensure using the test curve
    # alone cannot accidentally pass the requested inverse check.
    index = speed_duration.calibrated([{**context[1][0], "speed_kmh": 22.}])
    blend = dosing_curve.Curve(index, plain)
    monkeypatch.setattr(dosing_curve, "from_view", lambda _: blend)
    target = blend.speed(600)*3.6
    evidence = engine.capacity_for(metabolic_method(target), SETTINGS, speed, context, date(2026, 10, 8), use_model_prior=True)
    assert evidence["capacity_source"] == "BLENDED_DOSING_CURVE"
    assert evidence["capacity_minutes"] == pytest.approx(10.)
    assert evidence["model_version"] == dosing_curve.VERSION
    assert evidence["target_speed_kmh"] == target


def test_curve_capacity_changes_denominator_without_creating_canonical_zone_work():
    speed, context = single_anchor_view()
    target = context[0].curve.speed(600)*3.6
    method = metabolic_method(target)
    today = date(2026, 10, 8)
    evidence = engine.capacity_for(method, SETTINGS, speed, context, today, use_model_prior=True)
    blocks = engine._blocks(method, 3., evidence, SETTINGS)
    direct, effective, _ = engine._canonical_load(blocks, SETTINGS, [], today)
    assert evidence["capacity_minutes"] == pytest.approx(10.)
    assert evidence["effort_profile"]["continuous_capacity_min"] == 20.
    assert sum(b["duration_min"] for b in blocks if b["kind"] == "WORK") == 3.
    # Existing Z5 upper-edge equivalence is 1.5; the new curve capacity must
    # not add another capacity or interval-budget multiplier to canonical Q.
    assert direct["Z5"] == pytest.approx(4.5)
    assert effective["Z5"] == pytest.approx(4.5)


def test_actual_terrain_speed_profile_preserves_coach_capacity_and_assessment_recency():
    speed, context = single_anchor_view()
    target = context[0].curve.speed(600)*3.6
    today = date(2026, 10, 8)
    current = metabolic_method(target, basis="ACTUAL", assessed_on=today.isoformat())
    evidence = engine.capacity_for(current, SETTINGS, speed, context, today)
    assert evidence["capacity_source"] == "COACH_EFFORT_CAPACITY"
    assert evidence["capacity_minutes"] == 20.
    assert evidence["speed_role"] == "ACTUAL"
    stale = metabolic_method(target, basis="ACTUAL")
    assert engine.capacity_for(stale, SETTINGS, speed, context, today) is None


def test_missing_curve_flat_profile_does_not_invent_normative_capacity():
    today = date(2026, 10, 8)
    speed = {"status": "PRELIMINARY", "model_version": speed_duration.VERSION,
             "active_test_keys": [], "tests": []}
    current = metabolic_method(22., assessed_on=today.isoformat())
    evidence = engine.capacity_for(current, SETTINGS, speed, (None, [], []), today)
    assert evidence["capacity_source"] == "COACH_EFFORT_CAPACITY"
    assert evidence["capacity_minutes"] == 20.
    assert engine.capacity_for(metabolic_method(22.), SETTINGS, speed, (None, [], []), today) is None


def test_single_anchor_race_methods_use_available_dosing_curve_and_actual_capacity():
    from biathlon import preliminary_capacity, race_specific
    from biathlon.training_methods import resolved_methods
    from tests.api.test_load_progression import configured
    from tests.test_dosing_curve import BOUNDS, INDICES, example

    view = example()
    test = {**view["tests"][0]["payload"], "duration_s": 1050., "speed_kmh": 20.571428571428573}
    view.update(tests=[{"entry_key": "race", "payload": test}], active_test_keys=["race"], active_test_count=1)
    prior = preliminary_capacity.curve_from_summary(view["preliminary_capacity"])
    view["dosing_model"] = dosing_curve.describe(prior, speed_duration.calibrated([test]), [test],
        BOUNDS, 205, INDICES, view["preliminary_capacity"]["anchors"])
    profile = configured(discipline="7500 m", age_years=30, training_experience_years=5)
    today = date(2026, 9, 30)
    reference = race_specific.reference(profile, view, {"source": "SPEED_DURATION", "distance_m": 7500}, today)
    assert reference["status"] == "AVAILABLE" and reference["accepted_test_count"] == 1
    methods = race_specific.methods(profile, reference, resolved_methods(profile))
    assert methods and all(m["race_specific"]["dosing_model_version"] == dosing_curve.VERSION for m in methods)
    settings = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=205)
    context = engine._capacity_context(view, settings)
    for m in methods:
        capacity = engine.capacity_for(m, settings, view, context, today, use_model_prior=True)
        assert capacity["capacity_source"] == "BLENDED_DOSING_CURVE"
        assert capacity["target_speed_kmh"] == m["race_specific"]["speed_kmh"]
        assert capacity["capacity_minutes"] == pytest.approx(m["race_specific"]["maximum_duration_s"]/60)


def test_unavailable_or_unanchored_race_reference_does_not_create_methods():
    from biathlon import race_specific
    from biathlon.training_methods import resolved_methods
    from tests.api.test_load_progression import configured
    profile = configured()
    methods = resolved_methods(profile)
    assert race_specific.methods(profile, {"status": "UNAVAILABLE"}, methods) == []
    assert race_specific.methods(profile, {"status": "AVAILABLE", "accepted_test_count": 0}, methods) == []


def test_full_planner_generates_whole_z5_dose_from_single_long_anchor_with_long_term_budget(monkeypatch):
    from datetime import timedelta
    from biathlon import preliminary_capacity
    from tests.api.test_load_progression import configured, observed
    from tests.api.test_readiness_adaptive_plan_v2 import (
        assert_readiness_dose, assert_rolling_budgets, fixed_readiness,
    )
    from tests.api.test_training_plan_engine import NOW, TODAY
    from tests.test_dosing_curve import BOUNDS, INDICES, example

    repo, _, _ = observed()
    repo.settings = SimpleNamespace(zone_bounds_bpm=BOUNDS, hrmax_bpm=205, timezone="Europe/Sofia")
    view = example()
    test = {**view["tests"][0]["payload"], "duration_s": 1050., "speed_kmh": 20.571428571428573,
            "day": (TODAY-timedelta(days=2)).isoformat()}
    view.update(tests=[{"entry_key": "long-race", "payload": test}], active_test_keys=["long-race"],
                active_test_count=1, source_generation_id="generation-one", source_revision=1,
                index_window={"last_activity_date": TODAY.isoformat()})
    prior = preliminary_capacity.curve_from_summary(view["preliminary_capacity"])
    view["dosing_model"] = dosing_curve.describe(prior, speed_duration.calibrated([test]), [test],
        BOUNDS, 205, INDICES, view["preliminary_capacity"]["anchors"])
    body = configured(age_years=25, training_experience_years=5, building_fraction=.65,
                      available_minutes=[180]*7, horizon_mode="MANUAL",
                      program_end=(TODAY+timedelta(days=6)).isoformat(),
                      # This fixture's 15-bpm-wide Z5 has 1.75 upper-edge
                      # equivalence: six 30-s reps require 5.25 canonical min.
                      component_targets_weekly={"Z1": 100., "Z5": 6.})
    body["planning_controls"].update(accents=["Z5"], sessions_per_week=1,
        sessions_by_day=[0, 1, 0, 0, 0, 0, 0], threshold_days=[1])
    fixed_readiness(monkeypatch, 90.)
    monkeypatch.setattr(engine.model_service, "speed_view", lambda *args, **kwargs: deepcopy(view))
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    sessions = [s for day in plan["days"] for s in day["sessions"]]
    z5_rejections = [r for day in plan["days"] for r in day.get("rejected_alternatives", [])
                     if any("Z5" in key for key in r.get("method_ids", [r.get("method_id", "")]))]
    assert len(sessions) == 1 and sessions[0]["zone"] == "Z5", z5_rejections
    session = sessions[0]
    evidence = session["dose_evidence"]
    assert session["purpose"] == "BUILDING"
    assert evidence["capacity_source"] == "BLENDED_DOSING_CURVE"
    assert evidence["capacity_reference"] == "BOUNDARY_HALF_TMAX_MAX600"
    assert evidence["supported_test_duration_s"] == [1050., 1050.]
    assert "test_anchor" not in evidence
    assert evidence["base_fraction"] == .65 and evidence["readiness_dose_factor"] == .9
    assert evidence["requested_primary_work_minutes"] / evidence["capacity_minutes"] == pytest.approx(.65*.9, abs=.0001)
    assert evidence["target_speed_kmh"] > evidence["boundary_speed_kmh"]
    work = [b for b in session["blocks"] if b["kind"] == "WORK"]
    template = evidence["effort_profile"]
    assert template["min_repetitions"] <= len(work) <= template["max_repetitions"]
    assert template["work_seconds"] in {15, 30}
    assert all(b["duration_s"] == template["work_seconds"] for b in work)
    assert session["main_work_minutes"] == pytest.approx(len(work)*template["work_seconds"]/60)
    assert session["main_work_minutes"] <= evidence["requested_primary_work_minutes"] + .001
    assert_readiness_dose(session)
    assert_rolling_budgets(plan)
    allocation = plan["allocation"]["components"]["Z5"]
    assert allocation["actual"] + allocation["planned"] <= allocation["target"] + .005
    assert allocation["actual_effective"] + allocation["planned_effective"] <= allocation["target_effective"] + .005
    assert allocation["planned_q"] == pytest.approx(session["direct_equivalent_minutes"]["Z5"], abs=.005)
    assert allocation["planned"] > 0
