"""Authenticated prepared energy view and revisioned nutritional inputs."""

from datetime import date
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Header, HTTPException
from .. import dependencies, energy_service, race_nutrition
from ..prepared_views import endpoint
from ..oauth_store import PersistentStoreFailure

router = APIRouter()


@router.get("/api/v2/athlete/energy/race-reminders")
def race_reminders(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    alias = scope(authorization, athlete_alias)
    try:
        return race_nutrition.reminders(dependencies.repository(), alias)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Nutrition reminders unavailable") from exc


@router.put("/api/v2/athlete/energy/race-loading")
def save_race_loading(
    body: race_nutrition.Decision,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None,
):
    alias = scope(authorization, athlete_alias)
    if actor is None:
        raise HTTPException(401, "Actor session is required")
    try:
        return race_nutrition.save_decision(dependencies.repository(), alias, body, actor)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Nutrition decision unavailable") from exc


def scope(authorization, athlete_alias):
    dependencies.authorize(authorization)
    alias = dependencies.validated_alias(athlete_alias, fallback=False)
    if not alias:
        raise HTTPException(401, "Athlete session is required")
    return alias


@router.get("/api/v2/athlete/energy")
@endpoint("energy")
def energy_history(
    period_start: date | None = None,
    period_end: date | None = None,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    alias = scope(authorization, athlete_alias)
    try:
        return energy_service.history(
            dependencies.repository(), alias, period_start, period_end
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Energy data unavailable") from exc


@router.put("/api/v2/athlete/energy/profile")
def save_nutrition_profile(
    body: energy_service.NutritionProfile,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None,
):
    alias = scope(authorization, athlete_alias)
    if actor is None:
        raise HTTPException(401, "Actor session is required")
    try:
        return energy_service.save_profile(
            dependencies.repository(), alias, body, actor
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Energy settings unavailable") from exc
