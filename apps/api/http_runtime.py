"""One bounded HTTP connection pool per API process, closed at shutdown.

Repositories carry their own credentials. The shared transport carries no
athlete state, responses, model results, or authorization decisions.
"""
from contextlib import asynccontextmanager
from threading import Lock

import httpx

_lock = Lock()
_client: httpx.Client | None = None


def store_client() -> httpx.Client:
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(15.0, connect=5.0, pool=5.0),
                limits=httpx.Limits(max_connections=40, max_keepalive_connections=20,
                                    keepalive_expiry=30.0),
            )
        return _client


@asynccontextmanager
async def lifespan(app):
    global _client
    store_client()
    try:
        # Compute immutable release identities before this instance becomes ready;
        # prepared HTTP reads then need no source scan after a deployment.
        from .preparation_identity import warm
        warm()
        yield
    finally:
        with _lock:
            client, _client = _client, None
        if client is not None:
            client.close()
