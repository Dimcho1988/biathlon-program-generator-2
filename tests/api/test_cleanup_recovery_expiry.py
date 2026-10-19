from __future__ import annotations

import base64
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from apps.api.sync_worker import run_worker


RPC_PATH = "/rest/v1/rpc/expire_onflows_analysis_cleanup_recovery"


def repository(handler):
    return SupabasePilotRepository(
        supabase_url="https://project.supabase.co",
        secret_key="sb_secret_server-only",
        encryption_key=base64.urlsafe_b64encode(b"x" * 32).decode(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize("limit,count", [(1, 0), (10, 10), (50, 49)])
def test_expiry_uses_one_bounded_global_scalar_rpc(limit, count):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=count)

    repo = repository(handle)
    assert repo.expire_analysis_cleanup_recovery(batch_limit=limit) == count
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == RPC_PATH
    assert requests[0].url.query == b""
    assert json.loads(requests[0].content) == {"p_limit": limit}


def test_expiry_default_batch_is_ten():
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=0)

    assert repository(handle).expire_analysis_cleanup_recovery() == 0
    assert requests == [{"p_limit": 10}]


@pytest.mark.parametrize("limit", [None, True, False, 0, -1, 51, 10.0, "10", [], {}])
def test_expiry_rejects_invalid_limit_before_io(limit):
    def unexpected(request):
        raise AssertionError("Invalid batch limit must not reach the database")

    with pytest.raises(ValueError, match="batch limit"):
        repository(unexpected).expire_analysis_cleanup_recovery(batch_limit=limit)


@pytest.mark.parametrize("payload", [None, True, False, -1, 11, 1.0, "1", [], [1], {}])
def test_expiry_rejects_ambiguous_or_unbounded_rpc_counts(payload):
    repo = repository(lambda request: httpx.Response(200, content=json.dumps(payload)))
    with pytest.raises(PersistentStoreFailure, match="expiry result is invalid"):
        repo.expire_analysis_cleanup_recovery()


@pytest.mark.parametrize("status", [401, 404, 409, 500])
def test_failed_expiry_is_not_retried_or_reported_successful(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"code": "PGRST202", "message": "private detail"})

    with pytest.raises(PersistentStoreFailure) as exc_info:
        repository(handle).expire_analysis_cleanup_recovery()
    assert len(calls) == 1
    assert type(exc_info.value) is PersistentStoreFailure


def test_expiry_timeout_is_not_retried():
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("private detail")

    with pytest.raises(PersistentStoreFailure):
        repository(handle).expire_analysis_cleanup_recovery()
    assert len(calls) == 1


class ControlledStop:
    """Drive idle cycles without wall-clock waits or background threads."""

    def __init__(self, times=(0.0,)):
        self.times = times
        self.index = 0
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def now(self):
        return self.times[self.index]

    def wait(self, delay):
        self.waits.append(delay)
        self.index += 1
        if self.index >= len(self.times):
            self.set()
        return self.is_set()


class IdleRepository:
    def __init__(self, stop, *, result=0, error=None):
        self.stop = stop
        self.result = result
        self.error = error
        self.calls = []
        self.claims = 0

    def claim_sync_job(self, **kwargs):
        self.claims += 1
        return None

    def expire_analysis_cleanup_recovery(self, **kwargs):
        self.calls.append((self.stop.now(), kwargs))
        if self.error is not None:
            raise self.error
        return self.result


def run_idle(repo, stop, **kwargs):
    return run_worker(
        repo,
        worker_id="test-worker",
        stop_event=stop,
        monotonic_clock=stop.now,
        **kwargs,
    )


def test_idle_expiry_covers_inactive_aliases_and_is_throttled_to_fifteen_minutes(caplog):
    stop = ControlledStop((0.0, 10.0, 899.0, 900.0, 1799.0, 1800.0))
    repo = IdleRepository(stop, result=2)
    with caplog.at_level(logging.INFO, logger="apps.api.sync_worker"):
        assert run_idle(repo, stop) == 0
    assert repo.calls == [(at, {"batch_limit": 10}) for at in (0.0, 900.0, 1800.0)]
    assert repo.claims == 6
    assert stop.waits == [2.0, 4.0, 8.0, 10.0, 10.0, 10.0]
    assert caplog.messages == ["cleanup_recovery_expiry_tick expired_batches=2"] * 3


@pytest.mark.parametrize("result", [None, True, -1, 11, {}, [0]])
def test_invalid_expiry_results_are_nonfatal_and_do_not_hot_loop(result, caplog):
    stop = ControlledStop((0.0, 899.0, 900.0))
    repo = IdleRepository(stop, result=result)
    assert run_idle(repo, stop) == 0
    assert [at for at, kwargs in repo.calls] == [0.0, 900.0]
    assert caplog.messages == [
        "cleanup_recovery_expiry_tick_deferred error_type=ValueError"
    ] * 2


@pytest.mark.parametrize("failed_lane", ["archive", "expiry"])
def test_archive_and_expiry_failures_are_isolated_with_independent_timers(failed_lane, caplog):
    stop = ControlledStop((0.0, 1.0, 899.0, 900.0))

    class BothRepository(IdleRepository):
        def __init__(self):
            super().__init__(stop, error=RuntimeError("private alias and payload") if failed_lane == "expiry" else None)
            self.archive_calls = []

        def archive_due_history(self):
            self.archive_calls.append(stop.now())
            if failed_lane == "archive":
                raise RuntimeError("private alias and payload")
            return {"scanned": 2}

    repo = BothRepository()
    assert run_idle(repo, stop) == 0
    assert repo.archive_calls == [0.0, 900.0]
    assert [at for at, kwargs in repo.calls] == [0.0, 900.0]
    assert len(caplog.messages) == 2
    assert all("error_type=RuntimeError" in message for message in caplog.messages)
    assert "private" not in caplog.text


def test_expiry_failure_does_not_block_the_next_job(monkeypatch, caplog):
    stop = ControlledStop((0.0, 1.0))

    class NextJobRepository(IdleRepository):
        def claim_sync_job(self, **kwargs):
            self.claims += 1
            return None if self.claims == 1 else {"athlete_alias": "private alias"}

    repo = NextJobRepository(stop, error=RuntimeError("private database detail"))
    processed = []

    def process(repository, claim, **kwargs):
        processed.append(claim)
        stop.set()
        return SimpleNamespace(job_id="test-job", outcome="ACTIVATED", failure_code=None)

    monkeypatch.setattr("apps.api.sync_worker.process_claimed_job", process)
    assert run_idle(repo, stop) == 0
    assert repo.claims == 2 and processed == [{"athlete_alias": "private alias"}]
    assert repo.calls == [(0.0, {"batch_limit": 10})]
    assert caplog.messages == ["cleanup_recovery_expiry_tick_deferred error_type=RuntimeError"]
    assert "private" not in caplog.text


def test_once_mode_skips_optional_expiry():
    stop = ControlledStop()
    repo = IdleRepository(stop)
    assert run_idle(repo, stop, once=True) == 0
    assert repo.claims == 1 and repo.calls == []


@pytest.mark.parametrize("error", [None, RuntimeError("private database detail")])
def test_busy_worker_expires_between_jobs_without_interrupting_imports(monkeypatch, error, caplog):
    stop = ControlledStop((0.0, 899.0, 900.0, 1800.0))
    events = []

    class BusyRepository(IdleRepository):
        inflight = False

        def expire_analysis_cleanup_recovery(self, **kwargs):
            assert not self.inflight
            events.append(("expiry", stop.now()))
            return super().expire_analysis_cleanup_recovery(**kwargs)

        def claim_sync_job(self, **kwargs):
            self.claims += 1
            events.append(("claim", self.claims))
            self.inflight = True
            return {"athlete_alias": "private alias"}

    def process(repository, claim, **kwargs):
        assert repository.inflight
        calls_before_work = len(repository.calls)
        events.append(("process", repository.claims))
        # Time can cross the deadline during a job; expiry waits for completion.
        if repository.claims < len(stop.times):
            stop.index += 1
        else:
            stop.set()
        assert len(repository.calls) == calls_before_work
        repository.inflight = False
        return SimpleNamespace(job_id="test-job", outcome="ACTIVATED", failure_code=None)

    repo = BusyRepository(stop, error=error)
    monkeypatch.setattr("apps.api.sync_worker.process_claimed_job", process)
    assert run_idle(repo, stop) == 0
    assert repo.claims == 4
    assert repo.calls == [(at, {"batch_limit": 10}) for at in (0.0, 900.0, 1800.0)]
    assert events == [
        ("expiry", 0.0), ("claim", 1), ("process", 1),
        ("claim", 2), ("process", 2),
        ("expiry", 900.0), ("claim", 3), ("process", 3),
        ("expiry", 1800.0), ("claim", 4), ("process", 4),
    ]
    assert "private" not in caplog.text


def test_once_with_a_job_skips_optional_expiry(monkeypatch):
    stop = ControlledStop()

    class BusyRepository(IdleRepository):
        def claim_sync_job(self, **kwargs):
            self.claims += 1
            return {"athlete_alias": "private alias"}

    def process(repository, claim, **kwargs):
        assert repository.calls == []
        return SimpleNamespace(job_id="test-job", outcome="ACTIVATED", failure_code=None)

    repo = BusyRepository(stop)
    monkeypatch.setattr("apps.api.sync_worker.process_claimed_job", process)
    assert run_idle(repo, stop, once=True) == 0
    assert repo.claims == 1 and repo.calls == []


def test_shutdown_during_claim_skips_further_expiry():
    stop = ControlledStop()

    class ShuttingDownRepository(IdleRepository):
        def claim_sync_job(self, **kwargs):
            stop.set()
            return super().claim_sync_job(**kwargs)

    repo = ShuttingDownRepository(stop)
    assert run_idle(repo, stop) == 0
    assert repo.claims == 1 and repo.calls == [(0.0, {"batch_limit": 10})]


def test_archive_shutdown_skips_further_expiry():
    stop = ControlledStop()

    class ShuttingDownRepository(IdleRepository):
        def archive_due_history(self):
            stop.set()
            return {"scanned": 0}

    repo = ShuttingDownRepository(stop)
    assert run_idle(repo, stop) == 0
    assert repo.calls == [(0.0, {"batch_limit": 10})]


def test_stopped_worker_skips_expiry_and_claims():
    stop = ControlledStop()
    stop.set()
    repo = IdleRepository(stop)
    assert run_idle(repo, stop) == 0
    assert repo.calls == [] and repo.claims == 0


def test_shutdown_during_expiry_does_not_claim_a_new_job():
    stop = ControlledStop()

    class ShuttingDownRepository(IdleRepository):
        def expire_analysis_cleanup_recovery(self, **kwargs):
            result = super().expire_analysis_cleanup_recovery(**kwargs)
            stop.set()
            return result

    repo = ShuttingDownRepository(stop)
    assert run_idle(repo, stop) == 0
    assert repo.calls == [(0.0, {"batch_limit": 10})] and repo.claims == 0
