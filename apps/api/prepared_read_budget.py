"""Bound memory retained by prepared HTTP reads through the final response send.

Waiting requests do not occupy a worker thread or fetch a payload. The gate is
per ASGI event loop (one per uvicorn process), never shared between athletes as
a response cache. Health checks, progress polling and writes stay outside it.
"""
import asyncio
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from starlette.responses import JSONResponse


@dataclass
class _Gate:
    slots: asyncio.Semaphore
    admitted: int = 0


class PreparedReadBudgetMiddleware:
    def __init__(self, app, *, paths, concurrency=2, waiting=32, timeout=20.0):
        self.app = app
        self.paths = frozenset(paths)
        self.concurrency = concurrency
        self.waiting = waiting
        self.timeout = timeout
        self._gates = WeakKeyDictionary()

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "GET"
                or scope["path"] not in self.paths):
            return await self.app(scope, receive, send)
        loop = asyncio.get_running_loop()
        gate = self._gates.get(loop)
        if gate is None:
            gate = self._gates[loop] = _Gate(asyncio.Semaphore(self.concurrency))
        if gate.admitted >= self.concurrency + self.waiting:
            return await self._busy(scope, receive, send)
        gate.admitted += 1
        acquired = False
        processing = None
        try:
            try:
                await asyncio.wait_for(gate.slots.acquire(), timeout=self.timeout)
                acquired = True
            except TimeoutError:
                return await self._busy(scope, receive, send)
            # Keep the slot while FastAPI renders and gzip/send retain bytes.
            # Releasing it at the end of the repository call is too early.
            processing = asyncio.create_task(self.app(scope, receive, send))

            def finished(task):
                gate.slots.release()
                gate.admitted -= 1
                # A disconnected caller may no longer await this result.
                if not task.cancelled():
                    task.exception()

            processing.add_done_callback(finished)
            # Cancellation of the caller must not admit another large read
            # while its synchronous repository/render thread is still running.
            await asyncio.shield(processing)
        finally:
            if processing is None:
                if acquired:
                    gate.slots.release()
                gate.admitted -= 1

    @staticmethod
    async def _busy(scope, receive, send):
        response = JSONResponse(
            {"detail": "В момента се отварят много страници. Опитай отново след малко."},
            status_code=503,
            headers={"Retry-After": "3", "Cache-Control": "no-store",
                     "X-OnFlows-Read-Busy": "1"},
        )
        await response(scope, receive, send)
