"""FastAPI entry point for the onFlows read-only API."""

import os
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from .cloud import service_token_valid
from .oauth_service import (
    OAuthConfigurationError,
    OAuthFlowError,
    begin_authorization,
    complete_authorization,
    connection_status,
    settings_from_environment,
)
from .oauth_store import (
    PersistentStoreConfigurationError,
    PersistentStoreFailure,
    SupabasePilotRepository,
)
from .real_service import (
    ConfigurationError,
    ProviderFailure,
    load_history_from_persisted,
    refresh,
    training_status_from_persisted,
)
from .schemas import (
    HealthResponse,
    LoadHistoryResponse,
    OAuthAuthorizationResponse,
    OAuthConnectionStatusResponse,
    TrainingStatusResponse,
)
from .training_status import build_demo_training_status

app = FastAPI(title="onFlows API", version="1.0.0")


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
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _repository() -> SupabasePilotRepository:
    try:
        return SupabasePilotRepository.from_environment()
    except PersistentStoreConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail="Persistent server storage is not configured",
        ) from exc


def _pilot_alias() -> str:
    alias = os.environ.get("ONFLOWS_ATHLETE_ALIAS", "").strip()
    if not alias:
        raise HTTPException(
            status_code=503, detail="Pilot athlete configuration is incomplete"
        )
    return alias


@app.get("/api/v2/real/training-status", response_model=TrainingStatusResponse)
def real_training_status(authorization: Annotated[str | None, Header()] = None):
    _authorize(authorization)
    try:
        snapshot = _repository().latest(_pilot_alias())
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if snapshot is None:
        raise HTTPException(
            status_code=503, detail="No valid real-data snapshot is available"
        )
    try:
        return training_status_from_persisted(snapshot)
    except ValueError as exc:
        raise HTTPException(
            status_code=503, detail="Stored real-data snapshot is invalid"
        ) from exc


@app.get("/api/v2/real/load-history", response_model=LoadHistoryResponse)
def real_load_history(authorization: Annotated[str | None, Header()] = None):
    _authorize(authorization)
    try:
        snapshot = _repository().latest(_pilot_alias())
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if snapshot is None:
        raise HTTPException(
            status_code=503, detail="No valid real-data snapshot is available"
        )
    try:
        return load_history_from_persisted(snapshot)
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Load history requires a new real-data refresh",
        ) from exc


@app.post("/api/v2/real/refresh", status_code=202)
def refresh_real_data(authorization: Annotated[str | None, Header()] = None):
    _authorize(authorization)
    try:
        repository = _repository()
        connection = repository.connection(_pilot_alias())
        if connection is None or connection.status != "CONNECTED":
            raise ConfigurationError("Intervals profile is not connected")
        result = refresh(
            repository,
            access_token=connection.access_token,
            provider_athlete_id=connection.provider_athlete_id,
        )
    except ConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    except ProviderFailure as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "status": "refreshed",
        "processed_activities": result.processed_activities,
    }


@app.post(
    "/api/v2/integrations/intervals/authorize",
    response_model=OAuthAuthorizationResponse,
)
def authorize_intervals(authorization: Annotated[str | None, Header()] = None):
    _authorize(authorization)
    try:
        url = begin_authorization(_repository())
    except (OAuthConfigurationError, PersistentStoreFailure) as exc:
        raise HTTPException(
            status_code=503, detail="Intervals OAuth connection is unavailable"
        ) from exc
    return OAuthAuthorizationResponse(authorization_url=url)


@app.get(
    "/api/v2/integrations/intervals/status",
    response_model=OAuthConnectionStatusResponse,
)
def intervals_status(authorization: Annotated[str | None, Header()] = None):
    _authorize(authorization)
    try:
        status = connection_status(_repository())
    except (OAuthConfigurationError, PersistentStoreFailure) as exc:
        raise HTTPException(
            status_code=503, detail="Intervals OAuth connection is unavailable"
        ) from exc
    return OAuthConnectionStatusResponse(
        connected=status.connected, scopes=list(status.scopes)
    )


@app.get("/api/v2/integrations/intervals/callback", include_in_schema=False)
def intervals_callback(request: Request):
    try:
        settings = settings_from_environment()
    except OAuthConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="OAuth server configuration is incomplete"
        ) from exc
    destination = settings.web_base_url.rstrip("/")
    try:
        complete_authorization(_repository(), dict(request.query_params))
    except (
        OAuthConfigurationError,
        OAuthFlowError,
        PersistentStoreConfigurationError,
        PersistentStoreFailure,
    ):
        return RedirectResponse(f"{destination}/?intervals=error", status_code=303)
    return RedirectResponse(f"{destination}/?intervals=connected", status_code=303)
