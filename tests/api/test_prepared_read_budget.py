import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from apps.api import dependencies, main, prepared_view_storage, prepared_views
from apps.api.prepared_read_budget import PreparedReadBudgetMiddleware
from apps.api.management_projection import public_management
from tests.api.test_prepared_views import HEADERS


PATH = "/api/v2/athlete/management/view"


def scope(path=PATH, method="GET"):
    return {"type": "http", "method": method, "path": path}


async def receive():
    return {"type": "http.disconnect"}


async def discard(message):
    pass


def test_gate_covers_final_send_and_leaves_health_progress_and_writes_free():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def app(s, r, send):
            calls.append((s["path"], s["method"]))
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"complete"})

        async def slow_send(message):
            if message["type"] == "http.response.body":
                entered.set()
                await release.wait()

        gate = PreparedReadBudgetMiddleware(app, paths={PATH}, concurrency=1)
        first = asyncio.create_task(gate(scope(), receive, slow_send))
        await entered.wait()
        second = asyncio.create_task(gate(scope(), receive, discard))
        await asyncio.sleep(.02)
        assert len(calls) == 1  # First render finished, but send still retains bytes.
        for s in (scope("/health"), scope("/api/v2/athlete/management/state"), scope(method="PUT")):
            await asyncio.wait_for(gate(s, receive, discard), .2)
        assert len(calls) == 4
        release.set()
        await asyncio.gather(first, second)
        assert len(calls) == 5
    asyncio.run(scenario())


def test_queue_overflow_timeout_and_cancel_do_not_leak_capacity():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def app(s, r, send):
            entered.set()
            await release.wait()

        gate = PreparedReadBudgetMiddleware(app, paths={PATH}, concurrency=1, waiting=1, timeout=.08)
        first = asyncio.create_task(gate(scope(), receive, discard))
        await entered.wait()
        queued = asyncio.create_task(gate(scope(), receive, discard))
        await asyncio.sleep(.01)
        messages = []

        async def collect(message):
            messages.append(message)

        await gate(scope(), receive, collect)
        assert messages[0]["status"] == 503
        assert (b"retry-after", b"3") in messages[0]["headers"]
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        messages.clear()
        await gate(scope(), receive, collect)  # Wait timeout, no payload loaded.
        assert messages[0]["status"] == 503
        release.set()
        await first
        await gate(scope(), receive, discard)
        assert gate._gates[asyncio.get_running_loop()].admitted == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [ValueError("render failed"), OSError("client disconnected")])
def test_application_or_send_error_releases_slot(failure):
    async def scenario():
        calls = 0

        async def app(s, r, send):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise failure

        gate = PreparedReadBudgetMiddleware(app, paths={PATH}, concurrency=1)
        with pytest.raises(type(failure)):
            await gate(scope(), receive, discard)
        await asyncio.wait_for(gate(scope(), receive, discard), .2)
        assert calls == 2
    asyncio.run(scenario())


def test_cancelled_caller_does_not_release_still_running_processing():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def app(s, r, send):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()

        gate = PreparedReadBudgetMiddleware(app, paths={PATH}, concurrency=1)
        first = asyncio.create_task(gate(scope(), receive, discard))
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(gate(scope(), receive, discard))
        await asyncio.sleep(.02)
        assert calls == 1
        release.set()
        await second
        assert calls == 2
    asyncio.run(scenario())


def test_actual_app_registers_prepared_get_paths():
    middleware = next(m for m in main.app.user_middleware if m.cls is PreparedReadBudgetMiddleware)
    assert {PATH, "/api/v2/athlete/management/active", "/api/v2/athlete/models/speed"} <= middleware.kwargs["paths"]
    assert "/api/v2/athlete/management/state" not in middleware.kwargs["paths"]


@pytest.mark.parametrize("compressed", [False, True])
def test_projection_and_wire_json_match_without_changing_saved_evidence(monkeypatch, compressed):
    payload = {"active": {"plan": {"days": [{"date": "2026-11-28", "seconds": 900.25}] * 4000},
        "individual_learning": {"mode": "observe", "private_answers": ["never public"]},
        "input_snapshot": {"response_revisions": {"lab": "private-date"}},
        "warnings": [{"code": "DRAFT_REQUIRES_COACH_REVIEW"}, {"code": "KEEP"}]}}
    stored = prepared_view_storage.pack("management-view", payload) if compressed else payload
    original = deepcopy(stored)
    state = {"ready": True, "athlete_alias": "ath-prepared", "kind": "management-view", "payload": stored}
    repo = SimpleNamespace(prepared_view=lambda *a, **kw: state)
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "prepared-test")
    monkeypatch.setattr(dependencies, "repository", lambda: repo)
    expected = public_management(prepared_view_storage.unpack(stored))
    expected["active"]["capabilities"] = {"automatic_publication": True}
    response = TestClient(main.app).get(PATH, headers=HEADERS)
    assert response.status_code == 200
    assert response.content == json.dumps(expected, ensure_ascii=False, allow_nan=False,
        indent=None, separators=(",", ":")).encode("utf-8")
    assert response.json() == prepared_views.read(repo, "ath-prepared", "management-view")
    assert stored == original
