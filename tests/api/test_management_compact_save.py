"""Compact acknowledgements preserve publication and fresh HTTP response contracts."""
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.api import dependencies, management_lifecycle
from apps.api.main import app
from apps.api.management_plan_store import PlanStore
from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from apps.api.routes import management
from tests.api.test_supabase_generation_rpc_contract import ENCRYPTION_KEY


ACTOR = "11111111-1111-4111-8111-111111111111"
STAMP = "2026-10-19T09:20:00.123456+00:00"
PAYLOAD = {"status": "REVIEW_REQUIRED", "plan": {"approved": True},
           "proposal": {"review_required": True, "dose": 1.0}}
ACK = {"saved": True, "revision": 8, "recorded_at": STAMP}


def save_result(result, *, include_payload=True):
    requests = []
    def request(method, path, **kwargs):
        requests.append((method, path, deepcopy(kwargs)))
        return deepcopy(result)
    repository = SimpleNamespace(_request=request, _json=lambda value: value)
    actual = PlanStore(repository).save("athlete", PAYLOAD, 7, ACTOR, "REVIEW", 2,
        {"generation_id": "generation"}, include_payload=include_payload)
    return actual, requests


@pytest.mark.parametrize("include_payload", [True, False])
def test_full_and_compact_saves_send_identical_evidence_and_validate_ack(include_payload):
    result = {**ACK, **({"payload": deepcopy(PAYLOAD)} if include_payload else {})}
    actual, requests = save_result(result, include_payload=include_payload)
    assert actual == result
    method, path, arguments = requests[0]
    assert method == "POST"
    assert path == "/rpc/save_onflows_management_plan" + ("" if include_payload else "_compact")
    assert arguments["json"] == {
        "p_alias": "athlete", "p_payload": PAYLOAD, "p_expected_revision": 7,
        "p_actor": ACTOR, "p_operation": "REVIEW", "p_expected_profile_revision": 2,
        "p_checkpoint": {"generation_id": "generation"}, "p_automatic": False,
    }
    # Compact mode never pretends the local input is a database readback.
    assert ("payload" in actual) is include_payload


@pytest.mark.parametrize("patch", [
    {"saved": False}, {"saved": 1}, {"revision": True}, {"revision": 0},
    {"revision": 7}, {"revision": 9}, {"revision": "8"},
    {"recorded_at": None}, {"recorded_at": 1}, {"recorded_at": "invalid"},
    {"recorded_at": "2026-10-19T09:20:00"},
])
def test_malformed_compact_ack_fails_closed(patch):
    result = {**ACK, **patch}
    with pytest.raises(PersistentStoreFailure):
        save_result(result, include_payload=False)


@pytest.mark.parametrize("result", [None, [], {}, {"saved": True, "revision": 8},
    {**ACK, "payload": PAYLOAD}])
def test_compact_save_rejects_missing_or_unexpected_echo(result):
    with pytest.raises(PersistentStoreFailure):
        save_result(result, include_payload=False)


def test_full_save_still_requires_persisted_payload():
    with pytest.raises(PersistentStoreFailure):
        save_result(ACK)


def test_full_save_keeps_existing_validation_contract():
    result = {"saved": True, "revision": 8, "payload": PAYLOAD}
    actual, requests = save_result(result)
    assert actual == result
    assert requests[0][1] == "/rpc/save_onflows_management_plan"


@pytest.mark.parametrize("include_payload", [True, False])
def test_compact_conflict_remains_409_without_retry(include_payload):
    with pytest.raises(HTTPException) as exc:
        save_result({"conflict": True, "reason": "INPUTS_CHANGED"}, include_payload=include_payload)
    assert exc.value.status_code == 409


def test_compact_transport_failure_does_not_repeat_the_write_via_legacy_rpc():
    calls = []
    def request(method, path, **kwargs):
        calls.append(path)
        raise PersistentStoreFailure("Response lost after possible publication")
    repository = SimpleNamespace(_request=request, _json=lambda value: value)
    with pytest.raises(PersistentStoreFailure):
        PlanStore(repository).save("athlete", PAYLOAD, 7, ACTOR, "REVIEW", 2, {}, include_payload=False)
    assert calls == ["/rpc/save_onflows_management_plan_compact"]


@pytest.mark.parametrize("include_payload", [True, False])
@pytest.mark.parametrize("response_lost", [True, False])
def test_publication_transport_outlives_sql_budget_without_retry_or_global_timeout_change(include_payload, response_lost):
    requests = []
    result = {**ACK, **({"payload": PAYLOAD} if include_payload else {})}
    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        assert request.extensions["timeout"] == {"connect": 5., "read": 35., "write": 35., "pool": 35.}
        if response_lost:
            raise httpx.ReadTimeout("Publication outcome is unknown", request=request)
        return httpx.Response(200, json=result)
    with httpx.Client(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(15., connect=5.)) as client:
        repository = SupabasePilotRepository(supabase_url="https://plan-test.supabase.co",
            secret_key="sb_secret_test-only", encryption_key=ENCRYPTION_KEY, client=client)
        store = PlanStore(repository)
        if response_lost:
            with pytest.raises(PersistentStoreFailure):
                store.save("athlete", PAYLOAD, 7, ACTOR, "REVIEW", 2, {}, include_payload=include_payload)
        else:
            assert store.save("athlete", PAYLOAD, 7, ACTOR, "REVIEW", 2, {}, include_payload=include_payload) == result
        repository._request("GET", "/onflows_management_plan_state")
    assert [request.method for request in requests] == ["POST", "GET"]
    assert requests[-1].extensions["timeout"]["read"] == 15.


@pytest.mark.parametrize("operation,route,body", [
    ("activate", "activate", {"start_date": "2026-10-19", "draft_revision": 1, "expected_revision": 7}),
    ("refresh", "day", {"date": "2026-10-19", "action": "SKIP", "expected_revision": 7}),
    ("action", "action", {"action": "REFRESH", "expected_revision": 7}),
])
def test_action_route_discards_compact_ack_and_returns_fresh_current(monkeypatch, operation, route, body):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "test-service-token")
    repository = SimpleNamespace()
    monkeypatch.setattr(dependencies, "repository", lambda: repository)
    calls = []
    def publish(repo, alias, *args, **kwargs):
        assert repo is repository
        assert kwargs["include_saved_payload"] is False
        calls.append("publish")
        return deepcopy(ACK)
    def current(repo, alias):
        assert repo is repository
        assert calls == ["publish"]
        calls.append("fresh_read")
        # Another publication can become current after this request's save.
        return {"active": {"revision": 9, "payload": PAYLOAD}, "history": []}
    monkeypatch.setattr(management.management_lifecycle, operation, publish)
    monkeypatch.setattr(management.management_lifecycle, "current", current)
    response = TestClient(app).post(f"/api/v2/athlete/management/{route}", json=body, headers={
        "Authorization": "Bearer test-service-token", "X-OnFlows-Athlete-Alias": "athlete",
        "X-OnFlows-Actor-Id": ACTOR,
    })
    assert response.status_code == 200
    assert response.json() == {"active": {"revision": 9, "payload": PAYLOAD}, "history": [],
                               "capabilities": {"unchanged_checks": True}}
    assert calls == ["publish", "fresh_read"]


def test_action_route_does_not_hide_publication_failure(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "test-service-token")
    monkeypatch.setattr(dependencies, "repository", lambda: SimpleNamespace())
    def publish(*args, **kwargs):
        raise PersistentStoreFailure("Malformed save acknowledgement")
    monkeypatch.setattr(management.management_lifecycle, "action", publish)
    monkeypatch.setattr(management.management_lifecycle, "current",
        lambda *args, **kwargs: pytest.fail("Failed publication must not be replaced with a successful read"))
    response = TestClient(app).post("/api/v2/athlete/management/action",
        json={"action": "REFRESH", "expected_revision": 7}, headers={
            "Authorization": "Bearer test-service-token", "X-OnFlows-Athlete-Alias": "athlete",
            "X-OnFlows-Actor-Id": ACTOR,
        })
    assert response.status_code == 503


def test_worker_requests_compact_ack_and_does_not_republish_result(monkeypatch):
    calls = []
    store = SimpleNamespace(
        due=lambda now: [{"athlete_alias": "athlete", "revision": 7, "approved_by": ACTOR}],
        queue_import=lambda alias: calls.append("queue_import"),
        defer_check=lambda *args: pytest.fail("Successful compact publication must not be deferred"))
    monkeypatch.setattr(management_lifecycle, "PlanStore", lambda repository: store)
    def refresh(repository, alias, actor, **kwargs):
        assert kwargs["automatic"] is True and kwargs["include_saved_payload"] is False
        calls.append("refresh")
        return deepcopy(ACK)
    monkeypatch.setattr(management_lifecycle, "refresh", refresh)
    assert management_lifecycle.run_due(SimpleNamespace()) == 1
    assert calls == ["queue_import", "refresh"]
