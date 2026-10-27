"""Preserve method meaning and local component coverage through selection."""
from copy import deepcopy

import pytest

from apps.api import training_plan_engine as engine
from apps.api.management_schemas import PlanningControls
from apps.api.planning_search import improve, score
from biathlon import method_anchors, method_dosing, method_library, method_registry, planning_allocation, strength_methods
from biathlon.constants import COMPONENTS
from tests.api.test_management_schedule import body, run
from tests.api.test_method_library_integration_review import SETTINGS, athlete
from tests.api.test_training_plan_engine import Repository, TODAY
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness
from tests.test_dosing_curve import example


def allocation(values, *, actual=0.):
    return {"components": {z: {"target": 100., "actual": actual, "planned": v,
                               "remaining": max(0., 100-actual-v)} for z, v in values.items()}}


def plan(values):
    return {"allocation": allocation(values), "days": []}


@pytest.mark.parametrize("value, expected", [(89.9, False), (90., True), (100., True), (110., True), (110.1, False)])
def test_component_tolerance_preserves_exact_central_target(value, expected):
    row = allocation({"Z3": value})["components"]["Z3"]
    result = planning_allocation.assess_component(row)
    assert result["within_tolerance"] is expected
    assert result["coverage_percent"] == pytest.approx(value)
    assert row["target"] == 100.
    assert result["lower_acceptable"] == pytest.approx(90.)
    assert result["upper_acceptable"] == pytest.approx(110.)


def test_unknown_actual_is_not_zero_and_overshoot_cannot_fill_another_cycle():
    unknown = {"target": 100., "actual": None, "planned": 100.}
    assert planning_allocation.assess_component(unknown)["coverage_status"] == "UNKNOWN"
    first, second = allocation({"Z3": 120.}), allocation({"Z3": 80.})
    combined = allocation({"Z3": 100.})
    combined["segments"] = [first, second]
    assert not planning_allocation.acceptance(combined)["within_tolerance"]
    assert not planning_allocation.acceptance(allocation({"Z3": 120., "Z4": 80.}))["within_tolerance"]


def test_balanced_components_outrank_accent_perfection_with_a_large_gap():
    previous = plan({"Z1": 100., "Z2": 100., "Z3": 20., "Z4": 94., "Z5": 63., "STR": 62.})
    improved = plan({"Z1": 97., "Z2": 95., "Z3": 87., "Z4": 99., "Z5": 51., "STR": 51.})
    assert score(improved, {"Z2", "Z4", "Z5"}) > score(previous, {"Z2", "Z4", "Z5"})
    assert score(plan({z: 100. for z in COMPONENTS}), {"Z4"}) > score(plan({z: 90. for z in COMPONENTS}), {"Z4"})
    assert score(plan({"Z3": 100.}), {"Z3"}) > score(plan({"Z3": 110.}), {"Z3"})
    assert score(plan({"Z3": 100.}), {"Z3"}) > score(plan({"Z3": 200.}), {"Z3"})


def test_slow_baseline_still_checks_a_useful_alternative_within_the_budget():
    baseline = plan({z: 50. for z in COMPONENTS})
    calls = []
    def replay(policy):
        calls.append(policy)
        return plan({z: 100. for z in COMPONENTS})
    result = improve(baseline, {}, replay, seconds_remaining=90., estimated_trial_seconds=60.)
    assert calls
    assert calls[0] == {"optional_easy_fraction": 0., "balance_complete_doses": False, "component_gap_first": True}
    assert result["allocation"]["search"]["improved"]
    assert result["allocation"]["search"]["hard_limit_proven"] is False


def test_accepted_95_percent_can_still_improve_toward_100():
    baseline = plan({z: 95. for z in COMPONENTS})
    result = improve(baseline, {}, lambda _: plan({z: 100. for z in COMPONENTS}))
    assert all(r["planned"] == 100. for r in result["allocation"]["components"].values())


@pytest.mark.parametrize("wrapper_zone", ["Z2", "Z3", "Z4"])
def test_continuous_threshold_parent_cannot_hide_inside_a_combination(wrapper_zone):
    continuous = {"zone": "Z3", "structure": "CONTINUOUS"}
    combined = {"zone": wrapper_zone, "combined_methods": [
        {"zone": "Z2", "structure": "CONTINUOUS"}, continuous]}
    assert not method_dosing.threshold_allowed(combined, "INTERVALS")
    assert method_dosing.threshold_allowed(combined, "CONTINUOUS")
    assert method_dosing.threshold_allowed(combined, "AUTO")


@pytest.mark.parametrize("method", [
    {"zone": "Z3", "structure": "AEROBIC_SUPPORT", "mixed_component": True},
    {"zone": "Z3", "structure": "MIXED_AEROBIC", "mixed_variant": "STEADY"},
])
def test_mixed_continuous_threshold_obeys_the_same_preference(method):
    assert not method_dosing.threshold_allowed(method, "INTERVALS")
    assert method_dosing.threshold_allowed(method, "CONTINUOUS")


def test_source_continuous_threshold_in_a_multi_zone_recipe_is_checked():
    method = {"zone": "Z4", "structure": "LIBRARY_CONTINUOUS", "method_library": {"resolution": {
        "structure": "PROGRESSIVE_CONTINUOUS", "segments": [{"kind": "WORK", "zone": "Z3"}, {"kind": "WORK", "zone": "Z4"}]}}}
    assert not method_dosing.threshold_allowed(method, "INTERVALS")


def test_every_numeric_source_recipe_declares_a_supported_anchor():
    supported = {"CV", "CS", "AnT", "ANT", "MAS", "V5", "V6", "V_DURATION", "RACE_DURATION", "RACE_DISTANCE", "PRIMARY_RACE", "HRMAX"}
    for method in method_library.entries():
        recipe = method.get("prescription")
        if recipe:
            assert recipe.get("anchor", {}).get("type") in supported, method["id"]
            for part in recipe.get("pattern", []):
                assert part.get("anchor", recipe["anchor"])["type"] in supported, method["id"]


def test_all_resolved_native_capacities_name_their_actual_anchor():
    profile = body()
    profile.update(strength_enabled=True)
    view = example()
    context = engine._capacity_context(view, SETTINGS)
    resolved = []
    for method in engine.resolved_methods(profile):
        cap = engine.capacity_for(method, SETTINGS, view, context, TODAY, use_model_prior=True)
        if cap:
            assert cap["effort_anchor"]["type"] in {"HR_ZONE_POSITION", "V_DURATION", "INDIVIDUAL_SPEED", "STRENGTH_PROFILE", "COACH_EFFORT"}
            resolved.append(cap)
    assert len(resolved) > 10
    assert any(c["effort_anchor"]["type"] == "STRENGTH_PROFILE" for c in resolved)
    assert any(c["effort_anchor"]["type"] == "HR_ZONE_POSITION" for c in resolved)


def test_source_anchor_api_is_the_shared_resolver_and_keeps_literal_v5():
    assert method_library._anchor is method_anchors.resolve_speed
    curve, tests, _ = method_library.selected_curve(example())
    for kind, seconds in [("MAS", 360.), ("V6", 360.), ("V5", 300.)]:
        result = method_anchors.resolve_speed({"type": kind}, athlete(), example(), curve, tests)
        assert result["speed_kmh"] == pytest.approx(curve.speed(seconds)*3.6)
    with pytest.raises(ValueError, match="UNSUPPORTED_OR_NON_SPEED_ANCHOR"):
        method_anchors.resolve_speed({"type": "FTP"}, athlete(), example(), curve, tests)


def test_native_catalogue_names_each_effort_before_weekly_capacity_resolution():
    profile = body()
    profile.update(strength_enabled=True)
    cards = method_registry.catalog(profile, example(), phase="SPECIAL_PREPARATION", today=TODAY)["methods"]
    native = [card for card in cards if card.get("registry_kind") == "COACH_PROFILE"]
    assert len(native) > 10
    assert all(card["resolution"]["anchor"]["type"] for card in native)
    threshold = next(c for c in native if c["id"] == "END-THR-LONG-01")
    assert threshold["resolution"]["anchor"]["type"] == "HR_ZONE_POSITION"
    assert threshold["resolution"]["anchor"]["zone"] == "Z3"


def test_unrepresentable_effort_anchor_is_unavailable_without_breaking_catalogue_json(monkeypatch):
    import json
    profile = body()
    method = next(m for m in engine.resolved_methods(profile) if m["structure"] == "MODEL_INTERVALS")
    method["interval_template"]["speed_time_duration_ratio"] = 1e308
    monkeypatch.setattr(method_registry.training_methods, "resolved_methods", lambda _: [method])
    cards = method_registry.catalog(profile, example(), phase="SPECIAL_PREPARATION", today=TODAY)["methods"]
    card = next(c for c in cards if c["id"] == method["id"])
    assert card["resolution"]["status"] == "UNAVAILABLE"
    assert card["resolution"]["anchor"]["type"] == "UNRESOLVED"
    json.dumps(cards, allow_nan=False)


def test_personal_api_preview_retains_the_resolved_anchor_and_profile(monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from apps.api import method_library_service as service
    profile = body()
    before = deepcopy(profile)
    monkeypatch.setattr(service, "ManagementStore", lambda _: SimpleNamespace(
        profile=lambda _: {"configured": True, "profile": deepcopy(profile), "revision": 1}))
    monkeypatch.setattr(service.model_service, "speed_view", lambda *args: deepcopy(example()))
    repository = SimpleNamespace(athlete_settings=lambda _: SETTINGS)
    result = service.view(repository, "athlete", phase="SPECIAL_PREPARATION",
        now=datetime.combine(TODAY, datetime.min.time(), tzinfo=timezone.utc))
    card = next(c for c in result["methods"] if c["id"] == "END-THR-LONG-01")
    resolved = card["resolution"]
    method = next(m for m in engine.resolved_methods(profile) if m["id"] == card["id"])
    expected_hr = SETTINGS.zone_bounds_bpm[2]+method["position"]*(SETTINGS.zone_bounds_bpm[3]-SETTINGS.zone_bounds_bpm[2])
    assert resolved["status"] == "READY"
    assert resolved["anchor"]["type"] == "HR_ZONE_POSITION"
    assert resolved["anchor"]["zone"] == "Z3"
    assert resolved["anchor"]["target_hr_bpm"] == pytest.approx(expected_hr, abs=.001)
    assert resolved["anchor"]["speed_kmh"] == resolved["target_speed_kmh"]
    assert profile == before


def test_q_ledger_uses_the_same_arithmetic_and_preserves_passive_and_strength_rules():
    settings = Repository().settings
    threshold_hr = sum(settings.zone_bounds_bpm[2:4])/2
    blocks = [
        {"kind": "WARMUP", "zone": "Z1", "duration_min": 7.345, "target_hr_bpm": 120., "target_speed_kmh": None},
        {"kind": "WORK", "zone": "Z3", "duration_min": 12.345, "target_hr_bpm": threshold_hr, "target_speed_kmh": None},
        {"kind": "RECOVERY", "zone": "Z1", "duration_min": 3., "target_hr_bpm": None, "passive_rest": True},
        {"kind": "WORK", "zone": "STR", "duration_min": 2.},
        {"kind": "RECOVERY", "zone": "STR", "duration_min": 3., "passive_rest": True},
        {"kind": "WARMUP", "zone": "STR", "duration_min": 10.},
        {"kind": "WORK", "zone": "NMS", "duration_min": .1},
    ]
    original = deepcopy(blocks)
    direct, ledger = engine._direct_q(blocks, Repository().settings, details=True)
    expected, _, _ = engine._canonical_load(blocks, Repository().settings, [], TODAY)
    assert direct == expected
    assert direct["STR"] == 5.
    for z in COMPONENTS:
        assert sum(r["equivalent_min"] for r in ledger if r["zone"] == z) == pytest.approx(direct[z], abs=1e-10)
    assert all(r["equivalent_min"] == r["duration_min"]*r["coefficient"] for r in ledger)
    assert len(ledger) == 4
    assert blocks == original


def test_selected_plan_freezes_q_and_anchor_without_changing_measured_history(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    repo = Repository()
    before = deepcopy(repo.envelope)
    profile = body()
    plan = run(monkeypatch, profile, repo)
    sessions = [s for d in [*plan["days"], *plan.get("lookahead_days", [])] for s in d["sessions"] if not d.get("locked")]
    assert sessions
    for session in sessions:
        assert session["dose_evidence"]["effort_anchor"]["type"]
        ledger = session["q_accounting"]["rows"]
        assert session["q_accounting"]["status"] == "COMPLETE"
        for z in COMPONENTS:
            assert round(sum(r["equivalent_min"] for r in ledger if r["zone"] == z), 3) == session["direct_equivalent_minutes"][z]
    assert plan["allocation"]["acceptance"]["scope"] == "EACH_COMPONENT_IN_EACH_MICROCYCLE"
    assert plan["allocation"]["acceptance"]["changes_load_limits"] is False
    assert repo.envelope == before


def test_unmapped_prescription_is_disclosed_as_unknown_not_as_zero_load():
    settings = Repository().settings
    blocks = [{"kind": "WORK", "zone": "Z3", "duration_min": 10.,
               "target_hr_bpm": settings.zone_bounds_bpm[1], "target_speed_kmh": None}]
    result = engine._q_accounting(blocks, settings)
    assert result["status"] == "INCOMPLETE"
    assert result["unavailable_minutes"] == 10.
    assert result["rows"] == []


@pytest.mark.parametrize("limit", [None, 0, 2, 3, 4])
def test_strength_quota_is_explicit_or_automatic_without_a_hidden_support_cap(limit):
    controls = PlanningControls(max_strength_sessions=limit).model_dump(mode="json")
    assert strength_methods.session_limit(controls, accent=False) == (4 if limit is None else limit)
    assert strength_methods.session_limit(controls, accent=True) == (4 if limit is None else limit)
    assert strength_methods.session_limit(None) == 2
