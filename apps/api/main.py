"""Compose the onFlows HTTP API; endpoint behavior lives in routes/."""

from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware

from .http_runtime import lifespan
from .request_metrics import RequestMetricsMiddleware
from .routes import (
    management,
    management_speed_comparison,
    models,
    settings,
    response,
    health,
    integrations,
    dashboard,
    sync,
    activities,
)

app = FastAPI(title="onFlows API", version="1.0.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=3)
app.add_middleware(RequestMetricsMiddleware)

ROUTE_MODULES = (
    management,
    management_speed_comparison,
    models,
    settings,
    response,
    health,
    integrations,
    dashboard,
    sync,
    activities,
)

for route_module in ROUTE_MODULES:
    app.include_router(route_module.router)
