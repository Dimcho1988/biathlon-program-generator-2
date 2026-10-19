"""Trial reuse preserves prescriptions and keeps source budgets independent."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from apps.api import training_plan_engine as engine
from biathlon import adaptive_methods
from tests.api.test_curve_tmax_nominal_dose_v3 import model_method
from tests.api.test_generator_history_projection_reuse import generator_inputs
from tests.api.test_load_progression import configured
from tests.api.test_sourced_method_library import adapter, recipe
from tests.api.test_training_plan_engine import NOW, TODAY, Repository, reference_speed


def budget_fixture(kind, monkeypatch):
    settings = Repository().settings
    if kind == "library":
        return adapter(monkeypatch, recipe())
    method = model_method()
    capacity = {"capacity_minutes": 34., "target_hr_bpm": None,
                "target_speed_kmh": None, "effort_profile": deepcopy(method["interval_template"])}
    if kind == "continuous":
        method.update(structure="CONTINUOUS", zone="Z2")
    elif kind == "strength":
        method = next(m for m in engine.resolved_methods(configured(strength_enabled=True))
                      if m["zone"] == "STR")
        capacity = engine.capacity_for(method, settings, None, (None, [], []), TODAY)
    elif kind in {"combined", "nested_combined"}:
        second, second_cap, _ = budget_fixture("continuous", monkeypatch)
        method = {**method, "structure": "COMBINED_METHODS", "combined_methods": [method, second]}
        capacity = {**capacity, "combined_capacities": [capacity, second_cap]}
        if kind == "nested_combined":
            method = {**method, "combined_methods": [method, second]}
            capacity = {**capacity, "combined_capacities": [capacity, second_cap]}
    elif kind == "native_resolved":
        engine._nominal_fraction(method, "BUILDING", {}, "SPECIAL_PREPARATION", capacity, None)
    return method, capacity, settings


def prescribe(method, capacity, settings, role):
    profile = {"building_fraction": .65}
    capacity["readiness_policy"] = adaptive_methods.readiness_policy(
        method, profile, dict.fromkeys(engine.COMPONENTS, 80.))
    fraction = engine._nominal_fraction(method, role, profile, "SPECIAL_PREPARATION", capacity, None)
    minimum = engine._minimum_work(method, capacity, settings)
    requested = capacity["capacity_minutes"] * fraction * capacity["readiness_dose_factor"]
    work = engine._quantized_work(method, min(method["max_work_min"], requested))
    blocks = engine._blocks(method, work, capacity, settings)
    ceiling = engine._dose_ceiling(method, role, profile, "SPECIAL_PREPARATION", capacity, None)
    return {"method": method, "capacity": capacity, "minimum": minimum, "blocks": blocks,
            "ceiling": ceiling, "fits": engine._dose_fits(blocks, capacity, method["zone"], ceiling)}


@pytest.mark.parametrize("kind", ["native", "native_resolved", "continuous", "library",
                                   "combined", "nested_combined", "strength"])
@pytest.mark.parametrize("role", ["BUILDING", "MAINTENANCE"])
def test_budget_trial_is_identical_to_full_copies_without_mutating_sources(monkeypatch, kind, role):
    method, capacity, settings = budget_fixture(kind, monkeypatch)
    before_method, before_capacity = deepcopy(method), deepcopy(capacity)
    expected = prescribe(deepcopy(method), deepcopy(capacity), settings, role)
    actual = prescribe(engine._copy_method_budget(method), engine._copy_capacity_budget(capacity), settings, role)
    assert actual == expected
    assert actual["blocks"] or (kind == "nested_combined" and role == "MAINTENANCE")
    assert method == before_method
    assert capacity == before_capacity
    # A second role/Recovery trial must start from the same untouched inputs.
    assert prescribe(engine._copy_method_budget(method), engine._copy_capacity_budget(capacity), settings, role) == expected


@pytest.mark.parametrize("kind", ["complete", "canonical", "estimated", "unsupported",
                                   "declared_rest", "conflicting_break", "empty"])
def test_complete_plan_and_method_sources_match_previous_full_copy_behavior(monkeypatch, kind):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    method_copy, capacity_copy = engine._copy_method_budget, engine._copy_capacity_budget
    registry = engine.method_registry.executable_methods
    emitted = []

    def capture(*args, **kwargs):
        methods = registry(*args, **kwargs)
        emitted.append(methods)
        return methods

    monkeypatch.setattr(engine.method_registry, "executable_methods", capture)
    repo, profile = generator_inputs(kind)
    original_envelope, original_profile = deepcopy(repo.envelope), deepcopy(profile)
    monkeypatch.setattr(engine, "_copy_method_budget", deepcopy)
    monkeypatch.setattr(engine, "_copy_capacity_budget", deepcopy)
    expected = engine.generate_plan(repo, "athlete", profile, start_date=TODAY + timedelta(days=1), now=NOW)
    previous_method_state = deepcopy(emitted.pop())
    monkeypatch.setattr(engine, "_copy_method_budget", method_copy)
    monkeypatch.setattr(engine, "_copy_capacity_budget", capacity_copy)
    actual = engine.generate_plan(repo, "athlete", profile, start_date=TODAY + timedelta(days=1), now=NOW)
    assert actual == expected
    assert json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)
    assert emitted.pop() == previous_method_state
    assert repo.envelope == original_envelope
    assert profile == original_profile


@pytest.mark.parametrize("combine", [False, True])
def test_complete_native_and_combined_weeks_keep_published_data_independent(monkeypatch, combine):
    from tests.api.test_management_schedule import body, high_capacity_history
    from tests.api.test_methodology_completion import parents
    from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness

    fixed_readiness(monkeypatch, 100.)
    profile = body(sessions_per_week=1, sessions_by_day=[0, 1, 0, 0, 0, 0, 0], threshold_days=[1],
                   accent_mode="MANUAL", accents=["Z3", "Z2"], mixed_sessions_enabled=combine)
    profile.update(age_years=30, training_experience_years=5, available_minutes=[360] * 7,
                   component_targets_weekly={"Z1": 400, "Z2": 200, "Z3": 100, "Z4": 0, "Z5": 0, "STR": 0})
    methods = [dict(m, source_id=m["id"], source_version="1") for m in parents()]
    monkeypatch.setattr(engine, "resolved_methods", lambda _: deepcopy(methods))
    monkeypatch.setattr(engine.method_library, "executable_methods", lambda *a, **kw: [])
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    registry, emitted = engine.method_registry.executable_methods, []

    def capture(*args, **kwargs):
        result = registry(*args, **kwargs)
        emitted.append(result)
        return result

    monkeypatch.setattr(engine.method_registry, "executable_methods", capture)
    method_copy, capacity_copy = engine._copy_method_budget, engine._copy_capacity_budget
    monkeypatch.setattr(engine, "_copy_method_budget", deepcopy)
    monkeypatch.setattr(engine, "_copy_capacity_budget", deepcopy)
    expected = engine.generate_plan(high_capacity_history(), "athlete", deepcopy(profile), start_date=TODAY, now=NOW)
    previous_methods = deepcopy(emitted.pop())
    monkeypatch.setattr(engine, "_copy_method_budget", method_copy)
    monkeypatch.setattr(engine, "_copy_capacity_budget", capacity_copy)
    actual = engine.generate_plan(high_capacity_history(), "athlete", deepcopy(profile), start_date=TODAY, now=NOW)
    assert actual == expected
    retained_methods = emitted.pop()
    assert retained_methods == previous_methods
    selected = [s for day in actual["days"] for s in day["sessions"]]
    assert selected and any(s["method_id"].startswith("COMBO-") for s in selected) == combine
    before = deepcopy(retained_methods)

    def mutate(value):
        if isinstance(value, dict):
            for child in list(value.values()):
                mutate(child)
            value["mutated_published_copy"] = True
        elif isinstance(value, list):
            for child in list(value):
                mutate(child)
            value.append("mutated_published_copy")

    for session in selected:
        mutate(session)
    assert retained_methods == before
