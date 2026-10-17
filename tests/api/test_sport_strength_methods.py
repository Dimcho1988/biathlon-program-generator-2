"""Whole sport programmes, stable exercise identity, and per-session role doses."""
from copy import deepcopy
from datetime import timedelta

import pytest
from pydantic import ValidationError

from apps.api import training_plan_engine as engine
from apps.api.management_schemas import ManagementProfile
from biathlon import strength_methods, method_registry, adaptive_methods
from biathlon.constants import COMPONENTS
from tests.api.test_training_plan_engine import Repository, TODAY
from tests.api.test_management_schedule import body, run


def prescription(sport, key, role, target=.6, period="GENERAL_PREPARATION", readiness=100):
    p = body(); p.update(sport=sport, strength_enabled=True, strength_maintenance_fraction=target)
    method = next(m for m in strength_methods.methods(p) if m["strength_profile"]["key"] == key)
    cap = engine.capacity_for(method, Repository().settings, None, (None, [], []), TODAY)
    cap["readiness_policy"] = adaptive_methods.readiness_policy(method, p, dict.fromkeys(COMPONENTS, readiness))
    fraction = engine._nominal_fraction(method, role, p, period, cap, p["planning_controls"])
    work = engine._quantized_work(method, cap["capacity_minutes"]*fraction*cap["readiness_dose_factor"])
    blocks = engine._blocks(method, work, cap, Repository().settings)
    return method, cap, blocks


@pytest.mark.parametrize("sport", ["Run", "NordicSki"])
@pytest.mark.parametrize("key", ["INTRO", "MAX", "END", "POWER"])
@pytest.mark.parametrize("target", [.5, .55, .6])
def test_reduced_roles_keep_every_exercise_and_50_to_60_percent_total_repetitions(sport, key, target):
    method, _, building = prescription(sport, key, "BUILDING", target)
    full = sum(b.get("repetitions", 0) for b in building)
    doses = []
    for role in ("MAINTENANCE", "ACTIVATION"):
        m, cap, blocks = prescription(sport, key, role, target, readiness=50)
        work = [b for b in blocks if b["kind"] == "WORK"]
        assert {b["exercise_id"] for b in work} == set(strength_methods.BANKS[sport])
        dose = sum(b["repetitions"] for b in work)/full
        assert .5-1e-9 <= dose <= .6+1e-9
        assert cap["readiness_dose_factor"] == 1.
        assert engine._dose_usage(blocks, cap, "STR") == pytest.approx(dose)
        assert engine._dose_fits(blocks, cap, "STR", dose)
        assert not engine._dose_fits(blocks, cap, "STR", dose-.01)
        assert all(b["repetitions"] == next(e["reps"] for e in m["strength_profile"]["exercises"] if e["id"] == b["exercise_id"]) for b in work)
        doses.append(dose)
    assert doses[0] == doses[1]


@pytest.mark.parametrize("sport", ["Run", "NordicSki"])
def test_main_sport_owns_bank_across_means_and_methods(sport):
    for actual in ("Run", "NordicSki", "RollerSki"):
        p = body(); p.update(sport=sport, actual_sport=actual, strength_enabled=True)
        methods = strength_methods.methods(p)
        assert len(methods) == 4
        assert all([e["id"] for e in m["strength_profile"]["exercises"]] == list(strength_methods.BANKS[sport]) for m in methods)
        for phase in strength_methods.PERIODS:
            assert any(phase in m["periods"] for m in methods)
    assert not any("plank" in key for key in strength_methods.BANKS[sport])


def test_heavy_complete_programme_includes_rests_once_and_no_tmax_ceiling():
    method, cap, blocks = prescription("Run", "MAX", "BUILDING")
    work = [b for b in blocks if b["kind"] == "WORK"]
    assert len(work) == 16 and all(b["repetitions"] == 5 for b in work)
    assert {b["reserve_repetitions"] for b in work} == {2, 3}
    duration = engine._strength_duration(blocks)
    assert duration["work_minutes"] == pytest.approx(16*5*4/60, abs=.001)
    assert duration["rest_minutes"] == 12*3+3*2
    direct, effective, _ = engine._canonical_load(blocks, Repository().settings, [], TODAY)
    assert direct["STR"] == pytest.approx(duration["main_minutes"], abs=.001)
    assert effective["STR"] == pytest.approx(direct["STR"])
    assert engine._dose_ceiling(method, "BUILDING", {}, "GENERAL_PREPARATION", cap, None, .8) == 1.
    assert sum(b["duration_min"] for b in blocks) == pytest.approx(direct["STR"]+15)


def test_dose_reduction_cannot_drop_core_exercises_or_go_below_role_minimum():
    method, cap, blocks = prescription("NordicSki", "MAX", "MAINTENANCE")
    minimum = engine._minimum_work(method, cap, Repository().settings)
    assert engine._blocks(method, minimum, cap, Repository().settings)
    assert not engine._blocks(method, engine._previous_work(method, minimum), cap, Repository().settings)
    assert len({b["exercise_id"] for b in blocks if b.get("exercise_id")}) == 5


def test_reentry_has_larger_reserve_without_second_aerobic_fraction():
    method, cap, blocks = prescription("Run", "INTRO", "BUILDING", period="RE_ENTRY", readiness=50)
    assert cap["resolved_role"] == "MAINTENANCE"
    assert .5 <= engine._dose_usage(blocks, cap, "STR") <= .6
    assert all(b["reserve_repetitions"] >= 4 for b in blocks if b["kind"] == "WORK")


def test_default_registry_replaces_legacy_stability_circuit_with_capacity_programmes():
    p = body(); p.update(strength_enabled=True)
    methods = method_registry.executable_methods(p, {})
    strength = [m for m in methods if m["zone"] == "STR"]
    assert len(strength) == 4
    assert all(m["structure"] == "STRENGTH_SETS" for m in strength)
    assert "50–60%" in strength[0]["method_card"]["guidance"]["adjustment"]
    assert strength[0]["method_card"]["coach_profile"]["strength"]["sources"]


def test_profile_accepts_legacy_and_rejects_invalid_reduced_percentages():
    p = body()
    assert ManagementProfile.model_validate(p).strength_maintenance_fraction == .6
    for value in (.49, .61, float("nan")):
        with pytest.raises(ValidationError):
            ManagementProfile.model_validate({**p, "strength_maintenance_fraction": value})


def test_generator_keeps_complete_sets_and_canonical_time_budgets(monkeypatch):
    p = body(sessions_per_week=7, strength_days=[0, 3], max_strength_sessions=2,
             accent_mode="MANUAL", accents=["STR"], mixed_sessions_enabled=False)
    p.update(strength_enabled=True, strength_maintenance_fraction=.6)
    original = deepcopy(p)
    plan = run(monkeypatch, p, Repository())
    found = []
    for day in plan["days"]:
        for session in day["sessions"]:
            if session["zone"] != "STR":
                continue
            found.append(session)
            strength = session["dose_evidence"]["strength_prescription"]
            assert strength["exercise_bank_id"] == "Run-capacity-v1"
            assert len(strength["exercises"]) == 4 and all(e["sets"] >= 1 for e in strength["exercises"])
            assert strength["total_sets"] == sum(b["kind"] == "WORK" for b in session["blocks"])
            assert session["dose_evidence"]["selection"]["diversity_penalty"] == 0.
            assert session["total_minutes"] == pytest.approx(sum(b["duration_min"] for b in session["blocks"]), abs=.002)
            assert session["direct_equivalent_minutes"]["STR"] == pytest.approx(session["strength_duration"]["main_minutes"], abs=.002)
            assert all(q <= day["load_budget"]["components"][z]["deficit_effective"]+.002 for z,q in session["canonical_effective_load"].items())
    assert found
    assert p == original


def test_taper_power_is_activation_with_same_reduced_budget(monkeypatch):
    from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness
    fixed_readiness(monkeypatch, 100.)
    p = body(sessions_per_week=7, strength_days=[0, 3], mixed_sessions_enabled=False)
    p.update(strength_enabled=True)
    power = next(m for m in strength_methods.methods(p) if m["strength_profile"]["key"] == "POWER")
    monkeypatch.setattr(engine, "resolved_methods", lambda _: [deepcopy(power)])
    repo = Repository()
    repo.events[0].update(start_date=(TODAY+timedelta(days=6)).isoformat(), end_date=(TODAY+timedelta(days=6)).isoformat())
    plan = run(monkeypatch, p, repo)
    sessions = [s for day in plan["days"] for s in day["sessions"] if s["zone"] == "STR"]
    assert sessions
    for session in sessions:
        assert session["purpose"] == session["dose_evidence"]["resolved_role"] == "ACTIVATION"
        assert .5 <= session["dose_evidence"]["strength_prescription"]["dose_fraction"] <= .6
