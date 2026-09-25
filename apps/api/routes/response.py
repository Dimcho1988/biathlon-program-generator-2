"""Response HTTP routes."""

from datetime import date
from typing import Annotated
from uuid import UUID
from fastapi import Header, HTTPException
from ..oauth_store import PersistentStoreFailure
from ..response_monitoring import DailyReport, SessionReport, ResponseBlock, OptionalTest
from .. import response_service
from fastapi import APIRouter
from .. import dependencies

router = APIRouter()


@router.get("/api/v2/athlete/response")
def response_history(period_start: date | None = None, period_end: date | None = None,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None):
    dependencies.authorize(authorization)
    alias = dependencies.validated_alias(athlete_alias, fallback=False)
    if not alias:
        raise HTTPException(401, "Athlete session is required")
    try:
        return response_service.history(dependencies.repository(), alias, period_start, period_end)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Response storage is unavailable") from exc


def _save_response(kind, body, authorization, athlete_alias, actor):
    dependencies.authorize(authorization)
    alias = dependencies.validated_alias(athlete_alias, fallback=False)
    if not alias or not actor:
        raise HTTPException(401, "Athlete and actor sessions are required")
    try:
        return response_service.save_report(dependencies.repository(), alias, kind, body, actor)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Response storage is unavailable") from exc


@router.put("/api/v2/athlete/response/daily")
def save_daily_response(body: DailyReport,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None):
    return _save_response("DAILY",body,authorization,athlete_alias,actor)


@router.put("/api/v2/athlete/response/session")
def save_session_response(body: SessionReport,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None):
    return _save_response("SESSION",body,authorization,athlete_alias,actor)


@router.put("/api/v2/athlete/response/block")
def save_response_block(body: ResponseBlock,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None):
    return _save_response("BLOCK",body,authorization,athlete_alias,actor)


@router.put("/api/v2/athlete/response/test")
def save_optional_response_test(body: OptionalTest,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None):
    return _save_response("TEST",body,authorization,athlete_alias,actor)
