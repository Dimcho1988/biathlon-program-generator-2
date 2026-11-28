"""Compose the onFlows HTTP API; endpoint behavior lives in routes/."""

from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware

from .http_runtime import lifespan
from .request_metrics import RequestMetricsMiddleware
from .prepared_read_budget import PreparedReadBudgetMiddleware
from .routes import (
    app_guide,
    chatgpt_oauth,
    assistant,
    learning,
    management,
    management_speed_comparison,
    models,
    settings,
    response,
    energy,
    health,
    integrations,
    dashboard,
    sync,
    activities,
)

app = FastAPI(title="onFlows API", version="1.0.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=3)

ROUTE_MODULES = (
    app_guide,
    chatgpt_oauth,
    assistant,
    learning,
    management,
    management_speed_comparison,
    models,
    settings,
    response,
    energy,
    health,
    integrations,
    dashboard,
    sync,
    activities,
)

for route_module in ROUTE_MODULES:
    app.include_router(route_module.router)

app.add_middleware(PreparedReadBudgetMiddleware, paths={
    route.path for module in ROUTE_MODULES for route in module.router.routes
    if getattr(getattr(route, "endpoint", None), "_onflows_prepared_kind", None)
    not in {None, "management-state"}
})
# Include admission waiting and overload responses in request metrics.
app.add_middleware(RequestMetricsMiddleware)
