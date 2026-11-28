"""Search capabilities follow all executable methods, not the chosen plan."""
from copy import deepcopy

import pytest

from apps.api import training_plan_engine as engine
from tests.api.test_load_progression import configured, observed
from tests.api.test_training_plan_engine import TODAY, NOW


MIX = "END-THR-LONG-01-MIX-STEADY"


def profile():
    body = configured(age_years=25, training_experience_years=5, available_minutes=[180]*7)
    body["planning_controls"].update(sessions_per_week=1, sessions_by_day=[1, 0, 0, 0, 0, 0, 0],
        mixed_sessions_enabled=True, threshold_method="AUTO")
    return body


def generate(repository, body, fraction):
    return engine.generate_plan(repository, "athlete", {
        **body, "_allocation_policy": {"optional_easy_fraction": fraction}}, start_date=TODAY, now=NOW)


def test_disabled_mixed_catalog_makes_optional_finish_a_proven_noop():
    body = profile()
    assert any(method.get("mixed_component") for method in engine.resolved_methods(body))
    repository, _, _ = observed()
    zero, full = (generate(repository, body, fraction) for fraction in (0., 1.))
    assert zero["parameters"]["allocation_policy_capabilities"]["optional_easy_fraction"] is False
    for key in ("days", "lookahead_days", "allocation", "long_term"):
        assert zero[key] == full[key]


@pytest.mark.parametrize("mixed_enabled", [True, False])
def test_enabled_unselected_mixed_recipe_preserves_search_capability(mixed_enabled):
    body = profile()
    body["available_minutes"] = [0]*7
    body["planning_controls"]["mixed_sessions_enabled"] = mixed_enabled
    body["method_library"] = {"overrides": {MIX: {"enabled": True}}}
    repository, _, _ = observed()
    plan = generate(repository, body, 0.)
    assert all(not day["sessions"] for day in [*plan["days"], *plan["lookahead_days"]])
    assert plan["parameters"]["allocation_policy_capabilities"]["optional_easy_fraction"] is mixed_enabled


def test_explicit_mixed_activation_keeps_distinct_effective_finish_choices(monkeypatch):
    body = profile()
    body["method_library"] = {"overrides": {MIX: {"enabled": True}}}
    methods = [method for method in engine.resolved_methods(body) if method["id"] == MIX]
    monkeypatch.setattr(engine, "resolved_methods", lambda _: deepcopy(methods))
    repository, _, _ = observed()
    zero, full = (generate(repository, body, fraction) for fraction in (0., 1.))
    assert zero["parameters"]["allocation_policy_capabilities"]["optional_easy_fraction"] is True
    left, right = zero["days"][0]["sessions"][0], full["days"][0]["sessions"][0]
    assert left["method_id"] == right["method_id"] == MIX
    primary = lambda session: [block for block in session["blocks"]
        if block["kind"] == "WORK" and block["zone"] == session["zone"]]
    assert primary(left) == primary(right)
    assert left["direct_equivalent_minutes"]["Z1"] < right["direct_equivalent_minutes"]["Z1"]
