"""Integrations HTTP routes."""

import logging
import re
from typing import Annotated
from urllib.parse import quote, urlencode
from fastapi import Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from ..oauth_service import (
    OAuthConfigurationError,
    OAuthFlowError,
    begin_authorization,
    complete_authorization,
    connection_status,
    issue_login_ticket,
    settings_from_environment,
)
from ..oauth_store import PersistentStoreConfigurationError, PersistentStoreFailure
from ..schemas import (
    OAuthAuthorizationResponse,
    OAuthConnectionStatusResponse,
    SessionExchangeRequest,
    SessionExchangeResponse,
)
from fastapi import APIRouter
from .. import dependencies

router = APIRouter()
logger = logging.getLogger(__name__)
SAFE_WEB_NOTICE_PATTERN = re.compile(r"^[a-z0-9-]{1,64}$")


@router.get("/api/v2/wake", include_in_schema=False)
def wake_preview(
    intervals: str | None = None,
    settings: str | None = None,
    resume: str | None = None,
):
    destination = dependencies.web_base_url()
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


@router.post(
    "/api/v2/integrations/intervals/authorize",
    response_model=OAuthAuthorizationResponse,
)
def authorize_intervals(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    try:
        url = begin_authorization(
            dependencies.repository(), athlete_alias=dependencies.validated_alias(athlete_alias, fallback=False)
        )
    except (OAuthConfigurationError, PersistentStoreFailure) as exc:
        raise HTTPException(
            status_code=503, detail="Intervals OAuth connection is unavailable"
        ) from exc
    return OAuthAuthorizationResponse(authorization_url=url)


@router.get(
    "/api/v2/integrations/intervals/status",
    response_model=OAuthConnectionStatusResponse,
)
def intervals_status(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    try:
        status = connection_status(
            dependencies.repository(), athlete_alias=dependencies.validated_alias(athlete_alias)
        )
    except (OAuthConfigurationError, PersistentStoreFailure) as exc:
        raise HTTPException(
            status_code=503, detail="Intervals OAuth connection is unavailable"
        ) from exc
    return OAuthConnectionStatusResponse(
        connected=status.connected, scopes=list(status.scopes)
    )


@router.get("/api/v2/integrations/intervals/callback", include_in_schema=False)
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
        repository = dependencies.repository()
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


@router.post("/api/v2/session/exchange", response_model=SessionExchangeResponse)
def exchange_session(
    body: SessionExchangeRequest,
    authorization: Annotated[str | None, Header()] = None,
):
    dependencies.authorize(authorization)
    if not 32 <= len(body.ticket) <= 128:
        raise HTTPException(status_code=400, detail="Login ticket is invalid")
    try:
        athlete_alias = dependencies.repository().consume_login_ticket(body.ticket)
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if athlete_alias is None or dependencies.validated_alias(athlete_alias, fallback=False) is None:
        raise HTTPException(status_code=401, detail="Login ticket is invalid or expired")
    return SessionExchangeResponse(athlete_alias=athlete_alias)
