"""A residual weekly budget cannot turn a complete method into one effort."""
from copy import deepcopy

import pytest

from apps.api import training_plan_engine as engine
from biathlon import method_dosing, method_library
from biathlon.constants import COMPONENTS
from tests.api.test_management_schedule import body, high_capacity_history, run
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness
from tests.api.test_training_plan_engine import Repository, TODAY
from tests.api.test_management_v2 import interval


def native(*, seconds=30., minimum=6, maximum=20, hard=False):
    method = {"id": "REGISTERED-Z5", "zone": "Z5", "purpose": "BUILDING", "structure": "MODEL_INTERVALS",
        "sports": ["Run"], "warmup_min": 15., "cooldown_min": 10., "instructions": "Пълни повторения.",
        "min_work_min": minimum*seconds/60, "max_work_min": maximum*seconds/60,
        "interval_template": {"zone": "Z5", "work_seconds": seconds, "recovery_seconds": 30.,
            "min_repetitions": minimum, "max_repetitions": maximum, "reserve_repetitions": 2,
            "total_capacity_ratio": 2.5}}
    if hard:
        method["execution_limits"] = {"min_repetitions": minimum, "min_work_min": method["min_work_min"]}
    return method


def prepared(method=None, role="BUILDING", readiness=1., capacity=10.):
    method = method or native()
    evidence = {"capacity_minutes": capacity, "target_hr_bpm": None, "target_speed_kmh": None,
        "readiness_policy": {"observed_dose_factor": readiness, "allowed": True}}
    engine._nominal_fraction(method, role, {"building_fraction": .65}, "GENERAL_PREPARATION", evidence, {})
    return method, evidence, Repository().settings


@pytest.mark.parametrize("role,readiness,expected", [("BUILDING", 1., 6), ("BUILDING", .8, 5),
    ("MAINTENANCE", 1., 3), ("MAINTENANCE", .8, 3), ("SUPPORTING", .8, 3)])
def test_native_minimum_uses_original_count_and_existing_role_factor_once(role, readiness, expected):
    method, evidence, settings = prepared(role=role, readiness=readiness)
    assert method["authored_interval_bounds"]["min_repetitions"] == 6
    assert method["interval_template"]["min_repetitions"] == expected
    assert engine._minimum_work(method, evidence, settings) == pytest.approx(expected*.5)
    assert len([b for b in engine._blocks(method, expected*.5, evidence, settings) if b["kind"] == "WORK"]) == expected
    assert engine._blocks(method, (expected-1)*.5, evidence, settings) == []


def test_budget_preparation_never_increases_authored_maximum_or_compounds_reduction():
    method, evidence, _ = prepared(role="MAINTENANCE", capacity=100.)
    assert method["max_work_min"] == 10.
    assert method["interval_template"]["max_repetitions"] == 20
    engine._nominal_fraction(method, "BUILDING", {}, "GENERAL_PREPARATION", evidence, {})
    assert method["interval_template"]["min_repetitions"] == 6
    engine._nominal_fraction(method, "MAINTENANCE", {}, "GENERAL_PREPARATION", evidence, {})
    assert method["interval_template"]["min_repetitions"] == 3


def test_low_capacity_cannot_lower_the_source_floor_or_expand_its_cap():
    method, evidence, settings = prepared(role="MAINTENANCE", capacity=.1)
    assert method["min_work_min"] == 1.5
    assert method["max_work_min"] < method["min_work_min"]
    assert engine._minimum_work(method, evidence, settings) is None
    assert engine._blocks(method, .25, evidence, settings) == []


def test_explicit_execution_and_coach_profile_minima_remain_hard():
    for explicit in (native(hard=True), {**native(), "structure": "METABOLIC_INTERVALS",
                     "interval_profile": native()["interval_template"], "interval_template": None}):
        method, evidence, settings = prepared(explicit, role="MAINTENANCE", readiness=.8)
        assert method["min_work_min"] == 3.
        assert engine._blocks(method, 2.5, evidence, settings) == []


@pytest.mark.parametrize("limits,expected", [({"min_repetitions": 8}, 8), ({"min_work_min": 3.6}, 8)])
def test_stricter_execution_minimum_survives_role_floor(limits, expected):
    explicit = {**native(), "execution_limits": limits}
    method, evidence, settings = prepared(explicit, role="MAINTENANCE", readiness=.8)
    assert method["interval_template"]["min_repetitions"] == expected
    assert engine._minimum_work(method, evidence, settings) == expected*.5
    assert engine._blocks(method, (expected-1)*.5, evidence, settings) == []


@pytest.mark.parametrize("seconds", [15., 27., 30., 90.])
def test_previous_variant_removes_exactly_one_authored_repeat(seconds):
    method, _, _ = prepared(native(seconds=seconds))
    assert engine._previous_work(method, 8*seconds/60) == pytest.approx(7*seconds/60)


def test_combination_cannot_hide_two_short_high_efforts_inside_long_easy_work():
    method, evidence, _ = prepared()
    blocks = [{"kind": "WORK", "zone": "Z5", "duration_min": .5, "combination_part": 1} for _ in range(2)]
    blocks += [{"kind": "WORK", "zone": "Z1", "duration_min": 31., "combination_part": 2}]
    assert not method_dosing.structure_fits(blocks, {"combined_capacities": [evidence, {}]})
    assert method_dosing.structure_fits(blocks[:2]*3+blocks[2:], {"combined_capacities": [evidence, {}]})


def test_threshold_high_uses_secondary_authored_bounds_independent_of_primary():
    profile = body()
    high = interval("Z5", continuous_capacity_min=30.)
    high.update(work_seconds=300., min_repetitions=4, max_repetitions=8, total_capacity_ratio=3.)
    profile["interval_profiles"] = [high]
    method = next(m for m in engine.resolved_methods(profile) if m["structure"] == "THRESHOLD_HIGH")
    evidence = {"capacity_minutes": 60., "target_hr_bpm": 145., "target_speed_kmh": None,
                "resolved_role": "BUILDING"}
    settings = Repository().settings
    method_dosing.prepare_native_budget(method, evidence, profile)
    assert method["source_work_minutes"] == [6., 12.]
    primary = deepcopy(method)
    assert engine._prepare_threshold_high_budget(method, evidence, profile, settings, None, (None, [], []),
        TODAY, primary_fraction=.325, readiness_factor=1.)
    assert method == primary
    contract = evidence["secondary_capacity"]["method_structure_contract"]
    assert contract["minimum_work_minutes"] == 20.
    assert contract["maximum_work_minutes"] == 40.
    blocks = engine._blocks(method, evidence["primary_requested_work"], evidence, settings)
    high_blocks = [b for b in blocks if b["kind"] == "WORK" and b["zone"] == "Z5"]
    assert 4 <= len(high_blocks) <= 8
    assert all(b["duration_min"] == 5. for b in high_blocks)
    assert method_dosing.structure_fits(blocks, evidence)


def test_saved_small_or_nonwhole_intervals_fail_the_frozen_contract():
    _, evidence, _ = prepared(role="MAINTENANCE")
    assert method_dosing.current_structure_known(evidence)
    assert not method_dosing.structure_fits([{"kind": "WORK", "zone": "Z5", "duration_min": .25}], evidence)
    assert not method_dosing.structure_fits([{"kind": "WORK", "zone": "Z5", "duration_min": 1.6}], evidence)
    legacy = deepcopy(evidence)
    legacy.pop("method_structure_contract")
    assert not method_dosing.current_structure_known(legacy)


def test_unfunded_complete_z5_method_is_not_replaced_by_a_tiny_fragment(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    profile = body(sessions_per_week=7, sessions_by_day=[1]*7, mesocycle_anchor=TODAY.isoformat(),
        accent_mode="MANUAL", accents=["Z5"], mixed_sessions_enabled=False)
    profile["max_key_sessions_per_week"] = 0
    profile["interval_profiles"] = [interval("Z5", continuous_capacity_min=10.)]
    profile["component_targets_weekly"] = {z: 500. if z == "Z1" else .3 if z == "Z5" else 0. for z in COMPONENTS}
    recipes = [m for m in engine.resolved_methods(profile) if m["id"] in {"END-VO2-TREF-01-Z5", "RUN-REC-EASY-01"}]
    assert len(recipes) == 2
    monkeypatch.setattr(engine, "resolved_methods", lambda _: deepcopy(recipes))
    monkeypatch.setattr(method_library, "executable_methods", lambda *a, **kw: [])
    plan = run(monkeypatch, profile, high_capacity_history())
    assert any(d["sessions"] for d in plan["days"])
    assert not any(b["kind"] == "WORK" and b["zone"] == "Z5" for d in plan["days"]
                   for s in d["sessions"] for b in s["blocks"])
    row = plan["allocation"]["segments"][0]["components"]["Z5"]
    assert row["planned"] == 0 and row["coverage_status"] == "BELOW_RANGE"
    assert any(r["code"] in {"PERIOD_COMPONENT_REMAINDER", "COMPONENT_BUDGET_EXHAUSTED", "MINIMUM_CAPACITY_DOSE"}
               for d in plan["days"] for r in d["rejected_alternatives"])


@pytest.mark.parametrize("introductory_enabled", [False, True])
def test_low_exposure_gate_applies_before_combination_construction(monkeypatch, introductory_enabled):
    fixed_readiness(monkeypatch, 100.)
    profile = body(sessions_per_week=7, sessions_by_day=[1]*7,
        mesocycle_anchor=TODAY.isoformat(), mixed_sessions_enabled=True)
    full_id = "ONFLOWS-CONTROLLED-Z5-V2"
    short_id = full_id+"-SHORT"
    profile["method_library"] = {"enabled": True,
        "overrides": {short_id: {"enabled": introductory_enabled}}}
    recipes = [m for m in engine.resolved_methods(profile) if m["id"] in {full_id, short_id, "RUN-REC-EASY-01"}]
    assert len(recipes) == 3
    monkeypatch.setattr(engine, "resolved_methods", lambda _: deepcopy(recipes))
    monkeypatch.setattr(method_library, "executable_methods", lambda *a, **kw: [])
    monkeypatch.setattr(engine.planning_history, "component_exposure_dates", lambda *a, **kw: {})
    original_combine = engine.combined_methods.candidates
    combination_inputs = []

    def capture_combinations(candidates, shortage):
        combination_inputs.extend(deepcopy(candidates))
        return original_combine(candidates, shortage)

    monkeypatch.setattr(engine.combined_methods, "candidates", capture_combinations)
    plan = run(monkeypatch, profile, high_capacity_history())
    assert combination_inputs  # The integration exercised combination creation.
    ids = {m["id"] for _, m in combination_inputs}
    assert full_id not in ids
    assert (short_id in ids) is introductory_enabled
    for _, method in combination_inputs:
        if method["id"] == short_id:
            assert method["method_card"]["id"] == short_id
            assert method["method_card"]["original"]["work"] == "15 сек"
            assert method["method_card"]["original"]["repetitions"] == "4–12"
    for day in plan["days"]:
        for session in day["sessions"]:
            assert session["method_id"] != full_id
            if not introductory_enabled:
                assert not any(b["kind"] == "WORK" and b["zone"] == "Z5" for b in session["blocks"])
