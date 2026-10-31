"""Planning diagnostics preserve leases/results and separate waiting from work."""
from contextlib import nullcontext
from contextvars import copy_context
from copy import deepcopy
import json
import logging
from threading import BoundedSemaphore, Event, Thread, current_thread
from time import sleep
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from apps.api import management_service, planning_computation, planning_search, request_metrics
from apps.api.oauth_store import PersistentStoreFailure
from apps.api.request_metrics import StoreMetrics, current_metrics
from tests.api.test_final_audit_contract import SharedStore
from tests.api.test_management_final_read_phase import PublicationHTTP


def measured_compute(repository, calculate, **kwargs):
    metrics = StoreMetrics()
    token = current_metrics.set(metrics)
    try:
        result = planning_computation.compute(repository, "private-athlete", "a" * 64, calculate, **kwargs)
    finally:
        current_metrics.reset(token)
    return result, metrics


def test_owned_work_and_cache_hit_have_distinct_stages_without_changing_or_aliasing_results():
    repository = SharedStore()
    payload = {"private-result": [1, {"approved": False}]}
    result, owned = measured_compute(repository, lambda: deepcopy(payload))
    assert result == payload
    assert set(owned.stages) == {"planning_claim", "planning_slot_wait", "planning_calculate", "planning_complete"}
    assert all(value.calls == 1 and value.seconds >= 0 and value.cpu_seconds >= 0
               for value in owned.stages.values())

    cached, joined = measured_compute(repository, lambda: pytest.fail("READY must not recalculate"))
    assert cached == result
    assert set(joined.stages) == {"planning_claim", "planning_result_copy"}
    cached["private-result"][1]["approved"] = True
    assert result == payload
    again, _ = measured_compute(repository, lambda: pytest.fail("READY must not recalculate"))
    assert again == payload
    assert not any(value in owned.details() + joined.details() for value in ("private-athlete", "private-result", "approved"))


def test_durable_busy_wait_is_measured_separately_from_local_slot_wait():
    calls = []
    def rpc(alias, key, token, operation, result=None):
        calls.append((operation, token))
        return {"state": "BUSY"} if len(calls) == 1 else {"state": "READY", "result": {"version": 1}}
    result, metrics = measured_compute(SimpleNamespace(planning_computation=rpc),
        lambda: pytest.fail("A joined result must not calculate"), poll_seconds=.001)
    assert result == {"version": 1}
    assert [operation for operation, _ in calls] == ["CLAIM", "CLAIM"]
    assert calls[0][1] == calls[1][1]
    assert metrics.stages["planning_claim"].calls == 2
    assert metrics.stages["planning_shared_wait"].calls == 1
    assert set(metrics.stages) == {"planning_claim", "planning_shared_wait", "planning_result_copy"}


def test_real_local_semaphore_wait_is_not_charged_to_the_calculation(monkeypatch):
    slot = BoundedSemaphore(1)
    slot.acquire()
    waiting, calculated = Event(), Event()
    class Gate:
        def acquire(self, **kwargs):
            waiting.set()
            return slot.acquire(**kwargs)
        def release(self):
            slot.release()
    monkeypatch.setattr(planning_computation, "_SLOTS", Gate())
    metrics, results, failures = StoreMetrics(), [], []
    token = current_metrics.set(metrics)
    context = copy_context()
    current_metrics.reset(token)
    def calculate():
        calculated.set()
        return {"safe": True}
    def request():
        try:
            results.append(planning_computation.compute(SharedStore(), "athlete", "a" * 64, calculate))
        except BaseException as exc:
            failures.append(exc)
    worker = Thread(target=lambda: context.run(request))
    worker.start()
    try:
        assert waiting.wait(2)
        assert not calculated.is_set()
        sleep(.04)
    finally:
        slot.release()
        worker.join(timeout=3)
    assert not worker.is_alive() and not failures
    assert results == [{"safe": True}] and calculated.is_set()
    wait = metrics.stages["planning_slot_wait"]
    assert wait.seconds >= .035
    assert wait.cpu_seconds < wait.seconds
    assert metrics.stages["planning_calculate"].seconds < wait.seconds
    assert slot.acquire(blocking=False), "Owned slot must still be released"
    slot.release()
    assert current_metrics.get() is None


def test_heartbeat_does_not_share_request_metrics_or_another_threads_cpu(monkeypatch):
    renewed, observed = Event(), []
    real_event = Event
    class FastHeartbeatEvent:
        def __init__(self):
            self.event = real_event()
        def wait(self, timeout=None):
            return self.event.wait(.001 if current_thread().name == "planning-lease" else timeout)
        def set(self):
            self.event.set()
        def is_set(self):
            return self.event.is_set()
    monkeypatch.setattr(planning_computation, "Event", FastHeartbeatEvent)
    store = SharedStore()
    original = store.planning_computation
    def rpc(alias, key, token, operation, result=None):
        if operation == "RENEW":
            observed.append(current_metrics.get())
            renewed.set()
        return original(alias, key, token, operation, result)
    store.planning_computation = rpc
    def calculate():
        assert renewed.wait(2)
        return {"review_required": True}
    result, metrics = measured_compute(store, calculate)
    assert result == {"review_required": True}
    assert observed and all(value is None for value in observed)
    assert metrics.stages["planning_claim"].calls == metrics.stages["planning_complete"].calls == 1
    assert current_metrics.get() is None


def test_calculation_failure_records_time_without_hiding_error_or_leaking_a_slot(monkeypatch):
    repository, calls, metrics = SharedStore(), [], StoreMetrics()
    slot = BoundedSemaphore(1)
    monkeypatch.setattr(planning_computation, "_SLOTS", slot)
    original = repository.planning_computation
    def rpc(alias, key, token, operation, result=None):
        calls.append(operation)
        return original(alias, key, token, operation, result)
    repository.planning_computation = rpc
    token = current_metrics.set(metrics)
    try:
        with pytest.raises(ValueError, match="original calculation failure"):
            planning_computation.compute(repository, "athlete", "a" * 64,
                lambda: (_ for _ in ()).throw(ValueError("original calculation failure")))
    finally:
        current_metrics.reset(token)
    assert calls == ["CLAIM", "FAIL"]
    assert metrics.stages["planning_calculate"].calls == 1
    assert "planning_complete" not in metrics.stages
    assert slot.acquire(blocking=False)
    slot.release()


@pytest.mark.parametrize("failure", ["busy_deadline", "invalid_ready", "lost_complete"])
def test_lease_failure_outcomes_remain_failures_with_bounded_stage_labels(failure):
    operations, metrics = [], StoreMetrics()
    def rpc(alias, key, token, operation, result=None):
        operations.append(operation)
        if failure == "busy_deadline":
            return {"state": "BUSY"}
        if failure == "invalid_ready":
            return {"state": "READY", "result": None}
        return {"state": "OWNED" if operation == "CLAIM" else "LOST"}
    token = current_metrics.set(metrics)
    try:
        with pytest.raises(PersistentStoreFailure if failure == "invalid_ready" else HTTPException) as caught:
            planning_computation.compute(SimpleNamespace(planning_computation=rpc), "athlete", "a" * 64,
                                          lambda: {"safe": True}, wait_seconds=0)
    finally:
        current_metrics.reset(token)
    assert metrics.stages["planning_claim"].calls == 1
    if failure != "invalid_ready":
        assert caught.value.status_code == (503 if failure == "busy_deadline" else 409)
    assert operations == (["CLAIM", "COMPLETE", "FAIL"] if failure == "lost_complete" else ["CLAIM"])


def test_pure_calculation_without_persistence_uses_calling_thread_clocks(monkeypatch):
    wall, cpu = iter([10., 12.]), iter([1., 1.25])
    monkeypatch.setattr(request_metrics, "perf_counter", lambda: next(wall))
    monkeypatch.setattr(request_metrics, "thread_time", lambda: next(cpu))
    result, metrics = measured_compute(SimpleNamespace(), lambda: {"unchanged": True})
    assert result == {"unchanged": True}
    stage = metrics.stages["planning_calculate"]
    assert (stage.calls, stage.seconds, stage.cpu_seconds) == (1, 2., .25)


def test_full_http_and_shared_input_key_are_identical_with_aggregate_baseline_search_timings(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    def search(baseline, profile, replay, **kwargs):
        # A cold baseline duration cannot veto the first warmed alternative.
        assert kwargs == {"seconds_remaining": 150.}
        replay({"optional_easy_fraction": 0.})
        return replay({"optional_easy_fraction": .5})
    monkeypatch.setattr(planning_search, "improve", search)
    former, current = PublicationHTTP(shared_state="OWNED"), PublicationHTTP(shared_state="OWNED")
    with monkeypatch.context() as original:
        original.setattr(management_service, "timed_stage", lambda name: nullcontext())
        original.setattr(planning_computation, "timed_stage", lambda name: nullcontext())
        expected = former.run(original)
    caplog.clear()
    actual = current.run(monkeypatch)
    assert actual.status_code == expected.status_code == 200
    assert json.dumps(actual.json(), sort_keys=True, separators=(",", ":"), allow_nan=False) == json.dumps(
        expected.json(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert current.record == former.record and current.key == former.key
    assert [(method, path) for method, path, _, _ in current.requests] == [
        (method, path) for method, path, _, _ in former.requests]
    lines = [record.message for record in caplog.records
             if record.message.startswith("onflows_detail route=/api/v2/athlete/management/action ")]
    assert len(lines) == 1
    detail = json.loads(lines[0].split("timings=", 1)[1])
    stages = detail["stages"]
    assert stages["planning_baseline"]["calls"] == stages["planning_search"]["calls"] == 1
    assert stages["planning_search_replay"]["calls"] == 2
    assert all(stages[name]["calls"] == 1 for name in (
        "planning_claim", "planning_slot_wait", "planning_calculate", "planning_complete", "input_after"))
    assert "planning_shared_wait" not in stages and "planning_result_copy" not in stages
    assert all(set(stage) == {"calls", "ms", "thread_cpu_ms"} for stage in stages.values())
    assert not any(secret in lines[0] for secret in ("ath-test", "generation-one", "optional_easy_fraction", "approved_by"))
