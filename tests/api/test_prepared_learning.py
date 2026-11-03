from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.api import background_planning, dependencies, learning_view, main, prepared_learning as prepared
from apps.api.oauth_store import PersistentStoreFailure


ALIAS = "ath-learning-cache"
DAY = "2026-11-03"


def report():
    return {"schema_version": "recovery-learning-view-v1", "as_of": DAY,
        "series": [{"day": "2026-08-06", "actual_q": 5}, {"day": "2026-10-05", "actual_q": None},
                   {"day": "2026-11-02", "actual_q": 10}],
        "components": [{"component": "Z1", "proposed_change_percent": None}]}


def payload():
    return {"athlete_alias": ALIAS, "report": report()}


def repository():
    saved = {}
    def operation(op, **kw):
        if op == "READ":
            return {"ready": True, "day": DAY, "payload": deepcopy(saved["value"]),
                    "prepared_at": "2026-11-03T07:00:00Z"} if saved else {"ready": False}
        if op == "CLAIM":
            return {"athlete_alias": ALIAS, "revision": 7, "day": DAY}
        if op == "STORE":
            saved["value"] = deepcopy(kw["payload"])
            return {"stored": True}
        return {"state": "RUNNING"}
    return SimpleNamespace(learning_preparation=Mock(side_effect=operation), saved=saved)


def test_hit_is_one_prepared_read_without_any_model_or_source_work(monkeypatch):
    repo = repository()
    repo.saved["value"] = payload()
    compute = Mock(side_effect=AssertionError("HTTP must never fit the model"))
    monkeypatch.setattr(learning_view, "view", compute)
    result = prepared.read_view(repo, ALIAS, days=30)
    assert result["series"] == report()["series"][1:]
    assert result["preparation"] == {"status": "READY", "prepared_at": "2026-11-03T07:00:00Z"}
    result["components"][0]["component"] = "corrupted by caller"
    assert prepared.read_view(repo, ALIAS)["components"] == report()["components"]
    assert [c.args[0] for c in repo.learning_preparation.call_args_list] == ["READ", "READ"]
    compute.assert_not_called()


@pytest.mark.parametrize("failure", [None, {"ready": False}, {"ready": True, "day": DAY, "payload": {
    "athlete_alias": "other-athlete", "report": report()}},
    {"ready": True, "day": "2026-11-04", "payload": payload()}])
def test_cold_or_invalid_projection_never_calculates_on_http(monkeypatch, failure):
    repo = repository()
    repo.learning_preparation.side_effect = lambda *a, **kw: failure
    compute = Mock(side_effect=AssertionError("No synchronous fallback"))
    monkeypatch.setattr(learning_view, "view", compute)
    with pytest.raises(HTTPException) as error:
        prepared.read_view(repo, ALIAS)
    assert error.value.status_code == 503
    assert error.value.headers == {"Retry-After": "15", "Cache-Control": "no-store"}
    assert error.value.detail == prepared.PREPARING
    compute.assert_not_called()
    assert [c.args[0] for c in repo.learning_preparation.call_args_list] == ["READ"]


def test_storage_failure_does_not_expose_private_values_or_calculate(monkeypatch, caplog):
    repo = repository()
    repo.learning_preparation.side_effect = PersistentStoreFailure("private health value")
    compute = Mock()
    monkeypatch.setattr(learning_view, "view", compute)
    with pytest.raises(HTTPException) as error:
        prepared.read_view(repo, ALIAS)
    assert "private health value" not in str(error.value.detail) + caplog.text
    compute.assert_not_called()


def test_build_or_policy_version_changes_cache_identity(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "first-build")
    first = prepared.model_key()
    monkeypatch.setenv("RENDER_GIT_COMMIT", "next-build")
    assert prepared.model_key() != first


def test_worker_computes_once_then_all_periods_read_the_same_report(monkeypatch):
    repo = repository()
    compute = Mock(return_value=report())
    monkeypatch.setattr(learning_view, "view", compute)
    assert prepared.process_learning(repo)
    compute.assert_called_once_with(repo, ALIAS, days=90)
    calls = repo.learning_preparation.call_args_list
    assert [c.args[0] for c in calls] == ["CLAIM", "STORE"]
    assert calls[0].kwargs["token"] == calls[1].kwargs["token"]
    assert calls[1].kwargs["revision"] == 7 and calls[1].kwargs["day"] == DAY
    assert calls[1].kwargs["payload"] == payload()
    prepared.read_view(repo, ALIAS, days=30)
    prepared.read_view(repo, ALIAS, days=90)
    assert compute.call_count == 1


def test_no_pending_preparation_does_no_model_work(monkeypatch):
    repo = repository()
    repo.learning_preparation.side_effect = lambda *a, **kw: None
    compute = Mock()
    monkeypatch.setattr(learning_view, "view", compute)
    assert prepared.process_learning(repo) is False
    compute.assert_not_called()


@pytest.mark.parametrize("failure", ["day", "lease", "compute"])
def test_changed_day_lost_lease_or_compute_error_never_store(monkeypatch, failure, caplog):
    repo = repository()
    result = report()
    if failure == "day":
        result["as_of"] = "2026-11-04"
    if failure == "lease":
        @contextmanager
        def lost(*a):
            flag = Event(); flag.set(); yield flag
        monkeypatch.setattr(prepared, "_lease", lost)
    compute = Mock(return_value=result)
    if failure == "compute":
        compute.side_effect = RuntimeError("private health value")
    monkeypatch.setattr(learning_view, "view", compute)
    assert prepared.process_learning(repo)
    assert [c.args[0] for c in repo.learning_preparation.call_args_list] == ["CLAIM", "FAIL"]
    assert "private health value" not in caplog.text


def test_authorization_and_cold_http_response_do_not_touch_source(monkeypatch):
    repo = repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "test-learning-cache")
    monkeypatch.setattr(dependencies, "repository", lambda: repo)
    compute = Mock()
    monkeypatch.setattr(learning_view, "view", compute)
    client = TestClient(main.app)
    url = "/api/v2/athlete/learning"
    assert client.get(url).status_code == 401
    repo.learning_preparation.assert_not_called()
    headers = {"Authorization": "Bearer test-learning-cache", "X-OnFlows-Athlete-Alias": ALIAS}
    response = client.get(url, headers=headers)
    assert response.status_code == 503 and response.json()["detail"] == prepared.PREPARING
    assert response.headers["retry-after"] == "15" and response.headers["cache-control"] == "no-store"
    compute.assert_not_called()


def test_learning_preparation_gets_a_turn_even_with_a_busy_planning_queue():
    stopping = Event()
    calls = []
    def job():
        calls.append("job")
        return True
    def warm_learning():
        calls.append("learning")
        stopping.set()
    worker = background_planning.start(SimpleNamespace(process_next_planning_job=job,
        process_next_prepared_learning=warm_learning), stopping)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert calls == ["job", "learning"]
