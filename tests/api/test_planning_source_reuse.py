"""A shared measured projection must not acquire planning-only assumptions."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from apps.api import management_service as service, training_plan_engine as engine
from apps.api.component_load_projection import ComponentLoadRefreshRequired
from apps.api.management_schemas import ManagementProfile
from apps.api.oauth_store import PersistentStoreFailure
from tests.api.test_load_progression import configured
from tests.api.test_planning_history_estimate import incomplete
from tests.api.test_training_plan_engine import TODAY, NOW, reference_speed, append_complete_activity
from tests.api.test_training_return import fixture as return_fixture


def inputs(kind):
    if kind in {"declared_rest", "conflicting_break"}:
        profile, repo, source = return_fixture()
        if kind == "declared_rest":
            pause, resume = (profile["training_return"][key] for key in ("pause_start", "resume_date"))
            source["daily"] = [row for row in source["daily"] if not pause <= row["date"] < resume]
            source["strength"]["daily"] = [row for row in source["strength"]["daily"]
                                            if not pause <= row["date"] < resume]
        else:
            append_complete_activity(source, {
                "activity_ref": "recorded-during-break", "date": (TODAY-timedelta(days=1)).isoformat(),
                "sport": "Run", "duration_min": 10., "zones": [
                    {"zone": zone, "raw_time_min": 10. if zone == "Z1" else 0.,
                     "equivalent_time_min": 10. if zone == "Z1" else 0.}
                    for zone in engine.COMPONENTS[:5]],
            })
        return repo, profile
    repo, source = incomplete(1 if kind in {"estimated", "unsupported"} else 0)
    if kind == "unsupported":
        source["activities"][0]["hr_coverage_percent"] = None
    return repo, configured(discipline="5000 m")


def prepare(repo, profile):
    speed = reference_speed(repo, "athlete", "Run")
    return engine.planning_source_details(repo, "athlete", profile, repo.settings, repo.envelope, TODAY,
                                          speed_by_sport={"Run": speed})


@pytest.mark.parametrize("kind", ["complete", "estimated", "unsupported", "declared_rest", "conflicting_break"])
def test_measured_result_matches_the_original_projection_before_planning_additions(kind):
    repo, profile = inputs(kind)
    original = deepcopy(repo.envelope)
    result = prepare(repo, profile)
    contexts = result.source["component_load_model"]["contexts_by_sport"]
    expected = engine.project_history(original["snapshot_payload"]["load_history"], contexts)
    assert result.measured_source == expected
    assert repo.envelope == original
    assert "planning_history" not in result.measured_source
    assert "training_return_declaration" not in result.measured_source
    assert not any(row.get("source") == "DECLARED_REST" for row in result.measured_source["daily"])
    assert not any(row.get("planning_estimated_q", 0) for activity in result.measured_source["activities"]
                   for row in activity["zones"])
    if kind == "estimated":
        assert result.evidence["supported"] and result.evidence["estimated"]
        assert any(row.get("planning_estimated_effective_load", 0) > 0 for row in result.source["daily"])
    elif kind == "unsupported":
        assert not result.evidence["supported"]
    elif kind == "declared_rest":
        assert result.source["training_return_declaration"]["status"] == "CONFIRMED"
        assert any(row.get("source") == "DECLARED_REST" for row in result.source["daily"])
    elif kind == "conflicting_break":
        assert result.source["training_return_declaration"]["status"] == "CONFLICT"
        assert any(a["activity_ref"] == "recorded-during-break" for a in result.measured_source["activities"])
    legacy = engine.planning_source(repo, "athlete", profile, repo.settings, repo.envelope, TODAY,
                                    speed_by_sport={"Run": reference_speed(repo, "athlete", "Run")})
    assert type(legacy) is tuple and len(legacy) == 2
    assert legacy == (result.source, result.evidence)
    result.source["daily"][0]["effective_load"] += 100.
    assert result.measured_source == expected
    assert repo.envelope == original


@pytest.mark.parametrize("kind", ["complete", "estimated", "unsupported", "declared_rest", "conflicting_break"])
def test_outlook_keeps_every_field_with_one_projection_instead_of_two(monkeypatch, kind):
    repo, profile = inputs(kind)
    profile = ManagementProfile.model_validate(profile).model_dump(mode="json")
    repo.active_analysis = lambda _: deepcopy(repo.envelope)
    monkeypatch.setattr(service, "ManagementStore", lambda _: SimpleNamespace(
        profile=lambda _: {"configured": True, "revision": 1, "profile": profile}))
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    details = engine.planning_source_details
    project = engine.project_history
    calls = []

    def count_projection(*args):
        calls.append(args)
        return project(*args)

    def repeat_original_projection(repository, alias, body, settings, envelope, today, **kwargs):
        prepared = details(repository, alias, body, settings, envelope, today, **kwargs)
        contexts = prepared.source["component_load_model"]["contexts_by_sport"]
        return replace(prepared, measured_source=engine.project_history(
            envelope["snapshot_payload"]["load_history"], contexts))

    monkeypatch.setattr(engine, "project_history", count_projection)
    monkeypatch.setattr(engine, "planning_source_details", repeat_original_projection)
    expected = service.outlook(repo, "athlete", now=NOW)
    assert len(calls) == 2
    assert calls[0] == calls[1]
    calls.clear()
    monkeypatch.setattr(engine, "planning_source_details", details)
    actual = service.outlook(repo, "athlete", now=NOW)
    assert actual == expected
    assert len(calls) == 1


@pytest.mark.parametrize("damage", ["missing_activity", "wrong_daily_load"])
def test_invalid_measured_ledger_still_fails_before_estimation(monkeypatch, damage):
    repo, profile = inputs("complete")
    source = repo.envelope["snapshot_payload"]["load_history"]
    if damage == "missing_activity":
        source["activities"].pop()
    else:
        source["daily"][0]["effective_load"] += 1.
    original = deepcopy(repo.envelope)
    monkeypatch.setattr(engine.planning_history_estimate, "prepare", lambda *args, **kwargs: pytest.fail(
        "Invalid measured history must not reach planning estimates"))
    with pytest.raises(ComponentLoadRefreshRequired):
        prepare(repo, profile)
    assert repo.envelope == original


def test_component_context_storage_failure_does_not_fall_back_to_an_unprojected_source(monkeypatch):
    repo, profile = inputs("complete")

    def unavailable(*args, **kwargs):
        raise PersistentStoreFailure("Context inputs unavailable")

    monkeypatch.setattr(engine, "read_contexts", unavailable)
    with pytest.raises(PersistentStoreFailure, match="Context inputs unavailable"):
        prepare(repo, profile)


def test_absent_measured_history_remains_empty_without_inventing_a_projection(monkeypatch):
    repo, profile = inputs("complete")
    repo.envelope["snapshot_payload"]["load_history"] = {}
    monkeypatch.setattr(engine, "project_history", lambda *args: pytest.fail(
        "An absent measured ledger must retain the existing empty-source path"))
    result = prepare(repo, profile)
    assert result.measured_source == {}
