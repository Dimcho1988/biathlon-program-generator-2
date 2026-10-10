"""Executable method alternatives keep their effort anchors and load budgets."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import training_plan_engine as engine
from biathlon.training_methods import METHODS, VARIETY_VERSION, resolved_methods, source_catalog
from tests.api.test_management_schedule import body, run
from tests.api.test_management_v2 import interval
from tests.api.test_training_plan_engine import Repository, TODAY


def resolved(**changes):
    p = body(mixed_sessions_enabled=False)
    p.update(changes)
    return resolved_methods(p)


def test_catalog_adds_actual_patterns_without_activating_unresolved_source_examples():
    p = body()
    before = deepcopy(p)
    methods = resolved_methods(p)
    assert p == before
    assert [m["id"] for m in methods[:len(METHODS)]] == [m["id"] for m in METHODS]
    assert len({m["id"] for m in methods}) == len(methods)
    assert all(m["method_family"] and m["method_family_version"] == VARIETY_VERSION for m in methods)
    assert {"Z3_LONG_REPETITIONS", "Z3_SHORT_REPETITIONS", "Z2_TWO_PARTS", "Z1_TWO_PARTS"} <= {
        m["method_family"] for m in methods}
    assert source_catalog()["method_count"] == 42
    assert not any(m["source_definition_executable"] for m in source_catalog()["methods"])


@pytest.mark.parametrize("suffix,work,rest", [("LONG", 25., 1.), ("SHORT", 10.5, .5)])
def test_standalone_threshold_patterns_use_parent_capacity_and_real_complete_blocks(suffix, work, rest):
    methods = resolved()
    m = next(m for m in methods if m["id"] == f"ONFLOWS-Z3-{suffix}-REPETITIONS-V1")
    parent = next(p for p in methods if p["id"] == m["variation_parent_id"])
    settings = Repository().settings
    context = (None, [], [])
    evidence = engine.capacity_for(m, settings, None, context, TODAY)
    assert evidence == engine.capacity_for(parent, settings, None, context, TODAY)
    blocks = engine._blocks(m, work, evidence, settings)
    work_blocks = [b for b in blocks if b["kind"] == "WORK"]
    rest_blocks = [b for b in blocks if b["kind"] == "RECOVERY"]
    total_work = sum(b["duration_min"] for b in work_blocks)
    assert 0 < total_work <= work
    assert len(rest_blocks) == len(work_blocks) - 1
    assert all(b["duration_min"] == rest for b in rest_blocks)
    assert all(b["zone"] == "Z3" for b in work_blocks)
    if suffix == "LONG":
        assert all(6 <= b["duration_min"] <= 10 for b in work_blocks)
        assert engine._blocks(m, 11.9, evidence, settings) == []
    else:
        assert all(b["duration_s"] == 60 and b["target_hr_bpm"] is None
                   and b["primary_control"] == "EFFORT_AND_QUALITY" for b in work_blocks)
        assert engine._blocks(m, 5.9, evidence, settings) == []
    assert engine._dose_usage(blocks, evidence, "Z3") == pytest.approx(total_work / evidence["capacity_minutes"])
    direct, effective, _ = engine._canonical_load(blocks, settings, [], TODAY)
    assert direct["Z3"] > 0 and direct["Z1"] > 0 and effective["Z3"] > 0


@pytest.mark.parametrize("zone", ["Z1", "Z2"])
def test_aerobic_two_parts_have_one_accounted_transition_and_same_supported_effort(zone):
    m = next(m for m in resolved() if m["id"] == f"ONFLOWS-{zone}-TWO-PARTS-V1")
    settings = Repository().settings
    evidence = engine.capacity_for(m, settings, None, (None, [], []), TODAY)
    blocks = engine._blocks(m, 40., evidence, settings)
    parts = [b for b in blocks if b["kind"] == "WORK"]
    rests = [b for b in blocks if b["kind"] == "RECOVERY"]
    assert [b["duration_min"] for b in parts] == [20., 20.]
    assert all(b["zone"] == zone and b["target_hr_bpm"] == evidence["target_hr_bpm"] for b in parts)
    assert len(rests) == 1 and rests[0]["duration_min"] == 2.
    assert sum(b["duration_min"] for b in blocks) == 40. + 2. + m["warmup_min"] + m["cooldown_min"]
    direct, _, _ = engine._canonical_load(blocks, settings, [], TODAY)
    assert direct[zone] > 0


@pytest.mark.parametrize("zone", ["Z4", "Z5"])
def test_subdivided_individual_intervals_preserve_assessed_anchor_work_cap_and_full_rest(zone):
    p = interval(zone=zone)
    original = deepcopy(p)
    methods = resolved(interval_profiles=[p])
    parent = next(m for m in methods if m["id"] == f"END-VO2-TREF-01-{zone}")
    m = next(m for m in methods if m["id"] == parent["id"] + "-SPLIT-V1")
    settings = Repository().settings
    evidence = engine.capacity_for(m, settings, None, (None, [], []), TODAY)
    parent_evidence = engine.capacity_for(parent, settings, None, (None, [], []), TODAY)
    assert p == original
    assert evidence["capacity_minutes"] == parent_evidence["capacity_minutes"]
    cp = m["interval_profile"]
    assert cp["continuous_capacity_min"] == p["continuous_capacity_min"]
    assert cp["total_capacity_ratio"] == p["total_capacity_ratio"]
    assert cp["target_speed_kmh"] == p["target_speed_kmh"]
    assert cp["recovery_seconds"] == p["recovery_seconds"]
    assert m["max_work_min"] <= parent["max_work_min"]
    blocks = engine._blocks(m, m["max_work_min"], evidence, settings)
    works = [b for b in blocks if b["kind"] == "WORK"]
    rests = [b for b in blocks if b["kind"] == "RECOVERY"]
    assert len(works) == cp["max_repetitions"] and len(rests) == len(works) - 1
    assert all(b["duration_s"] == p["work_seconds"] / 2 and b["target_hr_bpm"] is None for b in works)
    assert all(b["duration_s"] == p["recovery_seconds"] for b in rests)
    assert sum(b["duration_min"] for b in works) <= parent["max_work_min"]
    stale = deepcopy(m)
    stale["interval_profile"]["assessed_on"] = (TODAY - timedelta(days=43)).isoformat()
    assert engine.capacity_for(stale, settings, None, (None, [], []), TODAY) is None


def test_automatic_subdivision_does_not_invent_z5_capacity_or_raise_model_ratio():
    methods = resolved()
    settings = Repository().settings
    for zone in ("Z4", "Z5"):
        parent = next(m for m in methods if m["id"] == f"ONFLOWS-CONTROLLED-{zone}-V2")
        child = next(m for m in methods if m["id"] == parent["id"] + "-SPLIT-V1")
        assert child["interval_template"]["total_capacity_ratio"] == parent["interval_template"]["total_capacity_ratio"]
        parent_cap = engine.capacity_for(parent, settings, None, (None, [], []), TODAY)
        child_cap = engine.capacity_for(child, settings, None, (None, [], []), TODAY)
        if zone == "Z5":
            assert parent_cap is child_cap is None
        else:
            assert child_cap["capacity_minutes"] == parent_cap["capacity_minutes"]


def test_families_follow_execution_and_developmental_transform_not_variant_names():
    methods = resolved(interval_profiles=[interval()])
    parent = next(m for m in methods if m["id"] == "END-VO2-TREF-01-Z4")
    child = next(m for m in methods if m["id"] == parent["id"] + "-SPLIT-V1")
    assert child["method_family"] != parent["method_family"]
    young = resolved(age_years=16, interval_profiles=[interval()])
    a = next(m for m in young if m["id"] == parent["id"] + "-SHORT")
    b = next(m for m in young if m["id"] == parent["id"] + "-SPLIT-V1-SHORT")
    assert a["interval_profile"] == b["interval_profile"]
    assert a["method_family"] == b["method_family"]


@pytest.mark.parametrize("suffix", ["LONG", "SHORT"])
def test_new_threshold_templates_are_selectable_as_real_budgeted_sessions(monkeypatch, suffix):
    m = next(m for m in resolved() if m["id"] == f"ONFLOWS-Z3-{suffix}-REPETITIONS-V1")
    monkeypatch.setattr(engine, "resolved_methods", lambda _: [deepcopy(m)])
    p = body(sessions_per_week=3, intensity_days=[TODAY.weekday()], threshold_days=[TODAY.weekday()],
             accent_mode="MANUAL", accents=["Z3"], accent_index=1.5, mixed_sessions_enabled=False)
    # Avoid an unrelated late recovery week in this fixture's mesocycle.
    p["planning_controls"].update(mesocycle_anchor=TODAY.isoformat())
    plan = run(monkeypatch, p, Repository())
    sessions = [s for d in plan["days"] for s in d["sessions"]]
    assert sessions
    for day in plan["days"]:
        for session in day["sessions"]:
            assert session["method_id"] == m["id"]
            assert not session["double_threshold"]
            assert session["main_work_minutes"] > 0
            assert all(v <= day["load_budget"]["components"][z]["deficit_effective"] + .001
                       for z, v in session["canonical_effective_load"].items())
            assert sum(b["duration_min"] for b in session["blocks"]) == pytest.approx(session["total_minutes"], abs=.002)


def test_half_ready_strength_produces_one_complete_circuit_with_accounted_q_and_e(monkeypatch):
    m = next(m for m in resolved(strength_enabled=True, strength_circuits=2)
             if m["structure"] == "STRENGTH_CIRCUIT")
    assert m["min_work_min"] == 3. and m["max_work_min"] == 6.
    monkeypatch.setattr(engine, "resolved_methods", lambda _: [deepcopy(m)])
    original_simulate = engine.recovery_v2.simulate

    def half_ready(*args, **kwargs):
        result = original_simulate(*args, **kwargs)
        for component in result["current"]:
            component["readiness_percent"] = 50. if component["zone"] == "STR" else 100.
        return result

    monkeypatch.setattr(engine.recovery_v2, "simulate", half_ready)
    p = body(sessions_per_week=7, strength_days=[TODAY.weekday()],
             accent_mode="MANUAL", accents=["STR"], mixed_sessions_enabled=False)
    p.update(strength_enabled=True, strength_circuits=2, max_key_sessions_per_week=0)
    p["planning_controls"].update(mesocycle_anchor=TODAY.isoformat())
    plan = run(monkeypatch, p, Repository())
    sessions = [(day, session) for day in plan["days"] for session in day["sessions"]]
    assert sessions
    day, session = sessions[0]
    assert session["method_id"] == m["id"]
    assert session["dose_evidence"]["readiness_dose_factor"] == .5
    assert session["dose_evidence"]["requested_primary_work_minutes"] == 3.
    work = [b for b in session["blocks"] if b["kind"] == "WORK" and b["zone"] == "STR"]
    transitions = [b for b in session["blocks"] if b["kind"] == "TRANSITION"]
    assert len(work) == 9 and len(transitions) == 8
    assert sum(b["duration_s"] for b in work) == 180
    assert all("Кръг 1:" in b["label"] for b in work)
    assert session["direct_equivalent_minutes"]["STR"] == pytest.approx(7.)
    assert session["canonical_effective_load"]["STR"] == pytest.approx(7.)
    assert all(v <= day["load_budget"]["components"][z]["deficit_effective"] + .001
               for z, v in session["canonical_effective_load"].items())
