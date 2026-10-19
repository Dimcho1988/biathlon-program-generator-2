"""Timing diagnostics do not disclose data, cross requests or hide failures."""
import asyncio
import base64
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from apps.api import request_metrics
from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from apps.api.request_metrics import RequestMetricsMiddleware, StoreMetrics, current_metrics, timed_stage


def test_resource_labels_are_fixed_and_drop_all_parameters():
    metrics = StoreMetrics()
    metrics.record_store("GET", "/onflows_athlete_settings?athlete_alias=eq.private-athlete&token=secret", .12, 100)
    metrics.record_store("POST", "/rpc/private-athlete-secret-key?secret=lab-result", .2, 50)
    token = current_metrics.set(metrics)
    try:
        with timed_stage("private-athlete-lab-result"):
            pass
    finally:
        current_metrics.reset(token)
    detail = metrics.details()
    assert not any(value in detail for value in ("private-athlete", "secret", "lab-result", "athlete_alias", "token="))
    value = json.loads(detail)
    assert value["store"] == {
        "GET:settings": {"calls": 1, "ms": 120, "bytes": 100},
        "POST:other": {"calls": 1, "ms": 200, "bytes": 50},
    }
    assert set(value["stages"]) == {"other"}
    assert (metrics.calls, metrics.bytes) == (2, 150)


@pytest.mark.parametrize("transport_failure", [False, True])
def test_failed_store_reads_are_measured_and_keep_the_original_failure(transport_failure):
    def handle(request):
        if transport_failure:
            raise httpx.ConnectError("private network detail", request=request)
        return httpx.Response(503, json={"code": "UPSTREAM_DOWN", "message": "private source"})
    repository = SupabasePilotRepository(supabase_url="https://example.supabase.co", secret_key="synthetic-key",
        encryption_key=base64.urlsafe_b64encode(b"x"*32).decode(),
        client=httpx.Client(transport=httpx.MockTransport(handle)))
    metrics = StoreMetrics()
    token = current_metrics.set(metrics)
    try:
        with pytest.raises(PersistentStoreFailure):
            repository._request("GET", "/onflows_athlete_settings?athlete_alias=eq.private-athlete")
    finally:
        current_metrics.reset(token)
    assert metrics.calls == 1
    assert metrics.resources["GET:settings"].calls == 1
    assert metrics.resources["GET:settings"].seconds >= 0
    assert "private" not in metrics.details()


def test_request_scopes_restore_context_and_never_reuse_previous_measurements(caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    observed = []

    async def app(scope, receive, send):
        observed.append(current_metrics.get())
        if len(observed) == 1:
            with timed_stage("outlook"):
                current_metrics.get().record_store("GET", "/onflows_management_plan_state?private=true", .1, 20)
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"{}"})

    async def exercise():
        wrapped = RequestMetricsMiddleware(app)
        for _ in range(2):
            await wrapped({"type": "http", "path": "/api/v2/test", "method": "GET",
                           "route": SimpleNamespace(path="/api/v2/test")}, None, discard)
            assert current_metrics.get() is None

    async def discard(message):
        pass

    asyncio.run(exercise())
    assert observed[0] is not observed[1]
    assert observed[0].calls == 1 and observed[1].calls == 0
    messages = [r.message for r in caplog.records]
    assert len([m for m in messages if m.startswith("onflows_detail")]) == 1
    assert not any("private" in m for m in messages)


@pytest.mark.parametrize("formatter_failure", [False, True])
def test_stage_and_formatter_failure_never_hide_application_error(monkeypatch, caplog, formatter_failure):
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    captured = []

    async def app(scope, receive, send):
        captured.append(current_metrics.get())
        with timed_stage("current"):
            raise RuntimeError("original application failure")

    if formatter_failure:
        def broken_formatter(self):
            raise ValueError("private formatter contents")
        monkeypatch.setattr(StoreMetrics, "details", broken_formatter)
    with pytest.raises(RuntimeError, match="original application failure"):
        asyncio.run(RequestMetricsMiddleware(app)({"type": "http", "path": "/api/v2/test", "method": "GET",
            "route": SimpleNamespace(path="/api/v2/test")}, None, None))
    assert current_metrics.get() is None
    assert captured[0].stages["current"].calls == 1
    assert "private formatter" not in caplog.text
    if formatter_failure:
        assert 'timings={"unavailable":true}' in caplog.text
