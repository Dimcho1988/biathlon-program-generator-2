"""Shared, fenced planning computation. Approval/publication remain caller-owned.

The durable slot has one owner per athlete, survives API/worker restarts, and
only reuses an exactly matching immutable input key. No user authorization is
cached. Every caller must still perform the live input and publication checks.
"""
from copy import deepcopy
from threading import Event, Thread, BoundedSemaphore
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException
from .oauth_store import PersistentStoreFailure
from .request_metrics import timed_stage


_SLOTS = BoundedSemaphore(1)  # Bound CPU/memory per API or worker process.

def compute(repository, alias, key, calculate, *, wait_seconds=180., poll_seconds=1.):
    rpc = getattr(repository, "planning_computation", None)
    if rpc is None:
        # Non-persistent repositories are used by the pure engine/test harness.
        with timed_stage("planning_calculate"):
            return calculate()
    token = str(uuid4())
    deadline = monotonic() + wait_seconds
    stop = Event()
    while True:
        with timed_stage("planning_claim"):
            state = rpc(alias, key, token, "CLAIM")
        if state.get("state") == "READY":
            if not isinstance(state.get("result"), dict):
                raise PersistentStoreFailure("Invalid shared planning result")
            with timed_stage("planning_result_copy"):
                return deepcopy(state["result"])
        if state.get("state") == "OWNED":
            break
        if state.get("state") != "BUSY":
            raise PersistentStoreFailure("Invalid planning lease")
        if monotonic() >= deadline:
            raise HTTPException(503, "Преизчислението още работи. Изчакайте и обновете проекта.", headers={"Retry-After": "5"})
        with timed_stage("planning_shared_wait"):
            stop.wait(min(poll_seconds, max(0., deadline-monotonic())))

    lost = Event()
    def heartbeat():
        while not stop.wait(20.):
            try:
                if rpc(alias, key, token, "RENEW").get("state") != "OWNED":
                    lost.set()
                    return
            except Exception:
                # Completion also validates the token and expiry in the database.
                lost.set()
                return
    # Do not copy the request's mutable metrics into this native thread. Its
    # renewals remain outside calling-thread CPU/store counters, including when
    # a slow renewal outlives the bounded join below.
    thread = Thread(target=heartbeat, daemon=True, name="planning-lease")
    thread.start()
    acquired = False
    try:
        with timed_stage("planning_slot_wait"):
            acquired = _SLOTS.acquire(timeout=max(0., deadline-monotonic()))
        if not acquired:
            raise HTTPException(503, "Planning capacity is busy; retry shortly", headers={"Retry-After": "5"})
        with timed_stage("planning_calculate"):
            result = calculate()
        with timed_stage("planning_complete"):
            if lost.is_set() or rpc(alias, key, token, "COMPLETE", result).get("state") != "READY":
                raise HTTPException(409, "Planning ownership changed; regenerate the current project")
        return result
    except BaseException:
        try:
            rpc(alias, key, token, "FAIL")
        except Exception:
            pass  # The bounded lease makes a crashed task retryable.
        raise
    finally:
        if acquired:
            _SLOTS.release()
        stop.set()
        thread.join(timeout=1.)
