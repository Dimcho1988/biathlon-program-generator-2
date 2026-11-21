"""Transport and validation boundaries for checks that do not rewrite a plan."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException

from apps.api.management_plan_store import PlanStore
from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from apps.api.request_metrics import StoreMetrics, current_metrics
from tests.api.test_supabase_generation_rpc_contract import ENCRYPTION_KEY


ACTOR = UUID("11111111-1111-4111-8111-111111111111")
OTHER_CHECK = "22222222-2222-4222-8222-222222222222"
STAMP = "2026-10-19T13:00:00.123456+00:00"
ALIAS = "athlete&one/two?"
CHECKPOINT = {
    "generation_id": "33333333-3333-4333-8333-333333333333",
    "settings": {"hr_zone_bounds": [100, 120, 140, 160, 180, 200],
                 "hrmax_bpm": 200, "timezone": "Europe/Sofia", "planning_calendar": {"events": []}},
    "models": [{"kind": "RECOVERY", "entry_key": "recovery-v2", "revision": 5}],
    "responses": [],
}


def acknowledgement(body, *, replayed=False):
    return {"unchanged": True, "checked_revision": 652, "check_id": body["p_check_id"],
            "recorded_at": STAMP, "replayed": replayed}


def transport(response):
    requests = []
    def handler(request):
        requests.append(request)
        body = json.loads(request.content) if request.content else None
        result = response(request, body) if callable(response) else deepcopy(response)
        return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)
    repository = SupabasePilotRepository(
        supabase_url="https://checks-test.supabase.co", secret_key="sb_secret_test-only",
        encryption_key=ENCRYPTION_KEY, client=httpx.Client(transport=httpx.MockTransport(handler)),
        generation_reads=True)
    return PlanStore(repository), requests


def record(store, alias=ALIAS):
    return store.record_unchanged_check(alias, expected_revision=652, actor_id=ACTOR,
        expected_profile_revision=12, checkpoint=CHECKPOINT)


@pytest.mark.parametrize("replayed", [False, True])
def test_receipt_is_tied_to_a_server_generated_id_without_sending_either_plan(replayed):
    store, requests = transport(lambda request, body: acknowledgement(body, replayed=replayed))
    original = deepcopy(CHECKPOINT)
    result = record(store)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST" and request.url.path == "/rest/v1/rpc/record_onflows_management_check"
    assert not request.url.query
    body = json.loads(request.content)
    assert set(body) == {"p_alias", "p_expected_revision", "p_actor", "p_expected_profile_revision",
                         "p_checkpoint", "p_check_id"}
    assert body["p_alias"] == ALIAS and body["p_expected_revision"] == 652
    assert body["p_actor"] == str(ACTOR) and body["p_expected_profile_revision"] == 12
    assert body["p_checkpoint"] == CHECKPOINT == original
    assert UUID(body["p_check_id"]).version == 4
    assert len(request.content) < 2000  # This synthetic checkpoint has no plan/proposal evidence.
    assert result == acknowledgement(body, replayed=replayed)
    assert not ({"payload", "p_payload", "saved", "plan", "proposal"} & set(body))


def test_separate_explicit_checks_receive_distinct_server_ids():
    store, requests = transport(lambda request, body: acknowledgement(body))
    first, second = record(store), record(store)
    assert first["checked_revision"] == second["checked_revision"] == 652
    assert first["check_id"] != second["check_id"]
    assert len(requests) == 2


@pytest.mark.parametrize("patch", [
    {"unchanged": False}, {"unchanged": 1}, {"unchanged": "true"},
    {"checked_revision": True}, {"checked_revision": 0}, {"checked_revision": 651},
    {"checked_revision": 653}, {"checked_revision": "652"},
    {"check_id": OTHER_CHECK}, {"check_id": None}, {"check_id": "invalid"},
    {"recorded_at": None}, {"recorded_at": 3}, {"recorded_at": "invalid"},
    {"recorded_at": "2026-10-19T13:00:00"},
    {"replayed": None}, {"replayed": 0}, {"replayed": "false"},
    {"payload": {}}, {"saved": False}, {"saved": True}, {"unexpected": "field"},
])
def test_malformed_or_foreign_acknowledgement_never_falls_back_to_saving_a_plan(patch):
    store, requests = transport(lambda request, body: {**acknowledgement(body), **patch})
    with pytest.raises(PersistentStoreFailure):
        record(store)
    assert len(requests) == 1 and requests[0].url.path.endswith("/record_onflows_management_check")


@pytest.mark.parametrize("missing", ["unchanged", "checked_revision", "check_id", "recorded_at", "replayed"])
def test_incomplete_acknowledgement_is_a_store_failure(missing):
    def response(request, body):
        result = acknowledgement(body)
        del result[missing]
        return result
    store, requests = transport(response)
    with pytest.raises(PersistentStoreFailure):
        record(store)
    assert len(requests) == 1


@pytest.mark.parametrize("value", [None, [], {}, "unchanged"])
def test_non_receipt_response_is_rejected_without_retry(value):
    store, requests = transport(value)
    with pytest.raises(PersistentStoreFailure):
        record(store)
    assert len(requests) == 1


@pytest.mark.parametrize("reason", ["REVISION_CHANGED", "PROFILE_CHANGED", "INPUTS_CHANGED", "LOCAL_DATE_CHANGED"])
def test_concurrency_conflict_stays_409_and_never_creates_a_revision(reason):
    store, requests = transport({"conflict": True, "reason": reason})
    with pytest.raises(HTTPException) as exc:
        record(store)
    assert exc.value.status_code == 409
    assert len(requests) == 1 and requests[0].url.path.endswith("/record_onflows_management_check")


@pytest.mark.parametrize("failure", ["timeout", "http_error", "invalid_json"])
def test_lost_or_invalid_response_does_not_repeat_the_post_or_use_a_save_rpc(failure):
    def response(request, body):
        if failure == "timeout":
            raise httpx.ReadTimeout("Response lost after possible check insertion", request=request)
        if failure == "http_error":
            return httpx.Response(503, json={"message": "Unavailable"})
        return httpx.Response(200, content=b"{invalid-json")
    store, requests = transport(response)
    with pytest.raises(PersistentStoreFailure):
        record(store)
    assert len(requests) == 1
    assert requests[0].method == "POST" and requests[0].url.path.endswith("/record_onflows_management_check")


def check_row(sequence=100, revision=652):
    return {"id": str(UUID(int=sequence, version=4)), "sequence": sequence, "checked_revision": revision,
            "operation": "CHECK_NO_CHANGE", "recorded_at": (
                datetime(2026, 10, 19, 13, tzinfo=timezone.utc)-timedelta(seconds=100-sequence)).isoformat(),
            "reason": "Входните данни са непроменени; тренировъчният план е запазен."}


@pytest.mark.parametrize("before", [None, 101])
def test_history_read_is_read_only_bounded_and_scoped_to_the_exact_athlete(before):
    expected = {"checks": [check_row(100), check_row(99)], "next_before": 99}
    store, requests = transport(expected)
    original = deepcopy(expected)
    actual = store.checks(ALIAS, before=before, limit=2)
    assert actual == expected == original
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET" and request.url.path == "/rest/v1/rpc/read_onflows_management_checks"
    assert not request.content
    assert dict(request.url.params) == {"p_alias": ALIAS, "p_limit": "2",
                                       **({"p_before": str(before)} if before is not None else {})}
    assert "p_alias=athlete%26one%2Ftwo%3F" in str(request.url)
    assert not ({"payload", "actor_id", "checkpoint", "plan", "proposal"} & set(actual["checks"][0]))


@pytest.mark.parametrize("rows", [[], [check_row()]])
def test_end_of_history_is_explicit_and_empty_is_not_a_read_failure(rows):
    store, requests = transport({"checks": rows, "next_before": None})
    assert store.checks(ALIAS) == {"checks": rows, "next_before": None}
    assert requests[0].url.params["p_limit"] == "10"


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": 51}, {"limit": -1}, {"limit": True}, {"limit": "10"},
    {"before": 0}, {"before": -1}, {"before": True}, {"before": "99"}, {"before": 1.5},
])
def test_invalid_page_arguments_fail_before_any_database_request(kwargs):
    store, requests = transport({"checks": [], "next_before": None})
    with pytest.raises(ValueError):
        store.checks(ALIAS, **kwargs)
    assert requests == []


def test_maximum_page_size_is_explicitly_bounded():
    rows = [check_row(sequence) for sequence in range(100, 50, -1)]
    store, requests = transport({"checks": rows, "next_before": None})
    assert store.checks(ALIAS, limit=50) == {"checks": rows, "next_before": None}
    assert requests[0].url.params["p_limit"] == "50"


def test_new_checks_inserted_between_pages_do_not_displace_older_entries():
    history = [check_row(sequence) for sequence in (100, 99, 98, 97)]
    def response(request, body):
        before = request.url.params.get("p_before")
        eligible = [row for row in history if before is None or row["sequence"] < int(before)]
        page = eligible[:int(request.url.params["p_limit"])]
        return {"checks": page, "next_before": page[-1]["sequence"] if len(eligible) > len(page) else None}
    store, requests = transport(response)
    first = store.checks(ALIAS, limit=2)
    history.insert(0, check_row(101))
    second = store.checks(ALIAS, before=first["next_before"], limit=2)
    assert [row["sequence"] for row in first["checks"]+second["checks"]] == [100, 99, 98, 97]
    assert first["next_before"] == 99 and second["next_before"] is None
    assert len(requests) == 2


@pytest.mark.parametrize("patch", [
    {"id": None}, {"id": "invalid"}, {"sequence": True}, {"sequence": 0}, {"sequence": "100"},
    {"sequence": 9007199254740992},
    {"checked_revision": True}, {"checked_revision": 0}, {"checked_revision": "652"},
    {"checked_revision": 9007199254740992},
    {"operation": "REVIEW"}, {"operation": None}, {"reason": ""}, {"reason": None},
    {"reason": " "}, {"reason": "x"*501},
    {"recorded_at": "invalid"}, {"recorded_at": "2026-10-19T13:00:00"},
    {"payload": {"plan": "must not be exposed"}}, {"actor_id": str(ACTOR)},
])
def test_malformed_history_entry_is_not_returned_as_valid_evidence(patch):
    store, requests = transport({"checks": [{**check_row(), **patch}], "next_before": None})
    with pytest.raises(PersistentStoreFailure):
        store.checks(ALIAS)
    assert len(requests) == 1


@pytest.mark.parametrize("value", [
    None, [], {}, {"checks": None, "next_before": None}, {"checks": [], "next_before": True},
    {"checks": [], "next_before": 0}, {"checks": [], "next_before": 99},
    {"checks": [check_row()], "next_before": "100"},
    {"checks": [check_row()], "next_before": None, "payload": {}},
])
def test_malformed_history_page_is_a_store_failure(value):
    store, requests = transport(value)
    with pytest.raises(PersistentStoreFailure):
        store.checks(ALIAS)
    assert len(requests) == 1


@pytest.mark.parametrize("rows,next_before,before", [
    ([check_row(100), check_row(100)], None, None),
    ([check_row(99), check_row(100)], None, None),
    ([check_row(100), check_row(99), check_row(98)], None, None),
    ([check_row(100), check_row(99)], 98, None),
    ([check_row(100), check_row(99)], 99, 100),
    ([check_row(100), {**check_row(99), "id": check_row(100)["id"]}], None, None),
    ([{**check_row(100), "id": "abcdefab-cdef-4def-8def-abcdefabcdef"},
      {**check_row(99), "id": "ABCDEFAB-CDEF-4DEF-8DEF-ABCDEFABCDEF"}], None, None),
])
def test_history_cannot_duplicate_skip_or_reverse_the_cursor(rows, next_before, before):
    store, requests = transport({"checks": rows, "next_before": next_before})
    with pytest.raises(PersistentStoreFailure):
        store.checks(ALIAS, before=before, limit=2)
    assert len(requests) == 1


def test_history_read_failure_does_not_appear_as_an_empty_history():
    store, requests = transport(httpx.Response(503, json={"message": "Unavailable"}))
    with pytest.raises(PersistentStoreFailure):
        store.checks(ALIAS)
    assert len(requests) == 1 and requests[0].method == "GET"


def test_check_resources_use_fixed_metric_labels_without_identifiers_or_payloads():
    def response(request, body):
        return acknowledgement(body) if request.method == "POST" else {"checks": [], "next_before": None}
    store, requests = transport(response)
    metrics = StoreMetrics()
    token = current_metrics.set(metrics)
    try:
        receipt = record(store)
        assert store.checks(ALIAS, before=77) == {"checks": [], "next_before": None}
    finally:
        current_metrics.reset(token)
    assert metrics.calls == len(requests) == 2
    assert set(metrics.resources) == {"POST:unchanged_check", "GET:check_history"}
    detail = metrics.details()
    assert ALIAS not in detail and str(ACTOR) not in detail and receipt["check_id"] not in detail
    assert CHECKPOINT["generation_id"] not in detail and "p_before" not in detail
