"""A residual weekly budget cannot turn a complete method into one effort."""
from copy import deepcopy

import pytest

from apps.api import training_plan_engine as engine
from biathlon import daily_intensity, method_dosing, method_library, planning_development
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


@pytest.mark.parametrize("role,readiness,work,reference,counts", [
    ("BUILDING", 1., 10., 10., True), ("MAINTENANCE", 1., 6., 10., False),
    ("BUILDING", .8, 9., 9., True), ("MAINTENANCE", .8, 6., 10., False),
])
def test_authored_full_dose_precedes_role_readiness_development_and_daily_accounting(
        role, readiness, work, reference, counts):
    method, evidence, settings = prepared(role=role, readiness=readiness, capacity=20.)
    profile = {"building_fraction": .65}
    fraction = engine._nominal_fraction(method, role, profile, "GENERAL_PREPARATION", evidence, {})
    evidence.update(base_fraction=fraction, structure_base_fraction=fraction)
    assert evidence["unbounded_full_method_work_minutes"] == 32.5
    assert evidence["full_method_work_minutes"] == 10.
    requested = evidence["capacity_minutes"]*fraction*evidence["readiness_dose_factor"]
    assert requested == pytest.approx(work)
    blocks = engine._blocks(method, requested, evidence, settings)
    assert sum(b["duration_min"] for b in blocks if b["kind"] == "WORK") == work
    session = {"method_id": method["id"], "purpose": role, "zone": "Z5", "blocks": blocks,
               "dose_evidence": evidence}
    assert planning_development.is_developmental(session) is counts
    contract = engine._daily_intensity_contract(method, evidence, profile, role, {})
    assert contract["references"][0]["minutes"] == reference
    assert daily_intensity.usage(blocks, contract["references"]) == pytest.approx(1. if counts else .6)
    restored = daily_intensity.saved_contract(session)
    assert restored["references"][0]["minutes"] == reference
    ceiling = engine._dose_ceiling(method, role, profile, "GENERAL_PREPARATION", evidence, {})
    assert engine._dose_fits(blocks, evidence, "Z5", ceiling*evidence["readiness_dose_factor"])
    if role == "MAINTENANCE":
        above_role = engine._blocks(method, work+.5, evidence, settings)
        assert above_role and not engine._dose_fits(above_role, evidence, "Z5", ceiling)


def test_combination_uses_each_bounded_full_native_method_for_development_and_daily_cost():
    first, second = native(), native()
    second.update(id="REGISTERED-Z4", zone="Z4")
    second["interval_template"]["zone"] = "Z4"
    method = {"id": "REGISTERED-PAIR", "zone": "Z5", "purpose": "BUILDING", "structure": "COMBINED_METHODS",
              "combined_methods": [first, second], "warmup_min": 15., "cooldown_min": 10.}
    evidence = {"capacity_minutes": 20., "combined_capacities": [
        {"capacity_minutes": 20., "target_hr_bpm": None, "target_speed_kmh": None} for _ in range(2)]}
    engine._nominal_fraction(method, "BUILDING", {}, "GENERAL_PREPARATION", evidence, {})
    assert [b["nominal_work_minutes"] for b in evidence["combined_budgets"]] == [10., 10.]
    blocks = engine._blocks(method, 10., evidence, Repository().settings)
    session = {"method_id": method["id"], "purpose": "BUILDING", "zone": "Z5", "blocks": blocks,
               "dose_evidence": evidence}
    development = planning_development.session_evidence(session)
    assert development["counts"]
    assert [c["maintenance_work_minutes"] for c in development["components"]] == [6., 6.]
    contract = engine._daily_intensity_contract(method, evidence, {}, "BUILDING", {})
    assert [r["minutes"] for r in contract["references"]] == [10., 10.]
    assert daily_intensity.usage(blocks, contract["references"]) == pytest.approx(2.)
    assert not daily_intensity.check(blocks, contract, {"used": 0., "building": False, "double": False})
    assert daily_intensity.usage(blocks, daily_intensity.saved_contract(session)["references"]) == pytest.approx(2.)


def test_native_role_dose_below_authored_ceiling_keeps_its_existing_capacity_formula():
    method, evidence, _ = prepared(capacity=2.)
    fraction = engine._nominal_fraction(method, "BUILDING", {}, "GENERAL_PREPARATION", evidence, {})
    assert fraction == 2.5*.65
    assert evidence["full_method_work_minutes"] == evidence["unbounded_full_method_work_minutes"] == 3.25


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


@pytest.mark.parametrize("role,readiness,high_budget,high_reference", [
    ("BUILDING", 1., 20., 40.), ("BUILDING", .8, 18., 36.),
    ("MAINTENANCE", 1., 12., 40.), ("MAINTENANCE", .8, 12., 40.),
])
def test_threshold_high_halves_its_own_bounded_full_dose_before_single_readiness(
        role, readiness, high_budget, high_reference):
    profile = body()
    high = interval("Z5", continuous_capacity_min=30.)
    high.update(work_seconds=300., min_repetitions=2, max_repetitions=8, total_capacity_ratio=3.)
    profile["interval_profiles"] = [high]
    method = next(m for m in engine.resolved_methods(profile) if m["structure"] == "THRESHOLD_HIGH")
    evidence = {"capacity_minutes": 60., "target_hr_bpm": 145., "target_speed_kmh": None,
                "readiness_policy": {"observed_dose_factor": readiness, "allowed": True}}
    nominal = engine._nominal_fraction(method, role, profile, "GENERAL_PREPARATION", evidence, {})
    factor = evidence["readiness_dose_factor"]
    assert engine._prepare_threshold_high_budget(method, evidence, profile, Repository().settings,
        None, (None, [], []), TODAY, primary_fraction=nominal/2, readiness_factor=factor)
    evidence.update(base_fraction=nominal/2, structure_base_fraction=nominal/2)
    secondary = evidence["secondary_capacity"]
    assert secondary["unbounded_full_method_work_minutes"] == 58.5
    assert secondary["full_method_work_minutes"] == 40.
    assert evidence["combination_high_work_cap"] == pytest.approx(high_budget)
    blocks = engine._blocks(method, evidence["primary_requested_work"], evidence, Repository().settings)
    high_work = sum(b["duration_min"] for b in blocks if b["kind"] == "WORK" and b["zone"] == "Z5")
    assert high_work == 5*int(high_budget//5)
    contract = engine._daily_intensity_contract(method, evidence, profile, role, {})
    assert next(r["minutes"] for r in contract["references"] if r["zones"] == ["Z5"]) == high_reference
    session = {"method_id": method["id"], "purpose": role, "zone": "Z3", "blocks": blocks, "dose_evidence": evidence}
    restored = daily_intensity.saved_contract(session)
    assert [r["minutes"] for r in restored["references"]] == pytest.approx([r["minutes"] for r in contract["references"]])
    assert daily_intensity.usage(blocks, contract["references"]) <= (1. if role == "BUILDING" else .6)
    ceiling = engine._dose_ceiling(method, role, profile, "GENERAL_PREPARATION", evidence, {})
    assert engine._dose_fits(blocks, evidence, "Z3", ceiling*factor)
    # An otherwise whole interval recipe must not exceed its allocated half
    # merely because the generic interval budget could contain more work.
    enlarged = deepcopy(blocks)
    high_block = next(b for b in blocks if b["kind"] == "WORK" and b["zone"] == "Z5")
    enlarged.append(deepcopy(high_block))
    assert method_dosing.structure_fits(enlarged, evidence)
    assert not engine._dose_fits(enlarged, evidence, "Z3", ceiling*factor)


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
