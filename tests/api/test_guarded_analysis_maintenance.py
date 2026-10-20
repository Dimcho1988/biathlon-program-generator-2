from __future__ import annotations

from copy import deepcopy
import logging
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from apps.api.sync_worker import _run_optional_housekeeping, run_worker
from tests.api.test_supabase_generation_rpc_contract import (
    ALIAS,
    ENCRYPTION_KEY,
    SupabaseHTTPHarness,
    repository,
    rpc_path,
)
from tests.api.test_sync_worker import NOW, PIPELINES, Repository, claim


COUNTS = {
    "deleted_generations": 2,
    "deleted_activity_rows": 15,
    "deleted_jobs": 1,
    "deleted_shadow_runs": 3,
    "deleted_canonical_runs": 4,
    "deleted_model_inputs": 5,
    "deleted_catalog_rows": 6,
}
RPC = rpc_path("maintain_onflows_analysis_history")


@pytest.mark.parametrize("counts", [COUNTS, dict.fromkeys(COUNTS, 0)])
def test_guarded_maintenance_sends_only_alias_in_one_post(counts):
    harness = SupabaseHTTPHarness()
    harness.respond(RPC, [counts])

    assert repository(harness).maintain_analysis_history(athlete_alias=ALIAS) == counts
    assert len(harness.requests) == 1
    request = harness.requests[0]
    assert (request["method"], request["path"], request["query"], request["json"]) == (
        "POST", RPC, "", {"p_alias": ALIAS}
    )


@pytest.mark.parametrize("key", COUNTS)
@pytest.mark.parametrize("invalid", [True, -1, None])
def test_guarded_maintenance_rejects_invalid_or_missing_each_count(key, invalid):
    harness = SupabaseHTTPHarness()
    counts = dict(COUNTS)
    if invalid is None:
        del counts[key]
    else:
        counts[key] = invalid
    harness.respond(RPC, [counts])

    with pytest.raises(PersistentStoreFailure, match="retention response"):
        repository(harness).maintain_analysis_history(athlete_alias=ALIAS)
    assert len(harness.requests) == 1


@pytest.mark.parametrize("response", [
    [], None, {}, [None],
    [{**COUNTS, "deleted_jobs": "1"}],
    [{**COUNTS, "deleted_jobs": 1.0}],
])
def test_guarded_maintenance_rejects_empty_or_malformed_rpc_result(response):
    harness = SupabaseHTTPHarness()
    harness.respond(RPC, response)

    with pytest.raises(PersistentStoreFailure):
        repository(harness).maintain_analysis_history(athlete_alias=ALIAS)
    assert len(harness.requests) == 1


@pytest.mark.parametrize("status,payload", [
    (401, {"code": "PGRST303", "message": "JWT expired"}),
    (409, {"code": "PT409", "message": "Historical references changed"}),
    (503, {"code": "PGRST000", "message": "Unavailable"}),
])
def test_guarded_maintenance_does_not_retry_post_on_error(status, payload):
    harness = SupabaseHTTPHarness()
    harness.respond(RPC, payload, status=status)
    # A second response would make an accidental retry visible as a false success.
    harness.respond(RPC, [COUNTS])

    with pytest.raises(PersistentStoreFailure):
        repository(harness).maintain_analysis_history(athlete_alias=ALIAS)
    assert len(harness.requests) == 1


def test_guarded_maintenance_does_not_retry_ambiguous_network_failure():
    requests = []

    def timeout(request):
        requests.append(request)
        raise httpx.ReadTimeout("private network detail", request=request)

    store = SupabasePilotRepository(
        supabase_url="https://project.supabase.co",
        secret_key="sb_secret_server-key",
        encryption_key=ENCRYPTION_KEY,
        client=httpx.Client(transport=httpx.MockTransport(timeout)),
    )
    with pytest.raises(PersistentStoreFailure):
        store.maintain_analysis_history(athlete_alias=ALIAS)
    assert len(requests) == 1
    assert requests[0].method == "POST" and requests[0].url.path == RPC


def test_maintenance_timeout_exceeds_sql_budget_without_changing_other_requests():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=[COUNTS])

    store = SupabasePilotRepository(
        supabase_url="https://project.supabase.co",
        secret_key="sb_secret_server-key",
        encryption_key=ENCRYPTION_KEY,
        client=httpx.Client(
            transport=httpx.MockTransport(respond),
            timeout=httpx.Timeout(15.0, connect=5.0),
        ),
    )
    assert store.maintain_analysis_history(athlete_alias=ALIAS) == COUNTS
    store._rpc_row("ordinary_read", {})
    assert requests[0].extensions["timeout"] == {
        "connect": 5.0, "read": 35.0, "write": 35.0, "pool": 35.0,
    }
    assert requests[1].extensions["timeout"]["read"] == 15.0


@pytest.mark.parametrize("fail", [False, True])
def test_worker_prefers_guarded_capability_between_jobs_with_existing_cadence(fail, caplog):
    stopping = Event()

    class GuardedRepository(Repository):
        def __init__(self):
            super().__init__()
            self.guarded_calls = []
            self.legacy_calls = []

        def claim_sync_job(self, **values):
            del values
            if self.claims:
                return self.claims.pop(0)
            stopping.set()
            return None

        def maintain_analysis_history(self, **values):
            # First call follows completed activation; the second follows the
            # third activation exactly six hours after the first opportunity.
            assert len(self.activate_calls) == (1 if not self.guarded_calls else 3)
            self.guarded_calls.append(deepcopy(values))
            if fail:
                raise PersistentStoreFailure("private database detail")
            return dict(COUNTS)

        def prune_analysis_generations(self, **values):
            self.legacy_calls.append(values)
            raise AssertionError("guarded capability must never fall back")

    repo = GuardedRepository()
    repo.claims = [
        claim(job_id=f"job-{i}", generation_id=f"generation-{i}")
        for i in (1, 2, 3)
    ]
    clock_values = iter((0.0, 21_599.0, 21_600.0))
    with caplog.at_level(logging.INFO):
        assert run_worker(
            repo, worker_id="test-worker", lease_seconds=30,
            poll_seconds=0.001, max_idle_seconds=0.001, stop_event=stopping,
            pipelines=PIPELINES, clock=lambda: NOW,
            monotonic_clock=lambda: next(clock_values),
        ) == 0
    assert len(repo.activate_calls) == 3
    assert repo.guarded_calls == [{"athlete_alias": "athlete-one"}] * 2
    assert repo.legacy_calls == []
    assert "private database detail" not in caplog.text
    if fail:
        assert caplog.text.count(
            "sync_worker_housekeeping_failed error_type=PersistentStoreFailure"
        ) == 2
    else:
        assert caplog.text.count("sync_worker_housekeeping_finished deleted_generations=2") == 2
        for key, count in COUNTS.items():
            assert f"{key}={count}" in caplog.text


def test_noncallable_guarded_capability_keeps_legacy_policy_unchanged():
    calls = []
    repo = SimpleNamespace(
        maintain_analysis_history=None,
        prune_analysis_generations=lambda **values: calls.append(values) or COUNTS,
    )
    _run_optional_housekeeping(repo, ALIAS)
    assert calls == [{
        "athlete_alias": ALIAS,
        "keep_superseded": 5,
        "terminal_older_than_days": 30,
        "batch_limit": 100,
    }]


@pytest.mark.parametrize("result", [
    None,
    {**COUNTS, "deleted_jobs": True},
    {**COUNTS, "deleted_shadow_runs": -1},
])
def test_guarded_bad_result_is_isolated_without_legacy_fallback(result, caplog):
    calls = []
    repo = SimpleNamespace(
        maintain_analysis_history=lambda **values: result,
        prune_analysis_generations=lambda **values: calls.append(values),
    )
    _run_optional_housekeeping(repo, ALIAS)
    assert calls == []
    assert "sync_worker_housekeeping_failed error_type=SyncContractError" in caplog.text
