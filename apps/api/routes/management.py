"""Management HTTP routes."""

from datetime import date, datetime, timezone
from typing import Annotated, Literal
from uuid import UUID
from fastapi import Header, HTTPException
from ..oauth_store import PersistentStoreFailure
from .. import race_duration
from ..management_schemas import (
    ManagementProfileWrite,
    ManagementGenerateRequest,
    ManagementActivateRequest,
    ManagementPlanAction,
    ManagementDayAction,
    RaceDurationRequest,
)
from ..management_store import ManagementStore
from .. import management_service
from .. import management_lifecycle
from ..read_session import ReadSession
from fastapi import APIRouter
from .. import dependencies
from ..sync_service import public_sync_state

router = APIRouter()


@router.post("/api/v2/athlete/management/race-duration")
def management_race_duration(
    body: RaceDurationRequest,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    alias = dependencies.model_alias(authorization, athlete_alias)
    try:
        return race_duration.preview(dependencies.repository(), alias, body.model_dump())
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Race duration is temporarily unavailable") from exc


@router.get("/api/v2/athlete/management/profile")
def management_profile(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    alias = dependencies.model_alias(authorization, athlete_alias)
    try:
        return management_service.profile_view(dependencies.repository(), alias, now=datetime.now(timezone.utc))
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Management storage is unavailable") from exc


@router.get("/api/v2/athlete/management/view")
def management_view(
    view: Literal["week", "overview"] = "week",
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    """Assemble one page with request-local read reuse; never modify a plan."""
    alias = dependencies.model_alias(authorization, athlete_alias)
    repository = ReadSession(dependencies.repository())
    try:
        profile = management_service.profile_view(repository, alias)
        active = management_lifecycle.current(repository, alias)
        drafts = management_service.history(repository, alias) if view == "week" else {"drafts": []}
        outlook = management_service.outlook(repository, alias) if view == "overview" else None
        try:
            sync = public_sync_state(repository.sync_state(alias))
        except (PersistentStoreFailure, ValueError):
            sync = None
        return {"profile": profile, "active": active, "drafts": drafts, "outlook": outlook, "sync": sync}
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Management view is unavailable") from exc


@router.put("/api/v2/athlete/management/profile")
def save_management_profile(
    body: ManagementProfileWrite,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None,
):
    alias = dependencies.model_alias(authorization, athlete_alias)
    if actor is None:
        raise HTTPException(401, "Actor session is required")
    try:
        result = ManagementStore(dependencies.repository()).save_profile(
            alias, body.profile.model_dump(mode="json"), body.expected_revision, actor,
        )
        return {"configured": True, "profile": body.profile.model_dump(mode="json"),
                "revision": result["revision"]}
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Management profile could not be saved") from exc


@router.get("/api/v2/athlete/management/outlook")
def management_outlook(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    alias = dependencies.model_alias(authorization, athlete_alias)
    try:
        return management_service.outlook(dependencies.repository(), alias)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Training outlook is unavailable") from exc


@router.get("/api/v2/athlete/management/drafts")
def management_drafts(
    start_date: date | None = None,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    alias = dependencies.model_alias(authorization, athlete_alias)
    try:
        return management_service.history(dependencies.repository(), alias, start_date=start_date)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Management drafts are unavailable") from exc


@router.post("/api/v2/athlete/management/generate")
def generate_management_draft(
    body: ManagementGenerateRequest,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None,
):
    alias = dependencies.model_alias(authorization, athlete_alias)
    if actor is None:
        raise HTTPException(401, "Actor session is required")
    try:
        return management_service.generate(dependencies.repository(), alias, body, actor)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "A management draft could not be saved") from exc


@router.get("/api/v2/athlete/management/active")
def management_active(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    alias = dependencies.model_alias(authorization, athlete_alias)
    try:
        return management_lifecycle.current(dependencies.repository(), alias)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Active planning is temporarily unavailable") from exc


def _management_action(operation, body, authorization, athlete_alias, actor):
    alias = dependencies.model_alias(authorization, athlete_alias)
    if actor is None:
        raise HTTPException(401, "Actor session is required")
    try:
        repository = dependencies.repository()
        if operation == "activate":
            management_lifecycle.activate(repository, alias, body, actor)
        elif operation == "day":
            management_lifecycle.refresh(repository, alias, actor, expected_revision=body.expected_revision,
                                         force=True, day_action=body)
        else:
            management_lifecycle.action(repository, alias, body, actor)
        return management_lifecycle.current(repository, alias)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "The active plan could not be updated") from exc


@router.post("/api/v2/athlete/management/activate")
def management_activate(body: ManagementActivateRequest,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None):
    return _management_action("activate", body, authorization, athlete_alias, actor)


@router.post("/api/v2/athlete/management/action")
def management_plan_action(body: ManagementPlanAction,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None):
    return _management_action("action", body, authorization, athlete_alias, actor)


@router.post("/api/v2/athlete/management/day")
def management_day_action(body: ManagementDayAction,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None):
    return _management_action("day", body, authorization, athlete_alias, actor)
