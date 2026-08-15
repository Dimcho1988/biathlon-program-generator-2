"""FastAPI entry point for the onFlows read-only API."""

import os
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException

from .schemas import HealthResponse, TrainingStatusResponse
from .training_status import build_demo_training_status
from .cloud import InMemorySnapshotRepository, service_token_valid
from .real_service import ConfigurationError, ProviderFailure, context_from_environment, refresh

app = FastAPI(title="onFlows API", version="1.0.0")
snapshots = InMemorySnapshotRepository()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/api/v1/demo/training-status", response_model=TrainingStatusResponse)
def demo_training_status() -> TrainingStatusResponse:
    return build_demo_training_status()


def _authorize(authorization: str | None) -> None:
    expected = os.environ.get("ONFLOWS_SERVICE_TOKEN", "")
    provided = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not service_token_valid(provided, expected):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Bearer"})


@app.get("/api/v2/real/training-status")
def real_training_status(authorization: Annotated[str | None, Header()] = None):
    _authorize(authorization)
    try:
        alias = context_from_environment().public_alias
    except ConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    snapshot = snapshots.latest(alias)
    if snapshot is None:
        raise HTTPException(status_code=503, detail="No valid real-data snapshot is available")
    return snapshot


@app.post("/api/v2/real/refresh", status_code=202)
def refresh_real_data(authorization: Annotated[str | None, Header()] = None):
    _authorize(authorization)
    try:
        result = refresh(snapshots)
    except ConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderFailure as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "refreshed", "processed_activities": result.processed_activities}
