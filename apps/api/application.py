"""Cloud application entrypoint.

Keeps the established FastAPI application intact while registering integration
ingress routes that should not live in the scientific/read API module.
"""

from .intervals_webhook import router as intervals_webhook_router
from .main import app


app.include_router(intervals_webhook_router)

__all__ = ["app"]
