"""A residual component budget cannot silently choose a different recipe."""
from copy import deepcopy

import pytest

from biathlon import adaptive_methods, method_dosing, method_registry, training_methods
from tests.api.test_management_schedule import body


@pytest.mark.parametrize("identifier", [
    "ONFLOWS-CONTROLLED-Z5-V2-SPLIT-V1",
    "ONFLOWS-CONTROLLED-Z5-V2-MIX-REPETITIONS-SHORT",
    "END-CROSS-TRAIN-01-Z2-MIX-STEADY",
])
def test_shortened_structure_requires_its_own_explicit_selection(identifier):
    profile = body()
    method = next(m for m in training_methods.resolved_methods(profile) if m["id"] == identifier)
    original = deepcopy(method)
    assert not method_registry.enabled(profile, method)
    # Enabling a parent is not permission to invent a different interval shape.
    parent = method.get("variation_parent_id", "ONFLOWS-CONTROLLED-Z5-V2")
    profile["method_library"] = {"overrides": {parent: {"enabled": True}}}
    assert not method_registry.enabled(profile, method)
    profile["method_library"]["overrides"][identifier] = {"enabled": True}
    assert method_registry.enabled(profile, method)
    assert method == original


def test_introductory_profiles_do_not_fall_back_to_full_adult_intervals():
    profile = {**body(), "age_years": 16}
    methods = training_methods.resolved_methods(profile)
    intervals = [m for m in methods if m["structure"] in {"MODEL_INTERVALS", "METABOLIC_INTERVALS"}]
    assert intervals
    assert all(m.get("developmental_variant") for m in intervals)
    assert not any(method_registry.enabled(profile, m) for m in intervals)
    chosen = next(m for m in intervals if m["id"] == "ONFLOWS-CONTROLLED-Z5-V2-SHORT")
    profile["method_library"] = {"overrides": {chosen["id"]: {"enabled": True}}}
    assert method_registry.enabled(profile, chosen)


@pytest.mark.parametrize("zone", ["Z4", "Z5"])
def test_adult_coach_can_explicitly_enable_the_required_introductory_card(zone):
    profile = body()
    parent_id = f"ONFLOWS-CONTROLLED-{zone}-V2"
    short_id = parent_id + "-SHORT"

    def card():
        cards = {method["id"]: method for method in method_registry.catalog(profile)["methods"]}
        assert parent_id in cards
        return cards[short_id]

    assert card()["resolution"]["status"] == "DISABLED"
    assert card()["resolution"]["reason"] == "VARIANT_REQUIRES_COACH_ACTIVATION"
    profile["method_library"] = {"overrides": {parent_id: {"enabled": True}}}
    assert not card()["resolution"]["automatic_selection_enabled"]
    profile["method_library"]["overrides"][short_id] = {"enabled": True}
    assert card()["resolution"]["automatic_selection_enabled"]
    profile["method_library"]["overrides"][parent_id] = {"enabled": False}
    assert not card()["resolution"]["automatic_selection_enabled"]


@pytest.mark.parametrize("age", [16, 30])
def test_introductory_catalog_cards_have_unique_ids_with_mixed_variants(age):
    profile = {**body(), "age_years": age}
    profile["planning_controls"]["mixed_sessions_enabled"] = True
    cards = method_registry.catalog(profile)["methods"]
    identifiers = [method["id"] for method in cards]
    assert len(identifiers) == len(set(identifiers))
    assert "ONFLOWS-CONTROLLED-Z5-V2-SHORT" in identifiers


def test_coach_card_preserves_base_structure_and_honest_attribution():
    method = next(m for m in training_methods.resolved_methods(body())
                  if m["id"] == "ONFLOWS-CONTROLLED-Z5-V2")
    card = method_registry.coach_card(method)
    assert card["source"]["authors"] == "onFlows"
    assert card["original"]["work"] == "30 сек"
    assert card["original"]["repetitions"] == "6–20"
    assert card["original"]["recovery"] == "30 сек"
    assert not card["source"].get("links")
    assert method_registry.enabled(body(), method)


def test_explicit_short_variant_does_not_inherit_a_prepared_parent_floor():
    parent = next(m for m in training_methods.resolved_methods(body())
                  if m["id"] == "ONFLOWS-CONTROLLED-Z5-V2")
    evidence = {"capacity_minutes": 6., "resolved_role": "MAINTENANCE", "readiness_dose_factor": 1.}
    method_dosing.prepare_native_budget(parent, evidence, body())
    child = adaptive_methods.short_variant(parent)
    method_dosing.prepare_native_budget(child, evidence, body())
    method_dosing.apply_structure_floor(child, evidence)
    assert child["interval_template"]["min_repetitions"] == 4
    assert child["authored_interval_bounds"]["min_repetitions"] == 4
    assert child["authored_interval_bounds"]["work_seconds"] == 15
