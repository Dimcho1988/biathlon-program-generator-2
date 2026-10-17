from copy import deepcopy

import pytest

from apps.api import learning_service, management_service, training_plan_engine as engine
from apps.api.component_load_context import context_from_speed_view
from apps.api.component_load_projection import project_history
from apps.api.management_store import ManagementStore
from tests.api.test_load_progression import configured, observed
from tests.api.test_training_plan_engine import TODAY, NOW, reference_speed


def projected_fixture():
    repo, source, _ = observed()
    projected = project_history(source, {"Run": context_from_speed_view({"sport": "Run"})})
    envelope = {**repo.envelope, "snapshot_payload": {**repo.envelope["snapshot_payload"], "load_history": projected}}
    return repo, projected, engine._daily_rows(projected, TODAY), envelope


def test_learning_context_changes_with_capacity_or_model_but_not_history_generation():
    _, source, _, _ = projected_fixture()
    first = learning_service.component_load_context_key(source)
    newer = deepcopy(source)
    newer["component_load_model"]["fingerprint"] = "new-activity-history"
    newer["component_load_model"]["contexts_by_sport"]["Run"].update(source_revision=99, source_generation_id="new")
    assert learning_service.component_load_context_key(newer) == first
    newer["component_load_model"]["contexts_by_sport"]["NordicSki"] = context_from_speed_view({"sport": "NordicSki"})
    assert learning_service.component_load_context_key(newer) == first  # An unused future training sport changes no observed E.
    newer["component_load_model"]["contexts_by_sport"]["Run"]["minutes"]["Z3"] *= 1.1
    assert learning_service.component_load_context_key(newer) != first
    newer["component_load_model"]["version"] = "old-cascade"
    assert learning_service.component_load_context_key(newer) is None


def test_old_frozen_effective_windows_are_ignored_without_modifying_athlete_reports():
    _, source, _, _ = projected_fixture()
    key = learning_service.component_load_context_key(source)
    entries = [{"kind": "TEST", "entry_key": "outcome", "payload": {
        "value": 90., "observed_load_windows": [{"block": "old", "current": {"Z1": 999.}}],
        "load_source": {"generation_id": "old"}}}]
    original = deepcopy(entries)
    filtered = learning_service.compatible_load_observations(entries, source)
    assert filtered[0]["payload"]["observed_load_windows"] == []
    assert filtered[0]["payload"]["value"] == 90.
    assert entries == original
    entries[0]["payload"]["load_source"]["component_load_context_key"] = key
    assert learning_service.compatible_load_observations(entries, source) == entries


def test_actual_learner_accepts_projected_pinned_source_and_resets_old_memory(monkeypatch):
    repo, source, rows, envelope = projected_fixture()
    monkeypatch.setattr(learning_service, "history_from_calendar", lambda *args: [])
    monkeypatch.setattr(ManagementStore, "learning_memory", lambda *args: {
        "version": learning_service.VERSION, "context_key": "pre-adjacent-rule", "episodes": [{"id": "obsolete"}]})
    seen = []
    original = learning_service.learning_evidence.build_evidence
    def inspect(**kwargs):
        seen.append(kwargs["retained"])
        return original(**kwargs)
    monkeypatch.setattr(learning_service.learning_evidence, "build_evidence", inspect)
    report = learning_service.context(repo, "athlete", configured(), source, rows, TODAY, envelope=envelope)
    assert seen == [{}]
    assert report["source"]["component_load_context_key"] == learning_service.component_load_context_key(source)


def test_generator_pins_projected_history_for_learning_without_rewriting_repository(monkeypatch):
    repo, original, _ = observed()
    unchanged = deepcopy(repo.envelope)
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    seen = []
    def inspect(repository, alias, profile, source, rows, today, **kwargs):
        assert source == kwargs["envelope"]["snapshot_payload"]["load_history"]
        assert source["component_load_model"]["scope"] == "ACTIVITY"
        seen.append(source)
        return None
    monkeypatch.setattr(learning_service, "context", inspect)
    repo.events = []
    profile = configured(program_end=TODAY.isoformat(), horizon_mode="MANUAL")
    engine.generate_plan(repo, "athlete", profile, start_date=TODAY, now=NOW)
    assert len(seen) == 1
    assert repo.envelope == unchanged
    assert "component_load_model" not in original


def test_outlook_passes_the_same_projected_activity_envelope_to_learning(monkeypatch):
    repo, _, _ = observed()
    repo.active_analysis = lambda alias: deepcopy(repo.envelope)
    profile = configured(discipline="5000 m")
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    monkeypatch.setattr(ManagementStore, "profile", lambda *args: {"configured": True, "profile": profile, "revision": 1})
    seen = []
    def inspect(repository, alias, body, source, rows, today, **kwargs):
        envelope = kwargs["envelope"]
        assert source == envelope["snapshot_payload"]["load_history"]
        assert envelope["activities"] == repo.envelope["activities"]
        seen.append(source)
        return None
    monkeypatch.setattr(learning_service, "context", inspect)
    management_service.outlook(repo, "athlete", now=NOW)
    assert len(seen) == 1


@pytest.mark.parametrize("projection_failure", [None, "MISSING_Q", "CURVE_REFERENCE"])
def test_test_report_is_saved_but_old_unversioned_load_windows_are_not_reused(monkeypatch, projection_failure):
    from tests.api.test_response_monitoring import Repository, ACTOR
    from tests.api.test_load_progression import response_fixture
    from apps.api import response_service, load_adaptation
    from apps.api.response_monitoring import OptionalTest
    from apps.api import component_load_projection
    from apps.api.component_load_context import ComponentCapacityUnavailable
    if projection_failure is not None:
        failure = (ComponentCapacityUnavailable("Z3") if projection_failure == "CURVE_REFERENCE" else
                   component_load_projection.ComponentLoadRefreshRequired("Activity Q unavailable"))
        def unavailable(*args, **kwargs):
            raise failure
        monkeypatch.setattr(component_load_projection, "project_snapshot", unavailable)
    entries, rows = response_fixture()
    body = OptionalTest(**entries[-1]["payload"], expected_revision=1)
    entries[-1]["payload"].update(observed_load_windows=load_adaptation.load_observations(entries, rows, TODAY),
                                   load_source={"generation_id": "old-cascade", "revision": 1})
    repo = Repository(entries)
    response_service.save_report(repo, "ath-test", "TEST", body, ACTOR, now=NOW)
    saved = repo.saved["p_payload"]
    assert saved["value"] == body.value
    assert saved["observed_load_windows"] == []
    assert saved["load_observation_status"] == "UNAVAILABLE"
    assert saved["load_source"]["component_load_context_key"] is None
