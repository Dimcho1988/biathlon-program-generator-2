from __future__ import annotations

import base64
from collections import defaultdict, deque
from copy import deepcopy
from datetime import date
import hashlib
import json
from typing import Any

import httpx
import pytest

from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from apps.api.sync_contracts import ClaimedSyncJob


ALIAS = "ath-rpc-contract"
ACTIVITY_REF = "act_" + "1" * 32
JOB_ID = "11111111-1111-4111-8111-111111111111"
GENERATION_ID = "22222222-2222-4222-8222-222222222222"
BASE_GENERATION_ID = "33333333-3333-4333-8333-333333333333"
LEASE_TOKEN = "44444444-4444-4444-8444-444444444444"
INPUT_KEY = "5" * 64
CANONICAL_RUN_KEY = "6" * 64
SHADOW_RUN_KEY = "7" * 64
SNAPSHOT_HASH = "8" * 64
ACTIVITY_SET_HASH = "9" * 64
ENCRYPTION_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode()


class SupabaseHTTPHarness:
    """Queue deterministic PostgREST responses and capture exact HTTP calls."""

    def __init__(self) -> None:
        self._responses: dict[str, deque[tuple[int, Any]]] = defaultdict(deque)
        self.requests: list[dict[str, Any]] = []

    def respond(self, path: str, payload: Any, *, status: int = 200) -> None:
        self._responses[path].append((status, deepcopy(payload)))

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = (
            json.loads(request.content.decode("utf-8"))
            if request.content
            else None
        )
        captured_request = {
            "method": request.method,
            "path": request.url.path,
            "query": request.url.query.decode("ascii"),
            "json": body,
            "prefer": request.headers.get("Prefer"),
        }
        if request.url.path == rpc_path("publish_onflows_activity_shadow"):
            captured_request["timeout"] = request.extensions.get("timeout")
        self.requests.append(captured_request)
        queued = self._responses.get(request.url.path)
        if not queued:
            raise AssertionError(
                f"Unexpected HTTP request: {request.method} {request.url}"
            )
        status, payload = queued.popleft()
        return httpx.Response(status, json=payload)


def repository(harness: SupabaseHTTPHarness) -> SupabasePilotRepository:
    client = httpx.Client(transport=httpx.MockTransport(harness.handler))
    return SupabasePilotRepository(
        supabase_url="https://project.supabase.co",
        secret_key="sb_secret_server-key",
        encryption_key=ENCRYPTION_KEY,
        client=client,
        generation_reads=True,
    )


def rpc_path(name: str) -> str:
    return f"/rest/v1/rpc/{name}"


def test_shadow_publish_allows_large_atomic_payload_to_finish() -> None:
    harness = SupabaseHTTPHarness()
    harness.respond(rpc_path("publish_onflows_activity_shadow"), None)
    repo = repository(harness)

    run_key = repo.publish_activity_shadow(
        athlete_alias=ALIAS,
        activity_ref=ACTIVITY_REF,
        input_payload={
            "schema_version": "activity-model-input-v1",
            "input_hash": "a" * 64,
            "samples": [{"hr_raw_bpm": 140.0}],
        },
        derived_payload={
            "schema_version": "activity-shadow-derived-v1",
            "result_hash": "b" * 64,
            "vflat_model_version": "vflat_b65_dynamic_v3",
            "vflat_config_version": "vflat_b65_config_v3",
            "hrmod_model_version": "hrmod_mirror_area_shift_v6",
            "hrmod_config_version": "hrmod_config_v6",
            "terrain_model_version": "terrain_segments_v1",
        },
    )

    assert len(run_key) == 64
    assert len(harness.requests) == 1
    captured = harness.requests[0]
    assert captured["method"] == "POST"
    assert captured["path"] == rpc_path("publish_onflows_activity_shadow")
    assert captured["prefer"] == "return=minimal"
    assert captured["timeout"] == {
        "connect": 5.0,
        "read": 60.0,
        "write": 60.0,
        "pool": 60.0,
    }


def pointer_row() -> dict[str, Any]:
    return {
        "generation_id": GENERATION_ID,
        "revision": 7,
        "analysis_as_of": "2026-08-27",
        "activated_at": "2026-08-27T06:30:00+00:00",
        "catalog_payload": {
            "activity_ref": ACTIVITY_REF,
            "start_at_utc": "2026-08-27T05:00:00+00:00",
            "local_date": "2026-08-27",
            "latest_canonical_run_key": "a" * 64,
            "latest_shadow_run_key": "b" * 64,
        },
        "input_key": INPUT_KEY,
        "canonical_run_key": CANONICAL_RUN_KEY,
        "shadow_run_key": SHADOW_RUN_KEY,
        "previous_activity_ref": None,
        "next_activity_ref": "act_" + "2" * 32,
    }


def test_active_analysis_and_calendar_use_exact_coherent_rpc_contracts():
    harness = SupabaseHTTPHarness()
    snapshot = {"schema_version": "athlete-snapshot-v1", "marker": "active"}
    activity = {"activity_ref": ACTIVITY_REF, "marker": "pinned"}
    harness.respond(
        rpc_path("active_onflows_analysis"),
        [
            {
                "generation_id": GENERATION_ID,
                "revision": 7,
                "analysis_as_of": "2026-08-27",
                "activated_at": "2026-08-27T06:30:00+00:00",
                "snapshot_payload": snapshot,
            }
        ],
    )
    harness.respond(
        rpc_path("active_onflows_activity_calendar"),
        [
            {
                "generation_id": GENERATION_ID,
                "revision": 7,
                "analysis_as_of": "2026-08-27",
                "activated_at": "2026-08-27T06:30:00+00:00",
                "snapshot_payload": snapshot,
                "activities": [activity],
            }
        ],
    )
    store = repository(harness)

    analysis = store.active_analysis(ALIAS)
    calendar = store.active_activity_calendar(
        ALIAS, date(2026, 8, 1), date(2026, 8, 27)
    )

    assert analysis == {
        "generation_id": GENERATION_ID,
        "revision": 7,
        "analysis_as_of": "2026-08-27",
        "activated_at": "2026-08-27T06:30:00+00:00",
        "snapshot_payload": snapshot,
    }
    assert calendar is not None
    assert calendar["generation_id"] == GENERATION_ID
    assert calendar["revision"] == 7
    assert calendar["snapshot_payload"] == snapshot
    assert calendar["activities"] == [activity]
    assert harness.requests == [
        {
            "method": "POST",
            "path": rpc_path("active_onflows_analysis"),
            "query": "",
            "json": {"p_athlete_alias": ALIAS},
            "prefer": None,
        },
        {
            "method": "POST",
            "path": rpc_path("active_onflows_activity_calendar"),
            "query": "",
            "json": {
                "p_athlete_alias": ALIAS,
                "p_period_start": "2026-08-01",
                "p_period_end": "2026-08-27",
            },
            "prefer": None,
        },
    ]


@pytest.mark.parametrize(
    ("rpc_name", "payload", "call"),
    [
        (
            "active_onflows_analysis",
            [{"generation_id": GENERATION_ID, "revision": True,
              "snapshot_payload": {}}],
            lambda store: store.active_analysis(ALIAS),
        ),
        (
            "active_onflows_activity_calendar",
            [{"generation_id": GENERATION_ID, "revision": 1,
              "snapshot_payload": {}, "activities": ["not-an-object"]}],
            lambda store: store.active_activity_calendar(
                ALIAS, date(2026, 8, 1), date(2026, 8, 27)
            ),
        ),
    ],
)
def test_active_aggregate_rpcs_reject_malformed_rows(rpc_name, payload, call):
    harness = SupabaseHTTPHarness()
    harness.respond(rpc_path(rpc_name), payload)

    with pytest.raises(PersistentStoreFailure, match="active"):
        call(repository(harness))


def test_detail_series_shadow_and_index_are_pinned_to_active_generation_keys():
    harness = SupabaseHTTPHarness()
    pointer_rpc = rpc_path("active_onflows_activity")
    for _ in range(3):
        harness.respond(pointer_rpc, [pointer_row()])
    harness.respond(
        "/rest/v1/onflows_activity_model_inputs",
        [
            {
                "input_payload": {
                    "samples": [
                        {
                            "timestamp": "2026-08-27T05:00:00+00:00",
                            "elapsed_s": 0,
                            "hr_raw_bpm": 141,
                            "speed_raw_kmh": 12.3,
                            "quality_flags": [],
                        }
                    ]
                }
            }
        ],
    )
    harness.respond(
        "/rest/v1/onflows_activity_derived_runs",
        [{"result_payload": {"result_hash": "c" * 64, "marker": "pinned"}}],
    )
    harness.respond(
        rpc_path("active_onflows_activity_shadow_index"),
        [
            {
                "activity_ref": ACTIVITY_REF,
                "run_key": SHADOW_RUN_KEY,
                "vflat_model_version": "vflat-v1",
                "hrmod_model_version": "hrmod-v1",
                "terrain_model_version": "terrain-v1",
                "zone_summary": [{"zone_name": "Z1"}],
            }
        ],
    )
    store = repository(harness)

    detail = store.activity_detail(ALIAS, ACTIVITY_REF)
    series = store.activity_series(ALIAS, ACTIVITY_REF)
    shadow = store.activity_shadow(ALIAS, ACTIVITY_REF)
    shadow_index = store.activity_shadow_index(ALIAS)

    assert detail is not None
    assert detail["latest_canonical_run_key"] == CANONICAL_RUN_KEY
    assert detail["latest_shadow_run_key"] == SHADOW_RUN_KEY
    assert detail["next_activity_ref"] == "act_" + "2" * 32
    assert series is not None and series["returned_sample_count"] == 1
    assert shadow == {"result_hash": "c" * 64, "marker": "pinned"}
    assert shadow_index == (
        {
            "activity_ref": ACTIVITY_REF,
            "run_key": SHADOW_RUN_KEY,
            "vflat_model_version": "vflat-v1",
            "hrmod_model_version": "hrmod-v1",
            "terrain_model_version": "terrain-v1",
        },
    )

    pointer_calls = [
        request for request in harness.requests if request["path"] == pointer_rpc
    ]
    assert len(pointer_calls) == 3
    assert all(
        request["json"]
        == {"p_athlete_alias": ALIAS, "p_activity_ref": ACTIVITY_REF}
        for request in pointer_calls
    )
    series_call = next(
        request
        for request in harness.requests
        if request["path"] == "/rest/v1/onflows_activity_model_inputs"
    )
    assert f"input_key=eq.{INPUT_KEY}" in series_call["query"]
    assert f"athlete_alias=eq.{ALIAS}" in series_call["query"]
    assert f"activity_ref=eq.{ACTIVITY_REF}" in series_call["query"]
    assert "order=" not in series_call["query"]
    shadow_call = next(
        request
        for request in harness.requests
        if request["path"] == "/rest/v1/onflows_activity_derived_runs"
    )
    assert f"run_key=eq.{SHADOW_RUN_KEY}" in shadow_call["query"]
    assert "order=" not in shadow_call["query"]
    index_call = harness.requests[-1]
    assert index_call["path"] == rpc_path(
        "active_onflows_activity_shadow_index"
    )
    assert index_call["json"] == {"p_athlete_alias": ALIAS}


def test_active_activity_pointer_rejects_malformed_pinned_hashes():
    harness = SupabaseHTTPHarness()
    malformed = pointer_row()
    malformed["input_key"] = "short"
    harness.respond(rpc_path("active_onflows_activity"), [malformed])

    with pytest.raises(PersistentStoreFailure, match="pointer"):
        repository(harness).activity_detail(ALIAS, ACTIVITY_REF)


def test_active_activity_view_returns_catalog_series_and_shadow_from_one_rpc():
    harness = SupabaseHTTPHarness()
    view = {
        **pointer_row(),
        "series_payload": {
            "schema_version": "activity-model-input-v1",
            "samples": [{"timestamp": "2026-08-27T05:00:00+00:00"}],
        },
        "shadow_payload": {
            "schema_version": "activity-derived-v1",
            "result_hash": "c" * 64,
        },
    }
    harness.respond(rpc_path("active_onflows_activity_view"), [view])
    store = repository(harness)

    result = store.active_activity_view(ALIAS, ACTIVITY_REF)

    assert result is not None
    assert result["generation_id"] == GENERATION_ID
    assert result["input_key"] == INPUT_KEY
    assert result["series_payload"] == view["series_payload"]
    assert result["shadow_run_key"] == SHADOW_RUN_KEY
    assert result["shadow_payload"] == view["shadow_payload"]
    assert harness.requests == [
        {
            "method": "POST",
            "path": rpc_path("active_onflows_activity_view"),
            "query": "",
            "json": {
                "p_athlete_alias": ALIAS,
                "p_activity_ref": ACTIVITY_REF,
            },
            "prefer": None,
        }
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("series_payload", None),
        ("shadow_payload", None),
        ("canonical_run_key", "short"),
    ],
)
def test_active_activity_view_rejects_incomplete_generation_pins(field, value):
    harness = SupabaseHTTPHarness()
    view = {
        **pointer_row(),
        "series_payload": {"samples": []},
        "shadow_payload": {"result_hash": "c" * 64},
    }
    view[field] = value
    harness.respond(rpc_path("active_onflows_activity_view"), [view])

    with pytest.raises(PersistentStoreFailure, match="activity view"):
        repository(harness).active_activity_view(ALIAS, ACTIVITY_REF)


def test_enqueue_and_sync_state_use_exact_rpc_arguments_and_parse_counters():
    harness = SupabaseHTTPHarness()
    harness.respond(
        rpc_path("enqueue_onflows_sync_job"),
        [
            {
                "job_id": JOB_ID,
                "athlete_alias": ALIAS,
                "job_kind": "FULL_SYNC",
                "status": "QUEUED",
                "request_sequence": 4,
                "deduplicated": False,
            }
        ],
    )
    harness.respond(
        rpc_path("onflows_sync_state"),
        [
            {
                "athlete_alias": ALIAS,
                "active_generation_id": GENERATION_ID,
                "active_revision": 3,
                "request_sequence": 4,
                "job_id": JOB_ID,
                "job_kind": "FULL_SYNC",
                "status": "QUEUED",
                "pending_job_count": 1,
            }
        ],
    )
    store = repository(harness)

    job = store.enqueue_sync_job(
        athlete_alias=ALIAS,
        job_kind="FULL_SYNC",
        idempotency_key="d" * 64,
        request_payload={"as_of": "2026-08-27"},
    )
    state = store.sync_state(ALIAS)

    assert job["job_id"] == JOB_ID and job["request_sequence"] == 4
    assert state["active_generation_id"] == GENERATION_ID
    assert state["pending_job_count"] == 1
    assert harness.requests[0]["path"] == rpc_path("enqueue_onflows_sync_job")
    assert harness.requests[0]["json"] == {
        "p_athlete_alias": ALIAS,
        "p_job_kind": "FULL_SYNC",
        "p_idempotency_key": "d" * 64,
        "p_request_payload": {"as_of": "2026-08-27"},
    }
    assert harness.requests[1]["path"] == rpc_path("onflows_sync_state")
    assert harness.requests[1]["json"] == {"p_athlete_alias": ALIAS}


def test_sync_state_rejects_boolean_revision_or_sequence():
    harness = SupabaseHTTPHarness()
    harness.respond(
        rpc_path("onflows_sync_state"),
        [{"active_revision": True, "request_sequence": 1}],
    )
    with pytest.raises(PersistentStoreFailure, match="sync state"):
        repository(harness).sync_state(ALIAS)


def test_claim_and_renew_use_exact_fencing_contracts():
    harness = SupabaseHTTPHarness()
    claim = {
        "job_id": JOB_ID,
        "athlete_alias": ALIAS,
        "job_kind": "FULL_SYNC",
        "request_payload": {"as_of": "2026-08-27"},
        "request_sequence": 4,
        "generation_id": GENERATION_ID,
        "attempt_no": 1,
        "base_generation_id": BASE_GENERATION_ID,
        "base_revision": 3,
        "base_activity_set_hash": ACTIVITY_SET_HASH,
        "lease_token": LEASE_TOKEN,
        "lease_expires_at": "2026-08-27T06:35:00+00:00",
    }
    harness.respond(rpc_path("claim_onflows_sync_job"), [claim])
    harness.respond(rpc_path("renew_onflows_sync_lease"), True)
    store = repository(harness)

    claimed = store.claim_sync_job(worker_id="worker-one", lease_seconds=300)
    assert claimed is not None
    parsed = ClaimedSyncJob.from_mapping(claimed)
    assert parsed.generation_id == GENERATION_ID
    assert parsed.base_generation_id == BASE_GENERATION_ID
    assert parsed.base_activity_set_hash == ACTIVITY_SET_HASH
    assert store.renew_sync_lease(
        job_id=JOB_ID,
        generation_id=GENERATION_ID,
        lease_token=LEASE_TOKEN,
        lease_seconds=300,
    ) is True
    assert harness.requests[0] == {
        "method": "POST",
        "path": rpc_path("claim_onflows_sync_job"),
        "query": "",
        "json": {"p_worker_id": "worker-one", "p_lease_seconds": 300},
        "prefer": None,
    }
    assert harness.requests[1]["path"] == rpc_path(
        "renew_onflows_sync_lease"
    )
    assert harness.requests[1]["json"] == {
        "p_job_id": JOB_ID,
        "p_generation_id": GENERATION_ID,
        "p_lease_token": LEASE_TOKEN,
        "p_lease_seconds": 300,
    }


def test_claim_empty_queue_and_renew_non_boolean_are_strict():
    harness = SupabaseHTTPHarness()
    harness.respond(rpc_path("claim_onflows_sync_job"), [])
    harness.respond(rpc_path("renew_onflows_sync_lease"), 1)
    store = repository(harness)

    assert store.claim_sync_job(worker_id="worker-one") is None
    with pytest.raises(PersistentStoreFailure, match="lease response"):
        store.renew_sync_lease(
            job_id=JOB_ID,
            generation_id=GENERATION_ID,
            lease_token=LEASE_TOKEN,
        )


def test_stage_full_generation_sends_normalized_exact_activity_pins():
    harness = SupabaseHTTPHarness()
    harness.respond(
        rpc_path("stage_onflows_analysis_generation"),
        [{"outcome": "READY", "activity_count": 1}],
    )
    store = repository(harness)
    snapshot = {"schema_version": "athlete-snapshot-v1", "marker": "full"}
    catalog = {
        "activity_ref": ACTIVITY_REF,
        "start_at_utc": "2026-08-27T05:00:00+00:00",
        "local_date": "2026-08-27",
        "latest_canonical_run_key": CANONICAL_RUN_KEY,
        "latest_shadow_run_key": SHADOW_RUN_KEY,
    }

    result = store.stage_analysis_generation(
        job_id=JOB_ID,
        generation_id=GENERATION_ID,
        lease_token=LEASE_TOKEN,
        snapshot_payload=snapshot,
        snapshot_hash=SNAPSHOT_HASH,
        period_start=date(2026, 7, 18),
        period_end=date(2026, 8, 27),
        as_of=date(2026, 8, 27),
        provenance={"activity_set_hash": ACTIVITY_SET_HASH},
        activities=[
            {
                "activity_ref": ACTIVITY_REF,
                "catalog_payload": catalog,
                "input_key": INPUT_KEY,
            }
        ],
        inherit_activities=False,
    )

    assert result == {"outcome": "READY", "activity_count": 1}
    call = harness.requests[0]
    assert call["path"] == rpc_path("stage_onflows_analysis_generation")
    expected_payload_hash = hashlib.sha256(
        json.dumps(
            catalog,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert call["json"] == {
        "p_job_id": JOB_ID,
        "p_generation_id": GENERATION_ID,
        "p_lease_token": LEASE_TOKEN,
        "p_snapshot_payload": snapshot,
        "p_snapshot_hash": SNAPSHOT_HASH,
        "p_period_start": "2026-07-18",
        "p_period_end": "2026-08-27",
        "p_as_of": "2026-08-27",
        "p_provenance": {"activity_set_hash": ACTIVITY_SET_HASH},
        "p_activities": [
            {
                "activity_ref": ACTIVITY_REF,
                "start_at_utc": "2026-08-27T05:00:00+00:00",
                "local_date": "2026-08-27",
                "catalog_payload": catalog,
                "payload_hash": expected_payload_hash,
                "input_key": INPUT_KEY,
                "canonical_run_key": CANONICAL_RUN_KEY,
                "shadow_run_key": SHADOW_RUN_KEY,
            }
        ],
        "p_inherit_activities": False,
    }


def test_stage_patch_generation_explicitly_inherits_base_activity_set():
    harness = SupabaseHTTPHarness()
    harness.respond(
        rpc_path("stage_onflows_analysis_generation"),
        [{"outcome": "ALREADY_READY", "activity_count": 12}],
    )
    store = repository(harness)

    result = store.stage_analysis_generation(
        job_id=JOB_ID,
        generation_id=GENERATION_ID,
        lease_token=LEASE_TOKEN,
        snapshot_payload={"schema_version": "athlete-snapshot-v1"},
        snapshot_hash=SNAPSHOT_HASH,
        period_start=date(2026, 7, 18),
        period_end=date(2026, 8, 27),
        as_of=date(2026, 8, 27),
        provenance={"activity_set_hash": ACTIVITY_SET_HASH},
        activities=[],
        inherit_activities=True,
    )

    assert result["activity_count"] == 12
    payload = harness.requests[0]["json"]
    assert payload["p_activities"] == []
    assert payload["p_inherit_activities"] is True
    assert payload["p_generation_id"] == GENERATION_ID


def test_activate_and_fail_use_exact_fenced_rpc_contracts():
    harness = SupabaseHTTPHarness()
    harness.respond(
        rpc_path("activate_onflows_analysis_generation"),
        [
            {
                "outcome": "ACTIVATED",
                "active_generation_id": GENERATION_ID,
                "active_revision": 8,
            }
        ],
    )
    harness.respond(
        rpc_path("fail_onflows_sync_job"),
        [{"status": "RETRY_WAIT", "available_at": "2026-08-27T08:30:00Z"}],
    )
    store = repository(harness)

    activated = store.activate_analysis_generation(
        job_id=JOB_ID,
        generation_id=GENERATION_ID,
        lease_token=LEASE_TOKEN,
    )
    failed = store.fail_sync_job(
        job_id=JOB_ID,
        generation_id=GENERATION_ID,
        lease_token=LEASE_TOKEN,
        error_code="PROVIDER_RATE_LIMITED",
        retryable=True,
        retry_after_seconds=7200,
    )

    assert activated["active_revision"] == 8
    assert failed["status"] == "RETRY_WAIT"
    assert harness.requests[0]["path"] == rpc_path(
        "activate_onflows_analysis_generation"
    )
    assert harness.requests[0]["json"] == {
        "p_job_id": JOB_ID,
        "p_generation_id": GENERATION_ID,
        "p_lease_token": LEASE_TOKEN,
    }
    assert harness.requests[1]["path"] == rpc_path("fail_onflows_sync_job")
    assert harness.requests[1]["json"] == {
        "p_job_id": JOB_ID,
        "p_generation_id": GENERATION_ID,
        "p_lease_token": LEASE_TOKEN,
        "p_error_code": "PROVIDER_RATE_LIMITED",
        "p_retryable": True,
        "p_retry_after_seconds": 7200,
    }


@pytest.mark.parametrize(
    ("rpc_name", "response", "call"),
    [
        (
            "stage_onflows_analysis_generation",
            [{"outcome": "WRITING", "activity_count": 0}],
            lambda store: store.stage_analysis_generation(
                job_id=JOB_ID,
                generation_id=GENERATION_ID,
                lease_token=LEASE_TOKEN,
                snapshot_payload={"schema_version": "athlete-snapshot-v1"},
                snapshot_hash=SNAPSHOT_HASH,
                period_start=date(2026, 8, 1),
                period_end=date(2026, 8, 27),
                as_of=date(2026, 8, 27),
                provenance={},
                activities=[],
            ),
        ),
        (
            "activate_onflows_analysis_generation",
            [{"outcome": "UNKNOWN"}],
            lambda store: store.activate_analysis_generation(
                job_id=JOB_ID,
                generation_id=GENERATION_ID,
                lease_token=LEASE_TOKEN,
            ),
        ),
        (
            "fail_onflows_sync_job",
            [{"status": "UNKNOWN"}],
            lambda store: store.fail_sync_job(
                job_id=JOB_ID,
                generation_id=GENERATION_ID,
                lease_token=LEASE_TOKEN,
                error_code="FAILED",
                retryable=False,
            ),
        ),
    ],
)
def test_generation_write_rpcs_reject_unknown_outcomes(rpc_name, response, call):
    harness = SupabaseHTTPHarness()
    harness.respond(rpc_path(rpc_name), response)
    with pytest.raises(PersistentStoreFailure):
        call(repository(harness))


def test_prune_uses_bounded_interval_rpc_shape_and_strict_counts():
    harness = SupabaseHTTPHarness()
    harness.respond(
        rpc_path("prune_onflows_analysis_generations"),
        [
            {
                "deleted_generations": 2,
                "deleted_activity_rows": 15,
                "deleted_jobs": 2,
                "deleted_shadow_runs": 4,
                "deleted_canonical_runs": 3,
                "deleted_model_inputs": 4,
                "deleted_catalog_rows": 1,
            }
        ],
    )
    store = repository(harness)

    result = store.prune_analysis_generations(
        athlete_alias=ALIAS,
        keep_superseded=7,
        terminal_older_than_days=45,
        batch_limit=200,
    )

    assert result == {
        "deleted_generations": 2,
        "deleted_activity_rows": 15,
        "deleted_jobs": 2,
        "deleted_shadow_runs": 4,
        "deleted_canonical_runs": 3,
        "deleted_model_inputs": 4,
        "deleted_catalog_rows": 1,
    }
    assert harness.requests[0]["path"] == rpc_path(
        "prune_onflows_analysis_generations"
    )
    assert harness.requests[0]["json"] == {
        "p_athlete_alias": ALIAS,
        "p_keep_superseded": 7,
        "p_terminal_older_than": "45 days",
        "p_batch_limit": 200,
    }

    malformed = SupabaseHTTPHarness()
    malformed.respond(
        rpc_path("prune_onflows_analysis_generations"),
        [
            {
                "deleted_generations": True,
                "deleted_activity_rows": 0,
                "deleted_jobs": 0,
                "deleted_shadow_runs": 0,
                "deleted_canonical_runs": 0,
                "deleted_model_inputs": 0,
                "deleted_catalog_rows": 0,
            }
        ],
    )
    with pytest.raises(PersistentStoreFailure, match="retention response"):
        repository(malformed).prune_analysis_generations(athlete_alias=ALIAS)


def test_insert_only_canonical_store_uses_exact_rpc_and_does_not_publish_pointer():
    harness = SupabaseHTTPHarness()
    harness.respond(rpc_path("store_onflows_canonical_activity_result"), None)
    store = repository(harness)
    result_payload = {
        "schema_version": "activity-canonical-summary-v2",
        "model_version": "canonical-v1",
        "canonical_training_load": 42.5,
    }
    scientific_input_hash = "e" * 64

    run_key = store.store_canonical_activity_result(
        athlete_alias=ALIAS,
        activity_ref=ACTIVITY_REF,
        scientific_input_hash=scientific_input_hash,
        result_payload=result_payload,
    )

    result_hash = hashlib.sha256(
        json.dumps(
            result_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    expected_run_key = hashlib.sha256(
        (
            f"{ALIAS}|{ACTIVITY_REF}|{scientific_input_hash}|{result_hash}"
        ).encode("utf-8")
    ).hexdigest()
    assert run_key == expected_run_key
    call = harness.requests[0]
    assert call["path"] == rpc_path("store_onflows_canonical_activity_result")
    assert "publish_onflows_canonical_activity_result" not in call["path"]
    assert call["prefer"] == "return=minimal"
    assert call["json"] == {
        "p_run_key": expected_run_key,
        "p_athlete_alias": ALIAS,
        "p_activity_ref": ACTIVITY_REF,
        "p_scientific_input_hash": scientific_input_hash,
        "p_result_hash": result_hash,
        "p_schema_version": "activity-canonical-summary-v2",
        "p_model_version": "canonical-v1",
        "p_result_payload": result_payload,
    }


def test_rpc_rows_reject_non_list_and_non_object_responses():
    for malformed_payload in ({"job_id": JOB_ID}, ["not-an-object"]):
        harness = SupabaseHTTPHarness()
        harness.respond(rpc_path("enqueue_onflows_sync_job"), malformed_payload)
        with pytest.raises(PersistentStoreFailure, match="RPC response"):
            repository(harness).enqueue_sync_job(
                athlete_alias=ALIAS,
                job_kind="FULL_SYNC",
                idempotency_key="f" * 64,
                request_payload={},
            )
