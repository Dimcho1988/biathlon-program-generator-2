"""Reusing measured history must preserve the complete generated plan."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json

import pytest

from apps.api import training_plan_engine as engine
from apps.api.component_load_projection import ComponentLoadRefreshRequired
from tests.api.test_planning_source_reuse import inputs, prepare
from tests.api.test_training_plan_engine import NOW, TODAY, reference_speed


EMPTY_KINDS = {"empty", "missing", "null"}


def generator_inputs(kind):
    repo, profile = inputs("complete" if kind in {"canonical", *EMPTY_KINDS} else kind)
    if kind == "canonical":
        # A persisted projection with the current fingerprint returns its own
        # object, so this also exercises the no-copy branch in project_history.
        repo.envelope["snapshot_payload"]["load_history"] = deepcopy(prepare(repo, profile).measured_source)
    elif kind in EMPTY_KINDS:
        if kind == "missing":
            repo.envelope["snapshot_payload"].pop("load_history")
        else:
            repo.envelope["snapshot_payload"]["load_history"] = None if kind == "null" else {}
        repo.envelope["activities"] = []
    return repo, profile


def generate(repo, profile):
    return engine.generate_plan(repo, "athlete", profile,
                                start_date=TODAY + timedelta(days=1), now=NOW)


@pytest.mark.parametrize("kind", [
    "complete", "canonical", "estimated", "unsupported",
    "declared_rest", "conflicting_break", "empty", "missing", "null",
])
def test_entire_generator_result_matches_the_previous_second_projection(monkeypatch, kind):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo, profile = generator_inputs(kind)
    original_envelope, original_profile = deepcopy(repo.envelope), deepcopy(profile)
    details, project = engine.planning_source_details, engine.project_history
    calls, prepared_results = [], []

    def count_projection(source, contexts):
        result = project(source, contexts)
        calls.append((deepcopy(source), deepcopy(contexts), result is source))
        return result

    def original_second_projection(repository, alias, body, settings, envelope, today, **kwargs):
        prepared = details(repository, alias, body, settings, envelope, today, **kwargs)
        contexts = prepared.source["component_load_model"]["contexts_by_sport"]
        # This is the former generator's second call, on the original measured
        # input, rather than the planning ledger containing estimates/rest.
        return replace(prepared, measured_source=engine.project_history(
            envelope["snapshot_payload"].get("load_history") or {}, contexts))

    def capture_details(*args, **kwargs):
        prepared = details(*args, **kwargs)
        prepared_results.append((prepared, deepcopy(prepared.measured_source)))
        return prepared

    monkeypatch.setattr(engine, "project_history", count_projection)
    monkeypatch.setattr(engine, "planning_source_details", original_second_projection)
    expected = generate(repo, profile)
    assert len(calls) == (1 if kind in EMPTY_KINDS else 2)
    if kind not in EMPTY_KINDS:
        assert calls[0][:2] == calls[1][:2]
    assert repo.envelope == original_envelope
    assert profile == original_profile

    calls.clear()
    monkeypatch.setattr(engine, "planning_source_details", capture_details)
    actual = generate(repo, profile)
    # Includes every workout, role/range/dose, allocation shortfall, warning,
    # long-term goal, measured history, provenance and both fingerprints.
    assert actual == expected
    assert json.dumps(actual, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) == json.dumps(
        expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    assert len(calls) == (0 if kind in EMPTY_KINDS else 1)
    assert repo.envelope == original_envelope
    assert profile == original_profile
    assert len(prepared_results) == 1
    prepared, measured_before_generation = prepared_results[0]
    assert prepared.measured_source == measured_before_generation
    assert "planning_history" not in prepared.measured_source
    assert "training_return_declaration" not in prepared.measured_source
    assert not any(row.get("source") == "DECLARED_REST"
                   for row in prepared.measured_source.get("daily", []))
    assert not any(row.get("planning_estimated_q", 0)
                   for activity in prepared.measured_source.get("activities", [])
                   for row in activity["zones"])
    if kind == "canonical":
        assert calls[0][2] is True
    if kind in {"complete", "canonical", "estimated", "declared_rest"}:
        assert actual["summary"]["sessions"] > 0
    if kind == "estimated":
        assert actual["source"]["planning_history"]["estimated"]
        assert actual["source"]["readiness_basis"] == "ESTIMATED_LOAD"
    elif kind == "unsupported":
        assert not actual["activation_eligible"]
        assert actual["source"]["readiness_basis"] == "UNKNOWN"
    elif kind == "declared_rest":
        assert any(row.get("source") == "DECLARED_REST" for row in prepared.source["daily"])
    elif kind == "conflicting_break":
        assert any(warning["code"] == "TRAINING_RETURN_CONFLICT" for warning in actual["warnings"])
    elif kind in EMPTY_KINDS:
        assert prepared.measured_source == {}
        assert not actual["activation_eligible"]


@pytest.mark.parametrize("kind", ["canonical", "estimated", "declared_rest"])
def test_later_planning_edits_do_not_alias_reused_measurements(monkeypatch, kind):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo, profile = generator_inputs(kind)
    original = deepcopy(repo.envelope)
    details, captured = engine.planning_source_details, []

    def capture_details(*args, **kwargs):
        prepared = details(*args, **kwargs)
        captured.append(prepared)
        return prepared

    monkeypatch.setattr(engine, "planning_source_details", capture_details)
    plan = generate(repo, profile)
    prepared, = captured
    measured = deepcopy(prepared.measured_source)
    plan_before = deepcopy(plan)
    prepared.source["daily"][0]["effective_load"] += 1000.
    prepared.source["activities"][0]["zones"][0]["equivalent_time_min"] += 1000.
    prepared.source["component_load_model"]["contexts_by_sport"]["Run"]["minutes"]["Z1"] += 1000.
    assert prepared.measured_source == measured
    assert repo.envelope == original
    # Measured retrospective evidence must not become a view of the mutable
    # planning ledger once the generator has returned its complete plan.
    assert plan["component_history"] == plan_before["component_history"]
    assert plan["source"]["snapshot_fingerprint"] == plan_before["source"]["snapshot_fingerprint"]


@pytest.mark.parametrize("damage", ["missing_activity", "wrong_daily_load"])
def test_generator_still_rejects_invalid_measured_history_before_planning(monkeypatch, damage):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo, profile = generator_inputs("complete")
    history = repo.envelope["snapshot_payload"]["load_history"]
    if damage == "missing_activity":
        history["activities"].pop()
    else:
        history["daily"][0]["effective_load"] += 1.
    original = deepcopy(repo.envelope)
    monkeypatch.setattr(engine.planning_history_estimate, "prepare", lambda *args, **kwargs: pytest.fail(
        "An invalid measured ledger must fail before planning estimates or dose generation"))
    with pytest.raises(ComponentLoadRefreshRequired):
        generate(repo, profile)
    assert repo.envelope == original
