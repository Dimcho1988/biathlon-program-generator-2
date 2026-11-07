"""Checks remain lazy and transient; current plan identity stays authoritative."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api import dependencies
from apps.api.main import app
from apps.api.routes import management
from apps.api.oauth_store import PersistentStoreFailure
from tests.api.test_management_api import HEADERS

CHECK = {"unchanged": True, "checked_revision": 7,
         "check_id": "cde76e46-2767-4aad-8c9c-cc156745e699", "recorded_at": "2026-10-19T11:00:00+00:00", "replayed": False}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(dependencies, "repository", lambda: SimpleNamespace())
    return TestClient(app)


@pytest.mark.parametrize("revision", [7, 8])
def test_manual_check_receipt_is_separate_from_fresh_current_and_not_saved(client, monkeypatch, revision):
    old = {"revision": 7, "payload": {"status": "REVIEW_REQUIRED"}}
    calls = []
    def action(repo, alias, body, actor, **kwargs):
        assert alias == "ath-test" and body.record_unchanged_check is True
        assert kwargs == {"include_saved_payload": False}
        calls.append("check")
        return {**deepcopy(old), "unchanged_check": CHECK}
    def current(repo, alias):
        calls.append("fresh")
        return {"active": {"revision": revision, "payload": {"status": "REVIEW_REQUIRED"}}, "history": []}
    monkeypatch.setattr(management.management_lifecycle, "action", action)
    monkeypatch.setattr(management.management_lifecycle, "current", current)
    response = client.post("/api/v2/athlete/management/action", headers=HEADERS,
        json={"action": "REFRESH", "expected_revision": 7, "record_unchanged_check": True})
    assert response.status_code == 200
    value = response.json()
    assert value["active"]["revision"] == revision and value["active"]["payload"]["status"] == "REVIEW_REQUIRED"
    assert value["action_result"] == {"operation": "CHECK_NO_CHANGE", **{k: CHECK[k] for k in ("checked_revision", "check_id", "recorded_at")}}
    assert value["capabilities"] == {"unchanged_checks": True}
    assert calls == ["check", "fresh"]
    assert "unchanged_check" not in old and "action_result" not in value["active"]


def test_checks_reader_requires_scope_is_bounded_and_no_store(client, monkeypatch):
    calls = []
    class Store:
        def checks(self, alias, **kwargs):
            calls.append((alias, kwargs))
            return {"checks": [], "next_before": None}
    monkeypatch.setattr(management, "PlanStore", lambda repo: Store())
    url = "/api/v2/athlete/management/checks"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer service-secret"}).status_code == 401
    for query in ("?limit=0", "?limit=51", "?before=0", "?before=9007199254740992"):
        assert client.get(url+query, headers=HEADERS).status_code == 422
    assert calls == []
    response = client.get(url+"?limit=10&before=37", headers=HEADERS)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {"checks": [], "next_before": None}
    assert calls == [("ath-test", {"before": 37, "limit": 10})]


def test_no_check_history_query_on_active_or_state(client, monkeypatch):
    class Store:
        def checks(self, *args, **kwargs):
            raise AssertionError("Check history must be fetched only on explicit read")
    monkeypatch.setattr(management, "PlanStore", lambda repo: Store())
    monkeypatch.setattr(management.management_lifecycle, "current", lambda *a, **kw: {"active": None, "history": []})
    monkeypatch.setattr(management.management_lifecycle, "state", lambda *a, **kw: {"revision": 0, "stale": False})
    response = client.get("/api/v2/athlete/management/active", headers=HEADERS)
    assert response.json() == {"active": None, "history": [], "capabilities": {"unchanged_checks": True}}
    assert client.get("/api/v2/athlete/management/state", headers=HEADERS).json() == {"revision": 0, "stale": False}


def test_failed_check_is_not_replayed_as_a_full_save(client, monkeypatch):
    def failed(*args, **kwargs):
        raise PersistentStoreFailure("Ambiguous POST outcome")
    def unexpected(*args, **kwargs):
        raise AssertionError("Failed check cannot fall through to read or save")
    monkeypatch.setattr(management.management_lifecycle, "action", failed)
    monkeypatch.setattr(management.management_lifecycle, "current", unexpected)
    response = client.post("/api/v2/athlete/management/action", headers=HEADERS,
        json={"action": "REFRESH", "expected_revision": 7, "record_unchanged_check": True})
    assert response.status_code == 503
