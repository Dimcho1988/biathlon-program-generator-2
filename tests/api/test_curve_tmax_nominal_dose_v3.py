"""Building percentages use continuous Tmax; interval ceilings stay separate."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import training_plan_engine as engine
from biathlon import adaptive_methods, speed_duration
from tests.api.test_load_progression import configured, observed
from tests.api.test_readiness_adaptive_plan_v2 import (
    assert_readiness_dose, assert_rolling_budgets, fixed_readiness,
)
from tests.api.test_training_plan_engine import NOW, TODAY, Repository, supported_speed


def model_method():
    return next(method for method in engine.resolved_methods(configured(age_years=25, training_experience_years=5))
                if method["id"] == "ONFLOWS-CONTROLLED-Z4-V2")


@pytest.mark.parametrize("structure, ratio", [("MODEL_INTERVALS", 1.2), ("MODEL_INTERVALS", 1.5),
                                               ("METABOLIC_INTERVALS", 1.2), ("METABOLIC_INTERVALS", 1.5)])
def test_interval_ceiling_cannot_replace_selected_continuous_tmax_fraction(structure, ratio):
    method = model_method()
    method["structure"] = structure
    template = {**method.pop("interval_template"), "total_capacity_ratio": ratio}
    method["interval_template" if structure == "MODEL_INTERVALS" else "interval_profile"] = template
    evidence = {"capacity_minutes": 34., "effort_profile": template, "readiness_dose_factor": .5}
    fraction = engine._nominal_fraction(method, "BUILDING", {"building_fraction": .65},
                                        "GENERAL_PREPARATION", evidence, None)
    assert fraction == .65
    assert evidence["approved_interval_work_capacity_ratio"] == ratio
    assert evidence["nominal_capacity_basis"] == "CONTINUOUS_TMAX_AT_PRESCRIBED_EFFORT"
    # The approved ratio still defines the independent structure ceiling.
    blocks = [{"kind": "WORK", "zone": "Z4", "duration_min": 34.*fraction*.5,
               "target_hr_bpm": None}]
    assert engine._dose_usage(blocks, evidence, "Z4") == pytest.approx(.65*.5/ratio)


def test_missing_building_preference_defaults_to_65_percent_tmax():
    method = model_method()
    evidence = {"capacity_minutes": 34.}
    assert engine._nominal_fraction(method, "BUILDING", {}, "GENERAL_PREPARATION", evidence, None) == .65


@pytest.mark.parametrize("readiness_factor", [.5, .9])
def test_large_interval_ceiling_cannot_raise_the_continuous_tmax_minimum(readiness_factor):
    method = model_method()
    method["interval_template"]["total_capacity_ratio"] = 3.
    evidence = {"capacity_minutes": 34., "target_hr_bpm": None, "target_speed_kmh": None,
                "effort_profile": deepcopy(method["interval_template"]),
                "readiness_dose_factor": readiness_factor}
    original = deepcopy(evidence)
    minimum = engine._minimum_work(method, evidence, Repository().settings)
    fraction = engine._nominal_fraction(method, "BUILDING", {"building_fraction": .65},
                                        "GENERAL_PREPARATION", evidence, None)
    requested = 34.*fraction*readiness_factor
    blocks = engine._blocks(method, requested, evidence, Repository().settings)
    assert minimum == 9.
    assert minimum <= requested
    assert blocks and sum(block["duration_min"] for block in blocks if block["kind"] == "WORK") <= requested
    assert engine._dose_usage(blocks, evidence, "Z4") <= .8*readiness_factor
    assert evidence["effort_profile"] == original["effort_profile"]


def test_composite_minimum_uses_independent_secondary_tmax_without_mutating_its_ceiling():
    body = configured(age_years=25, training_experience_years=5)
    method = next(method for method in engine.resolved_methods(body) if method["structure"] == "ALTERNATING")
    evidence = {"capacity_minutes": 100., "target_hr_bpm": 135., "target_speed_kmh": None,
                "readiness_dose_factor": 1., "secondary_capacity": {
                    "capacity_minutes": 100., "target_hr_bpm": 115., "target_speed_kmh": None,
                    "effort_profile": {"total_capacity_ratio": 3.}}}
    original = deepcopy(evidence)
    minimum = engine._minimum_work(method, evidence, Repository().settings)
    assert minimum == 25.
    assert evidence == original
    blocks = engine._blocks(method, minimum, evidence, Repository().settings)
    assert len([block for block in blocks if block["kind"] == "WORK"]) == 4
    # Published upper-dose accounting retains the approved secondary ceiling.
    assert engine._dose_usage(blocks, evidence, method["zone"]) == pytest.approx(1/6)
    assert engine._minimum_dose_usage(blocks, evidence, method["zone"]) == .25


def test_paired_minimum_reports_the_least_complete_independent_tmax_fraction():
    evidence = {"capacity_minutes": 40., "effort_profile": {"total_capacity_ratio": 1.5},
                "paired_capacity": {"zone": "Z4", "capacity_minutes": 20.,
                                    "effort_profile": {"total_capacity_ratio": 1.2}}}
    blocks = [{"kind": "WORK", "zone": "Z3", "duration_min": 8., "target_hr_bpm": None, "session_index": 1},
              {"kind": "WORK", "zone": "Z4", "duration_min": 8., "target_hr_bpm": None, "session_index": 2}]
    assert engine._minimum_dose_usage(blocks, evidence, "Z3") == .2
    assert engine._dose_usage(blocks, evidence, "Z3") == pytest.approx(.4/1.2)


def test_mixed_primary_minimum_cannot_be_filled_by_more_easy_work():
    evidence = {"capacity_minutes": 20., "secondary_capacity": {"capacity_minutes": 100.}}
    blocks = [{"kind": "WORK", "zone": "Z3", "duration_min": 2., "target_hr_bpm": None},
              {"kind": "WORK", "zone": "Z1", "duration_min": 60., "target_hr_bpm": None}]
    assert engine._minimum_dose_usage(blocks, evidence, "Z3", primary_only=True) == .1
    assert engine._minimum_dose_usage(blocks, evidence, "Z3") == pytest.approx(.7)


@pytest.mark.parametrize("selected_fraction", [.6, .65, .7])
def test_short_building_structure_retains_the_selected_tmax_fraction(selected_fraction):
    method = adaptive_methods.short_variant(model_method())
    assert method["developmental_variant"]
    evidence = {"capacity_minutes": 34., "dose_capacity_basis": "INDEPENDENT_CONTINUOUS_TMAX"}
    assert engine._nominal_fraction(method, "BUILDING", {"building_fraction": selected_fraction},
                                    "GENERAL_PREPARATION", evidence, None) == selected_fraction


def test_strength_nominal_dose_keeps_circuit_capacity_without_continuous_tmax():
    method = next(method for method in engine.resolved_methods(configured(strength_enabled=True))
                  if method["zone"] == "STR")
    evidence = engine.capacity_for(method, Repository().settings, None, (None, [], []), TODAY)
    assert evidence["capacity_source"] == "STRENGTH_METHOD_PROFILE"
    assert engine._nominal_fraction(method, "BUILDING", {"building_fraction": .65},
                                    "GENERAL_PREPARATION", evidence, None) == 1.
    assert "nominal_capacity_basis" not in evidence


def test_large_component_remainder_cannot_expand_selected_building_fraction(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=25, training_experience_years=5,
                      building_fraction=.65, available_minutes=[240]*7,
                      horizon_mode="MANUAL", program_end=(TODAY+timedelta(days=6)).isoformat(),
                      component_targets_weekly={"Z1": 300.})
    body["planning_controls"].update(accents=["Z1"], sessions_per_week=2,
                                     sessions_by_day=[1, 0, 0, 1, 0, 0, 0])
    method = next(method for method in engine.resolved_methods(body) if method["id"] == "END-LONG-Z1-01-BUILD")
    monkeypatch.setattr(engine, "resolved_methods", lambda _: [deepcopy(method)])
    repo, _, _ = observed()
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    sessions = [session for day in plan["days"] for session in day["sessions"] if session["purpose"] == "BUILDING"]
    assert sessions
    assert any(session["dose_evidence"]["selection"]["component_allocation"]["Z1"] >
               session["canonical_effective_load"]["Z1"] + .5 for session in sessions)
    for session in sessions:
        assert session["dose_evidence"]["base_fraction"] == .65
        assert_readiness_dose(session)
    assert_rolling_budgets(plan)


@pytest.mark.parametrize("selected_fraction, readiness, expected_fraction", [
    (.6, 50., .3), (.7, 50., .35), (.6, 90., .54), (.7, 90., .63), (.65, 90., .585),
])
def test_curve_supported_interval_building_scales_selected_fraction_once(
        monkeypatch, selected_fraction, readiness, expected_fraction):
    fixed_readiness(monkeypatch, readiness)
    speed = supported_speed(Repository().settings)
    speed.update(sport="Run", source_generation_id="generation-one", source_revision=1)
    measured_curve = speed_duration.calibrated([entry["payload"] for entry in speed["tests"]])
    effort_speed = measured_curve.speed(1200.)*3.6
    body = configured(age_years=25, training_experience_years=5,
                      building_fraction=selected_fraction, available_minutes=[180]*7,
                      horizon_mode="MANUAL", program_end=TODAY+timedelta(days=6),
                      interval_profiles=[{
                          "zone": "Z4", "sport": "Run", "continuous_capacity_min": 20.,
                          "assessed_on": TODAY.isoformat(), "effort": "Repeatable individual curve-supported effort",
                          "work_seconds": 30, "recovery_seconds": 30,
                          "min_repetitions": 2, "max_repetitions": 20,
                          "reserve_repetitions": 2, "total_capacity_ratio": 1.2,
                          "target_speed_kmh": effort_speed, "speed_basis": "FLAT_EQUIVALENT",
                      }])
    body["program_end"] = body["program_end"].isoformat()
    body["planning_controls"].update(accents=["Z4"], sessions_per_week=1,
                                     sessions_by_day=[0, 1, 0, 0, 0, 0, 0], threshold_days=[1])
    method = next(method for method in engine.resolved_methods(body) if method["structure"] == "METABOLIC_INTERVALS")
    monkeypatch.setattr(engine, "resolved_methods", lambda _: [deepcopy(method)])
    monkeypatch.setattr(engine.model_service, "speed_view", lambda *args, **kwargs: deepcopy(speed))
    repo, _, _ = observed()
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    sessions = [session for day in plan["days"] for session in day["sessions"]]
    assert len(sessions) == 1
    session = sessions[0]
    evidence = session["dose_evidence"]
    assert session["purpose"] == "BUILDING"
    assert evidence["capacity_source"] in {"SPEED_DURATION", "BLENDED_DOSING_CURVE"}
    assert evidence["base_fraction"] == selected_fraction
    assert evidence["fraction"] == pytest.approx(expected_fraction)
    assert evidence["requested_primary_work_minutes"] / evidence["capacity_minutes"] == pytest.approx(expected_fraction, abs=.0001)
    assert session["main_work_minutes"] <= evidence["requested_primary_work_minutes"] + .001
    assert evidence["approved_interval_work_capacity_ratio"] == 1.2
    work = [block for block in session["blocks"] if block["kind"] == "WORK"]
    assert work and all(block["duration_min"] == .5 for block in work)
    assert all(block["target_hr_bpm"] is None for block in work)
    assert all(block["target_speed_kmh"] == pytest.approx(effort_speed, abs=.001) for block in work)
    assert_readiness_dose(session)
    assert_rolling_budgets(plan)
