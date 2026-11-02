from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api import dependencies, main, model_service, prepared_models as prepared
from apps.api import management_lifecycle, management_plan_store, management_store, planning_jobs
from apps.api.management_plan_store import PlanStore
from apps.api.oauth_store import PersistentStoreFailure
from tests.api.test_sync_api import _active_snapshot, AUTH


ALIAS = "ath-sync-test"


def envelope():
    return {"generation_id": "generation-8", "revision": 8, "analysis_as_of": "2026-08-09",
        "activated_at": "2026-08-10T06:00:00+00:00", "snapshot_payload": _active_snapshot()}


@pytest.fixture
def repository(monkeypatch):
    saved = {}
    def operation(op, **kw):
        if op == "READ":
            return {"ready": True, "envelope": deepcopy(saved["value"])} if saved else {
                "ready": False, "revision": 3, "day": "2026-08-09"}
        if op == "STORE":
            saved["value"] = deepcopy(kw["envelope"])
            return {"stored": True}
        if op == "CLAIM":
            return {"athlete_alias": ALIAS, "revision": 3, "day": "2026-08-09"}
        return {"state": "RUNNING"}
    project = Mock(side_effect=lambda repo, alias, snapshot: deepcopy(snapshot))
    monkeypatch.setattr(model_service, "project_recovery", project)
    repo = SimpleNamespace(dashboard_preparation=Mock(side_effect=operation), active_analysis=Mock(side_effect=lambda _: envelope()),
        cleanup_prepared_data=Mock(), saved=saved, project=project)
    return repo


def test_prepared_read_survives_a_new_reader_without_recalculating_or_rereading_source(repository):
    first = prepared.dashboard_envelope(repository, ALIAS)
    second = prepared.dashboard_envelope(repository, ALIAS)
    assert first == second and repository.project.call_count == repository.active_analysis.call_count == 1
    second["snapshot_payload"]["load_history"]["zones"][0]["tref_min"] = 999
    assert prepared.dashboard_envelope(repository, ALIAS) == first
    operations = [call.args[0] for call in repository.dashboard_preparation.call_args_list]
    assert operations == ["READ", "READ", "STORE", "READ", "READ"]


def test_concurrent_cold_reads_expand_only_one_dashboard(repository):
    entered, second_read, release = Event(), Event(), Event()
    owner = []
    operation = repository.dashboard_preparation.side_effect
    def read(op, **kw):
        if op == "READ" and entered.is_set() and get_ident() != owner[0]:
            second_read.set()
        return operation(op, **kw)
    def project(repo, alias, snapshot):
        owner.append(get_ident()); entered.set()
        assert release.wait(3)
        return deepcopy(snapshot)
    repository.dashboard_preparation.side_effect = read
    repository.project.side_effect = project
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(prepared.dashboard_envelope, repository, ALIAS)
        assert entered.wait(2)
        second = pool.submit(prepared.dashboard_envelope, repository, ALIAS)
        try:
            assert second_read.wait(2)
        finally:
            release.set()
        assert first.result(timeout=3) == second.result(timeout=3)
    assert repository.project.call_count == repository.active_analysis.call_count == 1


@pytest.mark.parametrize("failure_at", ["READ", "STORE"])
def test_optional_store_failure_keeps_the_authoritative_result(repository, failure_at):
    original = repository.dashboard_preparation.side_effect
    def operation(op, **kw):
        if op == failure_at:
            raise PersistentStoreFailure("private-value")
        return original(op, **kw)
    repository.dashboard_preparation.side_effect = operation
    assert prepared.dashboard_envelope(repository, ALIAS) == envelope()
    assert repository.project.call_count == 1


def test_cache_for_another_athlete_cannot_escape_validation(repository):
    wrong = envelope()
    wrong["snapshot_payload"]["training_status"]["athlete_id"] = "other-athlete"
    repository.saved["value"] = wrong
    assert prepared.dashboard_envelope(repository, ALIAS) == envelope()
    assert repository.active_analysis.call_count == 1


def test_authorization_runs_before_any_prepared_read(monkeypatch, repository):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(dependencies, "repository", lambda: repository)
    response = TestClient(main.app).get("/api/v2/real/dashboard-view")
    assert response.status_code == 401
    repository.dashboard_preparation.assert_not_called()
    repository.active_analysis.assert_not_called()


def test_main_dashboard_uses_the_same_prepared_payload_for_all_periods(monkeypatch, repository):
    repository.saved["value"] = envelope()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(dependencies, "repository", lambda: repository)
    response = TestClient(main.app).get("/api/v2/real/dashboard-view?period_start=2026-08-09&period_end=2026-08-09", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 8
    assert response.json()["completed_work"]["period_start"] == "2026-08-09"
    repository.active_analysis.assert_not_called()
    repository.project.assert_not_called()
    invalid = TestClient(main.app).get("/api/v2/real/dashboard-view?period_start=2026-08-08", headers=AUTH)
    assert invalid.status_code == 422


def test_background_preparation_stores_a_fenced_result_and_then_cleans_obsolete_data(repository):
    assert prepared.process_dashboard(repository)
    calls = repository.dashboard_preparation.call_args_list
    assert [c.args[0] for c in calls] == ["CLAIM", "STORE"]
    assert calls[0].kwargs["token"] == calls[1].kwargs["token"]
    assert calls[1].kwargs["revision"] == 3 and calls[1].kwargs["day"] == "2026-08-09"
    assert calls[1].kwargs["envelope"] == envelope()
    repository.cleanup_prepared_data.assert_called_once_with(ALIAS)


def test_no_pending_preparation_does_no_model_work(repository):
    repository.dashboard_preparation.side_effect = lambda *a, **k: None
    assert prepared.process_dashboard(repository) is False
    repository.active_analysis.assert_not_called()


def test_lost_lease_cannot_publish_a_dashboard(monkeypatch, repository):
    @contextmanager
    def lost_lease(*args):
        lost = Event(); lost.set(); yield lost
    monkeypatch.setattr(prepared, "_lease", lost_lease)
    assert prepared.process_dashboard(repository)
    assert [c.args[0] for c in repository.dashboard_preparation.call_args_list] == ["CLAIM", "FAIL"]
    repository.cleanup_prepared_data.assert_not_called()


def test_superseded_preparation_does_not_cleanup_or_claim_success(repository):
    original = repository.dashboard_preparation.side_effect
    repository.dashboard_preparation.side_effect = lambda op, **kw: {"stored": False} if op == "STORE" else original(op, **kw)
    assert prepared.process_dashboard(repository)
    repository.cleanup_prepared_data.assert_not_called()


def test_preparation_failure_does_not_expose_private_error_values(repository, caplog):
    repository.project.side_effect = RuntimeError("private-value")
    assert prepared.process_dashboard(repository)
    assert [c.args[0] for c in repository.dashboard_preparation.call_args_list] == ["CLAIM", "FAIL"]
    assert "private-value" not in caplog.text


def training_repository():
    def operation(op, **kwargs):
        return {"athlete_alias": ALIAS, "actor_id": "99001000-0000-0000-0000-000000000001", "revision": 4} if op == "CLAIM" else {"state": "READY"}
    return SimpleNamespace(training_plan_update=Mock(side_effect=operation))


def test_no_new_training_does_not_refresh_or_queue_an_import(monkeypatch):
    repo = SimpleNamespace(training_plan_update=Mock(return_value=None))
    refresh = Mock()
    monkeypatch.setattr(management_lifecycle, "refresh", refresh)
    assert prepared.process_training(repo) is False
    refresh.assert_not_called()


def test_new_training_passes_a_claim_into_the_existing_approval_aware_lifecycle(monkeypatch):
    repo = training_repository()
    monkeypatch.setattr(management_plan_store, "PlanStore", lambda repo: SimpleNamespace(current=lambda alias: {"revision": 6}))
    refresh = Mock()
    monkeypatch.setattr(management_lifecycle, "refresh", refresh)
    assert prepared.process_training(repo)
    kwargs = refresh.call_args.kwargs
    assert kwargs["automatic"] is True and kwargs["include_saved_payload"] is False
    assert kwargs["expected_revision"] == 6
    assert kwargs["_background_claim"] == {"token": repo.training_plan_update.call_args_list[0].kwargs["token"], "revision": 4}
    assert [c.args[0] for c in repo.training_plan_update.call_args_list] == ["CLAIM", "COMPLETE"]


def test_configured_athlete_without_active_plan_gets_a_reviewable_draft_job(monkeypatch):
    repo = training_repository()
    today = date.today()
    repo.athlete_settings = lambda alias: SimpleNamespace(timezone="UTC")
    monkeypatch.setattr(management_plan_store, "PlanStore", lambda repo: SimpleNamespace(current=lambda alias: None))
    store = SimpleNamespace(profile=lambda alias: {"configured": True, "revision": 2,
        "profile": {"program_start": today.isoformat(), "program_end": (today+timedelta(days=30)).isoformat()}},
        draft_metadata=lambda alias, start: {"revision": 5})
    monkeypatch.setattr(management_store, "ManagementStore", lambda repo: store)
    enqueue = Mock()
    monkeypatch.setattr(planning_jobs, "enqueue", enqueue)
    assert prepared.process_training(repo)
    body = enqueue.call_args.args[2]
    assert body.start_date == today and body.expected_draft_revision == 5 and body.expected_profile_revision == 2
    assert [c.args[0] for c in repo.training_plan_update.call_args_list] == ["CLAIM", "COMPLETE"]


def test_failed_training_update_stays_retryable(monkeypatch):
    repo = training_repository()
    monkeypatch.setattr(management_plan_store, "PlanStore", lambda repo: SimpleNamespace(current=lambda alias: {"revision": 6}))
    monkeypatch.setattr(management_lifecycle, "refresh", Mock(side_effect=PersistentStoreFailure("private-value")))
    assert prepared.process_training(repo)
    assert [c.args[0] for c in repo.training_plan_update.call_args_list] == ["CLAIM", "FAIL"]


def test_background_plan_publication_uses_the_database_fence():
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path, kwargs["json"]))
        return {"saved": True, "revision": 8, "recorded_at": "2026-11-02T10:00:00+00:00"}
    repo = SimpleNamespace(_request=request, _json=lambda value: value)
    store = PlanStore(repo, background_claim={"token": "lease-token", "revision": 4})
    store.save(ALIAS, {}, 7, "actor", "REVIEW", 2, {}, automatic=True, include_payload=False)
    assert calls[0][1] == "/rpc/save_onflows_management_plan_after_training"
    assert calls[0][2]["p_training_token"] == "lease-token" and calls[0][2]["p_training_revision"] == 4
    assert "_background_claim" not in calls[0][2]["p_payload"]
    with pytest.raises(ValueError):
        store.save(ALIAS, {}, 7, "actor", "REVIEW", 2, {}, automatic=False)


def test_new_local_day_alone_keeps_a_prepared_prescription_current():
    original = {"evaluation_date": "2026-11-01", "generation_id": "g1", "profile_revision": 2, "model_entries": []}
    fingerprint = management_lifecycle._hash(original)
    today = {**original, "evaluation_date": "2026-11-02"}
    assert management_lifecycle._inputs_current(today, fingerprint, "2026-11-01")
    for field, value in [("generation_id", "g2"), ("profile_revision", 3), ("model_entries", [{"revision": 2}])]:
        assert not management_lifecycle._inputs_current({**today, field: value}, fingerprint, "2026-11-01")
    assert not management_lifecycle._inputs_current(today, fingerprint, "2026-11-03")


def test_feature_flag_and_build_changes_do_not_reuse_a_prepared_view(monkeypatch):
    monkeypatch.setenv("ONFLOWS_RECOVERY_V2_ENABLED", "true")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "release-one")
    first = prepared.model_key()
    monkeypatch.setenv("ONFLOWS_RECOVERY_V2_ENABLED", "false")
    assert prepared.model_key() != first
    monkeypatch.setenv("ONFLOWS_RECOVERY_V2_ENABLED", "true")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "release-two")
    assert prepared.model_key() != first
