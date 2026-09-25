"""Request timings without identifiers, query strings, headers or payloads."""
from contextvars import ContextVar
from dataclasses import dataclass
import logging
from time import perf_counter

logger = logging.getLogger("uvicorn.error")


@dataclass
class StoreMetrics:
    calls: int = 0
    seconds: float = 0.
    bytes: int = 0


current_metrics: ContextVar[StoreMetrics | None] = ContextVar("onflows_store_metrics", default=None)


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
                        "store_calls=%d store_ms=%.0f store_bytes=%d response_bytes=%d",
                        scope["method"], route, status, (perf_counter()-started)*1000,
                        metrics.calls, metrics.seconds*1000, metrics.bytes, response_bytes)
            current_metrics.reset(token)
