"""Request timings without identifiers, query strings, headers or payloads."""
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import logging
from time import perf_counter, thread_time

logger = logging.getLogger("uvicorn.error")

# Only fixed operation names enter logs. In particular, never use a URL, query,
# athlete alias, model key or payload as a metric label.
_STORE_LABELS = {
    "/onflows_athlete_settings": "settings",
    "/onflows_management_entries": "management_entries",
    "/onflows_management_plan_state": "plan_pointer",
    "/onflows_management_plan_revisions": "plan_history",
    "/onflows_model_entries": "models",
    "/onflows_response_entries": "responses",
    "/onflows_analysis_generations": "generation",
    "/onflows_analysis_generation_activities": "catalog",
    "/onflows_activity_run_summaries": "summaries",
    "/onflows_activity_catalog": "activity_catalog",
    "/rpc/active_onflows_analysis": "analysis",
    "/rpc/onflows_management_checkpoint": "checkpoint",
    "/rpc/onflows_planning_computation": "computation",
    "/rpc/save_onflows_management_plan": "publication",
    "/rpc/save_onflows_management_plan_compact": "publication",
    "/rpc/defer_onflows_management_check": "defer",
    "/rpc/queue_onflows_management_import": "import_queue",
    "/rpc/read_onflows_management_overview": "overview_plan",
    "/rpc/record_onflows_management_check": "unchanged_check",
    "/rpc/read_onflows_management_checks": "check_history",
}
_STAGES = frozenset({
    "profile", "current", "overview_active", "drafts", "outlook", "projection", "input_after",
    "planning_claim", "planning_shared_wait", "planning_result_copy", "planning_slot_wait",
    "planning_calculate", "planning_complete", "planning_baseline", "planning_search",
    "planning_search_replay",
})


@dataclass
class _Timing:
    calls: int = 0
    seconds: float = 0.
    bytes: int = 0
    cpu_seconds: float = 0.


@dataclass
class StoreMetrics:
    calls: int = 0
    seconds: float = 0.
    bytes: int = 0
    json_seconds: float = 0.
    shadow_decode_seconds: float = 0.
    archive_calls: int = 0
    archive_seconds: float = 0.
    archive_bytes: int = 0
    archive_decode_seconds: float = 0.
    resources: dict[str, _Timing] = field(default_factory=dict)
    stages: dict[str, _Timing] = field(default_factory=dict)

    def record_store(self, method, path, seconds, size):
        self.calls += 1
        self.seconds += seconds
        self.bytes += size
        verb = method.upper() if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"} else "OTHER"
        label = f"{verb}:{_STORE_LABELS.get(path.split('?', 1)[0], 'other')}"
        timing = self.resources.setdefault(label, _Timing())
        timing.calls += 1
        timing.seconds += seconds
        timing.bytes += size

    def details(self):
        # One bounded detail line per request, rather than one log per read.
        resources = {key: {"calls": value.calls, "ms": round(value.seconds*1000), "bytes": value.bytes}
                     for key, value in sorted(self.resources.items(), key=lambda item: (-item[1].seconds, item[0]))[:12]}
        stages = {key: {"calls": value.calls, "ms": round(value.seconds*1000),
                        "thread_cpu_ms": round(value.cpu_seconds*1000)} for key, value in sorted(self.stages.items())}
        return json.dumps({"store": resources, "stages": stages}, separators=(",", ":"), allow_nan=False)


current_metrics: ContextVar[StoreMetrics | None] = ContextVar("onflows_store_metrics", default=None)


@contextmanager
def timed_stage(name):
    """Inclusive wall and calling-thread CPU time for a synchronous stage.

    Nested stages overlap. Thread CPU excludes work on other native threads;
    neither value is an isolated attribution of whole-process CPU usage. In
    particular, planning_calculate includes baseline and search, and search
    includes its replay stages: those values must not be added together.
    """
    metrics = current_metrics.get()
    if metrics is None:
        yield
        return
    label = name if isinstance(name, str) and name in _STAGES else "other"
    started, cpu_started = perf_counter(), thread_time()
    try:
        yield
    finally:
        value = metrics.stages.setdefault(label, _Timing())
        value.calls += 1
        value.seconds += perf_counter()-started
        value.cpu_seconds += thread_time()-cpu_started


class RequestMetricsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/v2/"):
            return await self.app(scope, receive, send)
        metrics = StoreMetrics()
        token = current_metrics.set(metrics)
        started = perf_counter()
        status, response_bytes = 500, 0

        async def record(message):
            nonlocal status, response_bytes
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, record)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            logger.info("onflows_request method=%s route=%s status=%d elapsed_ms=%.0f "
                        "store_calls=%d store_ms=%.0f store_bytes=%d response_bytes=%d "
                        "store_json_ms=%.0f shadow_decode_ms=%.0f archive_calls=%d "
                        "archive_ms=%.0f archive_bytes=%d archive_decode_ms=%.0f",
                        scope["method"], route, status, (perf_counter()-started)*1000,
                        metrics.calls, metrics.seconds*1000, metrics.bytes, response_bytes,
                        metrics.json_seconds*1000, metrics.shadow_decode_seconds*1000,
                        metrics.archive_calls, metrics.archive_seconds*1000,
                        metrics.archive_bytes, metrics.archive_decode_seconds*1000)
            try:
                if metrics.resources or metrics.stages:
                    try:
                        detail = metrics.details()
                    except (TypeError, ValueError, OverflowError):
                        # A diagnostic formatter must not replace an application
                        # error, or turn an otherwise successful read into one.
                        detail = '{"unavailable":true}'
                    logger.info("onflows_detail route=%s timings=%s", route, detail)
            finally:
                current_metrics.reset(token)
