"""Only the dedicated history-reference failure becomes an HTTP edit conflict."""
from copy import deepcopy
import json

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from apps.api.management_plan_store import PlanStore
from apps.api.management_store import ManagementStore, PROFILE_KEY
from apps.api.model_service import ModelStore
from apps.api.model_schemas import initial_settings
from apps.api import dependencies, management_lifecycle, management_service, model_service, response_service
from apps.api.main import app
from apps.api.oauth_store import (
    HistoricalGenerationReferenceConflict,
    PersistentStoreFailure,
    SupabasePilotRepository,
)
from apps.api.response_service import ResponseStore
from tests.api.test_management_api import PROFILE
from tests.api.test_supabase_generation_rpc_contract import ENCRYPTION_KEY


ACTOR = "11111111-1111-4111-8111-111111111111"
GENERATION = "22222222-2222-4222-8222-222222222222"
STAMP = "2026-10-19T18:00:00+00:00"
PAYLOAD = {"start_date": "2026-10-19", "status": "REVIEW_REQUIRED",
           "source": {"generation_id": GENERATION}, "private_evidence": "private-payload-content"}
CHECKPOINT = {"generation_id": GENERATION, "models": [], "responses": []}
ERROR = {"code": "PT409", "hint": "ONFLOWS_HISTORY_REFERENCE_CONFLICT",
         "message": "Historical generation reference conflict",
         "details": "private-database-detail"}
WRITERS = ["draft", "profile", "plan", "compact_plan", "response", "model_recovery", "model_speed_test"]


def transport(response):
    requests = []

    def handler(request):
        requests.append(request)
        if callable(response):
            return response(request)
        return httpx.Response(response[0], json=response[1])

    repository = SupabasePilotRepository(
        supabase_url="https://history-test.supabase.co", secret_key="sb_secret_test-only",
        encryption_key=ENCRYPTION_KEY, client=httpx.Client(transport=httpx.MockTransport(handler)),
        generation_reads=True)
    return repository, requests


def write(repository, writer, payload, checkpoint):
    if writer == "profile":
        return ManagementStore(repository).save_profile("athlete", payload, 2, ACTOR)
    if writer == "draft":
        return ManagementStore(repository).save_draft("athlete", payload, ACTOR, 1,
            expected_revision=2, expected_generation_id=GENERATION, check_generation=True,
            expected_responses=[])
    if writer in {"plan", "compact_plan"}:
        return PlanStore(repository).save("athlete", payload, 2, ACTOR, "REVIEW", 1, checkpoint,
                                          include_payload=writer == "plan")
    if writer in {"model_recovery", "model_speed_test"}:
        kind = "RECOVERY" if writer == "model_recovery" else "SPEED_TEST"
        return ModelStore(repository).save("athlete", kind, "model-key", payload, 2, ACTOR)
    return ResponseStore(repository).save("athlete", "TEST", "test-key", "2026-10-19", payload, 2, ACTOR)


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("failure", ["busy", "missing", "foreign", "malformed"])
def test_exact_database_reference_conflicts_are_409_without_repeating_or_changing_the_write(writer, failure, caplog):
    repository, requests = transport((409, {**ERROR, "message": "private-" + failure,
                                           "details": "private-" + GENERATION}))
    payload, checkpoint = deepcopy(PAYLOAD), deepcopy(CHECKPOINT)
    with pytest.raises(HTTPException) as caught:
        write(repository, writer, payload, checkpoint)
    assert caught.value.status_code == 409
    cause = caught.value.__cause__
    assert isinstance(cause, HistoricalGenerationReferenceConflict)
    assert isinstance(cause, PersistentStoreFailure) and cause.reason == "ANALYSIS_CHANGED"
    assert payload == PAYLOAD and checkpoint == CHECKPOINT
    assert len(requests) == 1 and requests[0].method == "POST"
    sent = json.loads(requests[0].content)
    assert sent["p_payload"] == PAYLOAD
    assert sent["p_expected_revision"] == 2 and sent["p_actor"] == ACTOR
    if writer in {"plan", "compact_plan"}:
        assert sent["p_checkpoint"] == CHECKPOINT
    for private in ("private-" + failure, "private-" + GENERATION, "private-database-detail",
                    "private-payload-content"):
        assert private not in str(caught.value.detail) + str(cause) + caplog.text


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("status,error", [
    (409, {"code": "PT409"}),
    (409, {"hint": "ONFLOWS_HISTORY_REFERENCE_CONFLICT"}),
    (409, {**ERROR, "code": "23503"}),
    (409, {**ERROR, "code": "pt409"}),
    (409, {**ERROR, "hint": "ONFLOWS_HISTORY_REFERENCE_CONFLICT_extra"}),
    (409, {**ERROR, "hint": None}),
    (403, {**ERROR, "code": "42501"}),
    (503, ERROR),
    (401, ERROR),
    (409, [ERROR]),
])
def test_other_storage_errors_remain_failures_without_fallback_or_retry(writer, status, error):
    repository, requests = transport((status, error))
    with pytest.raises(PersistentStoreFailure) as caught:
        write(repository, writer, PAYLOAD, CHECKPOINT)
    assert not isinstance(caught.value, HistoricalGenerationReferenceConflict)
    assert len(requests) == 1


@pytest.mark.parametrize("writer", WRITERS)
@pytest.mark.parametrize("failure", ["invalid_json", "network"])
def test_ambiguous_or_invalid_responses_never_become_success_or_a_different_write(writer, failure):
    def response(request):
        if failure == "network":
            raise httpx.ReadTimeout("Response lost after possible publication", request=request)
        return httpx.Response(409, content=b"not-json")

    repository, requests = transport(response)
    with pytest.raises(PersistentStoreFailure) as caught:
        write(repository, writer, PAYLOAD, CHECKPOINT)
    assert not isinstance(caught.value, HistoricalGenerationReferenceConflict)
    assert len(requests) == 1


@pytest.mark.parametrize("method,path,arguments", [
    ("GET", "/rpc/save_onflows_management_plan", {}),
    ("POST", "/onflows_management_entries", {"kind": "DRAFT"}),
    ("POST", "/onflows_management_plan_revisions", {}),
    ("POST", "/onflows_response_entries", {}),
    ("POST", "/onflows_model_entries", {"kind": "SPEED_TEST"}),
    ("POST", "/rpc/record_onflows_management_check", {}),
    ("POST", "/rpc/save_onflows_model_entry", {}),
    ("POST", "/rpc/save_onflows_management_plan_extra", {}),
    ("POST", "/rpc/save_onflows_management_entry", {"p_kind": "draft"}),
    ("POST", "/rpc/save_onflows_management_entry", {"p_kind": "DECISION"}),
    ("POST", "/rpc/save_onflows_management_entry", {"p_kind": ["PROFILE"]}),
    ("POST", "/rpc/save_onflows_management_entry", {}),
    ("POST", "/rpc/save_onflows_model_entry", {"p_kind": "speed_test"}),
    ("POST", "/rpc/save_onflows_model_entry", {"p_kind": "UNKNOWN"}),
    ("POST", "/rpc/save_onflows_model_entry", {"p_kind": {"kind": "SPEED_TEST"}}),
    ("GET", "/rpc/save_onflows_model_entry", {"p_kind": "SPEED_TEST"}),
])
def test_matching_error_is_not_reclassified_outside_supported_history_write_paths(method, path, arguments):
    repository, requests = transport((409, ERROR))
    with pytest.raises(PersistentStoreFailure) as caught:
        repository._request(method, path, json=arguments)
    assert not isinstance(caught.value, HistoricalGenerationReferenceConflict)
    assert len(requests) == 1


@pytest.mark.parametrize("writer", WRITERS)
def test_normal_success_and_ordinary_json_conflicts_keep_existing_contracts(writer):
    acknowledgement = {"saved": True, "revision": 3, "recorded_at": STAMP,
                       "entry_key": PROFILE_KEY if writer == "profile" else PAYLOAD["start_date"],
                       **({"payload": deepcopy(PAYLOAD)} if writer != "compact_plan" else {})}
    repository, requests = transport((200, acknowledgement))
    assert write(repository, writer, PAYLOAD, CHECKPOINT) == acknowledgement
    assert len(requests) == 1

    repository, requests = transport((200, {"conflict": True, "reason": "ANALYSIS_CHANGED"}))
    with pytest.raises(HTTPException) as caught:
        write(repository, writer, PAYLOAD, CHECKPOINT)
    assert caught.value.status_code == 409 and caught.value.__cause__ is None
    assert len(requests) == 1


@pytest.mark.parametrize("writer,method,path,body", [
    ("draft", "POST", "/api/v2/athlete/management/generate",
     {"start_date": "2026-10-19", "expected_profile_revision": 1, "expected_draft_revision": 2}),
    ("compact_plan", "POST", "/api/v2/athlete/management/action", {"action": "REFRESH", "expected_revision": 2}),
    ("response", "PUT", "/api/v2/athlete/response/test",
     {"day": "2026-10-19", "protocol": "same test", "protocol_version": "1", "value": 100.,
      "unit": "W", "direction": "HIGHER", "conditions": "same conditions", "components": ["Z3"]}),
    ("profile", "PUT", "/api/v2/athlete/management/profile",
     {"profile": {**PROFILE, "method_library": {}, "ant_references": []}, "expected_revision": 2}),
    ("model_recovery", "PUT", "/api/v2/athlete/models/recovery",
     {"zones": initial_settings(), "expected_revision": 2}),
    ("model_speed_test", "PUT", "/api/v2/athlete/models/speed-test",
     {"activity_ref": "act_" + "a" * 32, "start_s": 0, "duration_s": 180, "maximal": True,
      "comparable": True, "conditions": "same conditions", "expected_revision": 2}),
    ("model_speed_test", "PUT", "/api/v2/athlete/models/speed-test-manual",
     {"test_id": ACTOR, "name": "Same measured test", "day": "2026-10-19", "sport": "Run",
      "duration_s": 180., "speed_kmh": 20., "maximal": True, "comparable": True, "flat_terrain": True,
      "conditions": "same conditions", "expected_revision": 2}),
])
@pytest.mark.parametrize("exact_signal,status", [(True, 409), (False, 503)])
def test_real_http_routes_preserve_authentication_and_expose_only_the_trusted_conflict(
    monkeypatch, writer, method, path, body, exact_signal, status,
):
    repository, requests = transport((409, {**ERROR,
        "hint": ERROR["hint"] if exact_signal else "unrelated-conflict"}))
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "test-service-token")
    monkeypatch.setattr(dependencies, "repository", lambda: repository)

    def publish(*args, **kwargs):
        return write(repository, writer, PAYLOAD, CHECKPOINT)

    # Skip expensive calculation, retaining the actual route, writer adapter,
    # Supabase HTTP transport and failure boundary under test.
    monkeypatch.setattr(management_service, "generate", publish)
    monkeypatch.setattr(management_lifecycle, "action", publish)
    monkeypatch.setattr(response_service, "save_report", publish)
    monkeypatch.setattr(model_service, "save_test", publish)
    monkeypatch.setattr(model_service, "save_manual_test", publish)
    monkeypatch.setattr(management_lifecycle, "current", lambda *args, **kwargs: pytest.fail(
        "A failed publication must not continue with a successful current-plan response"))
    with TestClient(app) as client:
        assert client.request(method, path, json=body).status_code == 401
        assert not requests
        response = client.request(method, path, json=body, headers={
            "Authorization": "Bearer test-service-token", "X-OnFlows-Athlete-Alias": "athlete",
            "X-OnFlows-Actor-Id": ACTOR})
    assert response.status_code == status
    assert set(response.json()) == {"detail"}
    assert "private-" not in response.text and GENERATION not in response.text
    assert len(requests) == 1
