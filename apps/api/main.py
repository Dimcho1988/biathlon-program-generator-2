"""FastAPI entry point for the onFlows read-only API."""

from datetime import date
import logging
import os
import re
from typing import Annotated
from urllib.parse import quote, urlencode, urlsplit

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from .cloud import AthleteModelSettings, AthletePlanningProfile, service_token_valid
from .oauth_service import (
    OAuthConfigurationError,
    OAuthFlowError,
    begin_authorization,
    complete_authorization,
    connection_status,
    issue_login_ticket,
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
    completed_work_from_load_history,
    load_history_from_persisted,
    recovery_history_from_persisted,
    refresh,
    training_status_from_persisted,
    volume_history_from_load_history,
)
from .schemas import (
    AthleteSettingsInput,
    AthleteSettingsResponse,
    AthletePlanningProfileInput,
    AthletePlanningProfileResponse,
    CompletedWorkResponse,
    HealthResponse,
    LoadHistoryResponse,
    OAuthAuthorizationResponse,
    OAuthConnectionStatusResponse,
    RecoveryHistoryResponse,
    SessionExchangeRequest,
    SessionExchangeResponse,
    TrainingStatusResponse,
    VolumeHistoryResponse,
)
from .training_status import build_demo_training_status

app = FastAPI(title="onFlows API", version="1.0.0")
logger = logging.getLogger(__name__)
ATHLETE_ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
SAFE_WEB_NOTICE_PATTERN = re.compile(r"^[a-z0-9-]{1,64}$")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


def _web_base_url() -> str:
    destination = os.environ.get("ONFLOWS_WEB_BASE_URL", "").strip().rstrip("/")
    parsed = urlsplit(destination)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(status_code=503, detail="Web destination is not configured")
    return destination


@app.get("/api/v2/wake", include_in_schema=False)
def wake_preview(
    intervals: str | None = None,
    settings: str | None = None,
    resume: str | None = None,
):
    destination = _web_base_url()
    if resume is not None:
        if resume != "connect":
            raise HTTPException(status_code=400, detail="Wake continuation is invalid")
        return RedirectResponse(
            f"{destination}/api/integrations/intervals/connect?wake=ready",
            status_code=303,
        )
    query = [("wake", "ready")]
    for key, value in (("intervals", intervals), ("settings", settings)):
        if value is None:
            continue
        if not SAFE_WEB_NOTICE_PATTERN.fullmatch(value):
            raise HTTPException(status_code=400, detail="Wake destination is invalid")
        query.append((key, value))
    return RedirectResponse(f"{destination}/?{urlencode(query)}", status_code=303)


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


def _validated_alias(alias: str | None, *, fallback: bool = True) -> str | None:
    candidate = alias.strip() if alias else (_pilot_alias() if fallback else None)
    if candidate is not None and not ATHLETE_ALIAS_PATTERN.fullmatch(candidate):
        raise HTTPException(status_code=400, detail="Athlete session is invalid")
    return candidate


@app.get("/api/v2/real/training-status", response_model=TrainingStatusResponse)
def real_training_status(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        snapshot = _repository().latest(_validated_alias(athlete_alias))
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
def real_load_history(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        snapshot = _repository().latest(_validated_alias(athlete_alias))
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


@app.get("/api/v2/real/completed-work", response_model=CompletedWorkResponse)
def real_completed_work(
    period_start: date | None = None,
    period_end: date | None = None,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        snapshot = _repository().latest(_validated_alias(athlete_alias))
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if snapshot is None:
        raise HTTPException(
            status_code=503, detail="No valid real-data snapshot is available"
        )
    try:
        history = load_history_from_persisted(snapshot)
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Completed-work report requires a new real-data refresh",
        ) from exc
    try:
        return completed_work_from_load_history(history, period_start, period_end)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Requested period must be within the stored history",
        ) from exc


@app.get("/api/v2/real/volume-history", response_model=VolumeHistoryResponse)
def real_volume_history(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        snapshot = _repository().latest(_validated_alias(athlete_alias))
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if snapshot is None:
        raise HTTPException(
            status_code=503, detail="No valid real-data snapshot is available"
        )
    try:
        history = load_history_from_persisted(snapshot)
        return volume_history_from_load_history(history)
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Volume history requires a new real-data refresh",
        ) from exc


@app.get("/api/v2/real/recovery-history", response_model=RecoveryHistoryResponse)
def real_recovery_history(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        snapshot = _repository().latest(_validated_alias(athlete_alias))
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if snapshot is None:
        raise HTTPException(
            status_code=503, detail="No valid real-data snapshot is available"
        )
    try:
        return recovery_history_from_persisted(snapshot)
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Recovery history requires a new real-data refresh",
        ) from exc


@app.post("/api/v2/real/refresh", status_code=202)
def refresh_real_data(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        repository = _repository()
        resolved_alias = _validated_alias(athlete_alias)
        connection = repository.connection(resolved_alias)
        if connection is None or connection.status != "CONNECTED":
            raise ConfigurationError("Intervals profile is not connected")
        athlete_settings = repository.athlete_settings(resolved_alias)
        result = refresh(
            repository,
            access_token=connection.access_token,
            provider_athlete_id=connection.provider_athlete_id,
            athlete_alias=resolved_alias,
            athlete_settings=athlete_settings,
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


@app.get("/api/v2/athlete/settings", response_model=AthleteSettingsResponse)
def athlete_settings(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        settings = _repository().athlete_settings(_validated_alias(athlete_alias))
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if settings is None:
        return AthleteSettingsResponse(configured=False)
    return AthleteSettingsResponse(
        configured=True,
        hr_zone_bounds_bpm=settings.zone_bounds_bpm,
        timezone=settings.timezone,
    )


@app.put("/api/v2/athlete/settings", response_model=AthleteSettingsResponse)
def update_athlete_settings(
    body: AthleteSettingsInput,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    resolved_alias = _validated_alias(athlete_alias)
    try:
        settings = AthleteModelSettings(
            body.hr_zone_bounds_bpm, body.timezone.strip()
        ).validate()
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Six increasing HR boundaries and a valid timezone are required",
        ) from exc
    try:
        repository = _repository()
        connection = repository.connection(resolved_alias)
        if connection is None or connection.status != "CONNECTED":
            raise HTTPException(status_code=409, detail="Intervals profile is not connected")
        repository.save_athlete_settings(resolved_alias, settings)
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    return AthleteSettingsResponse(
        configured=True,
        hr_zone_bounds_bpm=settings.zone_bounds_bpm,
        timezone=settings.timezone,
    )


@app.get(
    "/api/v2/athlete/planning-profile",
    response_model=AthletePlanningProfileResponse,
)
def athlete_planning_profile(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        profile = _repository().athlete_planning_profile(
            _validated_alias(athlete_alias)
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if profile is None:
        return AthletePlanningProfileResponse(configured=False)
    return AthletePlanningProfileResponse(
        configured=True,
        profile=AthletePlanningProfileInput.model_validate(profile.to_payload()),
    )


@app.put(
    "/api/v2/athlete/planning-profile",
    response_model=AthletePlanningProfileResponse,
)
def update_athlete_planning_profile(
    body: AthletePlanningProfileInput,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    resolved_alias = _validated_alias(athlete_alias)
    try:
        profile = AthletePlanningProfile(
            schema_version=body.schema_version,
            season_start=body.season_start,
            season_end=body.season_end,
            annual_target_hours=body.annual_target_hours,
            sessions_per_week=body.sessions_per_week,
            rest_days=body.rest_days,
            double_session_days=body.double_session_days,
            long_session_day=body.long_session_day,
            intensity_days=body.intensity_days,
            strength_days=body.strength_days,
            max_key_sessions_per_week=body.max_key_sessions_per_week,
            mesocycle_anchor_date=body.mesocycle_anchor_date,
            mesocycle_length_weeks=body.mesocycle_length_weeks,
            camp_default_accent_limit=body.camp_default_accent_limit,
            double_threshold_enabled=body.double_threshold_enabled,
            double_threshold_day=body.double_threshold_day,
            double_threshold_components=body.double_threshold_components,
        ).validate()
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Planning profile values are inconsistent"
        ) from exc
    try:
        repository = _repository()
        if repository.athlete_settings(resolved_alias) is None:
            raise HTTPException(
                status_code=409,
                detail="Athlete HR zones and timezone must be configured first",
            )
        repository.save_athlete_planning_profile(resolved_alias, profile)
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    return AthletePlanningProfileResponse(
        configured=True,
        profile=AthletePlanningProfileInput.model_validate(profile.to_payload()),
    )


@app.post(
    "/api/v2/integrations/intervals/authorize",
    response_model=OAuthAuthorizationResponse,
)
def authorize_intervals(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        url = begin_authorization(
            _repository(), athlete_alias=_validated_alias(athlete_alias, fallback=False)
        )
    except (OAuthConfigurationError, PersistentStoreFailure) as exc:
        raise HTTPException(
            status_code=503, detail="Intervals OAuth connection is unavailable"
        ) from exc
    return OAuthAuthorizationResponse(authorization_url=url)


@app.get(
    "/api/v2/integrations/intervals/status",
    response_model=OAuthConnectionStatusResponse,
)
def intervals_status(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        status = connection_status(
            _repository(), athlete_alias=_validated_alias(athlete_alias)
        )
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
    stage = "storage"
    try:
        repository = _repository()
        stage = "authorization"
        athlete_alias = complete_authorization(
            repository, dict(request.query_params)
        )
        stage = "session"
        ticket = issue_login_ticket(repository, athlete_alias)
    except OAuthFlowError as exc:
        logger.warning("intervals_oauth_callback_failed stage=%s", exc.stage)
        return RedirectResponse(
            f"{destination}/?intervals=error-{exc.stage}", status_code=303
        )
    except (
        OAuthConfigurationError,
        PersistentStoreConfigurationError,
        PersistentStoreFailure,
    ):
        logger.warning("intervals_oauth_callback_failed stage=%s", stage)
        return RedirectResponse(
            f"{destination}/?intervals=error-{stage}", status_code=303
        )
    return RedirectResponse(
        f"{destination}/api/session/complete?ticket={quote(ticket, safe='')}",
        status_code=303,
    )


@app.post("/api/v2/session/exchange", response_model=SessionExchangeResponse)
def exchange_session(
    body: SessionExchangeRequest,
    authorization: Annotated[str | None, Header()] = None,
):
    _authorize(authorization)
    if not 32 <= len(body.ticket) <= 128:
        raise HTTPException(status_code=400, detail="Login ticket is invalid")
    try:
        athlete_alias = _repository().consume_login_ticket(body.ticket)
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if athlete_alias is None or _validated_alias(athlete_alias, fallback=False) is None:
        raise HTTPException(status_code=401, detail="Login ticket is invalid or expired")
    return SessionExchangeResponse(athlete_alias=athlete_alias)
