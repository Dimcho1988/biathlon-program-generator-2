"""Authenticated assistant gateway. No generic HTTP/SQL tools are exposed."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import assistant_service, dependencies
from ..oauth_store import PersistentStoreFailure
from ..assistant_provider import AssistantProviderError

router = APIRouter()


class AssistantTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID | None = None
    operation_id: UUID
    message: str = Field(min_length=1,max_length=4000)


class AssistantClear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID


def _scope(authorization,alias,actor):
    dependencies.authorize(authorization)
    alias=dependencies.validated_alias(alias,fallback=False)
    if not alias or actor is None:
        raise HTTPException(401,"Необходима е удостоверена сесия на спортиста и потребителя.")
    return dependencies.repository(),alias,str(actor)


def _unavailable(exc):
    raise HTTPException(503,"Асистентът временно не е достъпен. Останалите екрани и формуляри продължават да работят.") from exc


@router.get("/api/v2/athlete/assistant")
def assistant_overview(
    conversation_id: UUID | None = Query(default=None), operation_id: UUID | None = Query(default=None),
    authorization: Annotated[str | None,Header()] = None,
    athlete_alias: Annotated[str | None,Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None,Header(alias="X-OnFlows-Actor-Id")] = None):
    repository,alias,actor=_scope(authorization,athlete_alias,actor)
    try:
        return assistant_service.overview(repository,alias,actor,str(conversation_id) if conversation_id else None,
            str(operation_id) if operation_id else None)
    except (PersistentStoreFailure,AssistantProviderError) as exc:
        _unavailable(exc)


@router.post("/api/v2/athlete/assistant")
def assistant_turn(body:AssistantTurn,
    authorization: Annotated[str | None,Header()] = None,
    athlete_alias: Annotated[str | None,Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None,Header(alias="X-OnFlows-Actor-Id")] = None):
    repository,alias,actor=_scope(authorization,athlete_alias,actor)
    try:
        return assistant_service.turn(repository,alias,actor,body.model_dump(mode="json"))
    except (PersistentStoreFailure,AssistantProviderError) as exc:
        _unavailable(exc)


@router.delete("/api/v2/athlete/assistant")
def assistant_clear(body:AssistantClear,
    authorization: Annotated[str | None,Header()] = None,
    athlete_alias: Annotated[str | None,Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None,Header(alias="X-OnFlows-Actor-Id")] = None):
    repository,alias,actor=_scope(authorization,athlete_alias,actor)
    try:
        return assistant_service.clear(repository,alias,actor,str(body.conversation_id))
    except PersistentStoreFailure as exc:
        _unavailable(exc)
