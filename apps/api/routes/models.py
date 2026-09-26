"""Models HTTP routes."""

import re
from typing import Annotated
from uuid import UUID
from fastapi import Header, HTTPException
from ..oauth_store import PersistentStoreFailure
from .. import model_service
from ..model_schemas import RecoveryConfigInput, SpeedTestInput, ManualSpeedTestInput
from fastapi import APIRouter
from .. import dependencies

router = APIRouter()


@router.get("/api/v2/athlete/models/recovery")
def recovery_configuration(authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None):
    alias = dependencies.model_alias(authorization, athlete_alias)
    try:
        return model_service.ModelStore(dependencies.repository()).config(alias)
    except PersistentStoreFailure as exc:
        raise HTTPException(503,"Model settings are unavailable") from exc


@router.put("/api/v2/athlete/models/recovery")
def save_recovery_configuration(body: RecoveryConfigInput,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None):
    alias = dependencies.model_alias(authorization, athlete_alias)
    if actor is None: raise HTTPException(401,"Actor session is required")
    try:
        return model_service.save_config(dependencies.repository(),alias,body,actor)
    except PersistentStoreFailure as exc:
        raise HTTPException(503,"Model settings could not be saved") from exc


@router.get("/api/v2/athlete/models/speed")
def speed_model(sport: str | None = None, duration_s: float | None = None,
    distance_m: float | None = None, speed_kmh: float | None = None, hr_bpm: float | None = None,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None):
    alias = dependencies.model_alias(authorization, athlete_alias)
    try:
        return model_service.speed_view(dependencies.repository(),alias,sport,duration_s=duration_s,distance_m=distance_m,speed_kmh=speed_kmh,hr_bpm=hr_bpm)
    except PersistentStoreFailure as exc:
        raise HTTPException(503,"Speed model sources are unavailable") from exc


@router.put("/api/v2/athlete/models/speed-test")
def save_speed_test(body: SpeedTestInput,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None):
    alias = dependencies.model_alias(authorization, athlete_alias)
    if actor is None: raise HTTPException(401,"Actor session is required")
    try:
        return model_service.save_test(dependencies.repository(),alias,body,actor)
    except PersistentStoreFailure as exc:
        raise HTTPException(503,"Speed test could not be saved") from exc


@router.put("/api/v2/athlete/models/speed-test-manual")
def save_manual_speed_test(body: ManualSpeedTestInput,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None):
    alias = dependencies.model_alias(authorization, athlete_alias)
    if actor is None: raise HTTPException(401,"Actor session is required")
    try:
        return model_service.save_manual_test(dependencies.repository(),alias,body,actor)
    except PersistentStoreFailure as exc:
        raise HTTPException(503,"Speed test could not be saved") from exc


@router.get("/api/v2/athlete/models/speed-preview")
def speed_test_preview(activity_ref: str, start_s: int | None = None, duration_s: int | None = None, test_mode: str = "STRICT",
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None):
    from ..speed_segments import preview
    alias = dependencies.model_alias(authorization, athlete_alias)
    if not re.fullmatch(r"act_[a-f0-9]{32}", activity_ref):
        raise HTTPException(422, "Invalid activity reference")
    if ((start_s is None) != (duration_s is None) or
        start_s is not None and not 0 <= start_s <= 172800 or
        duration_s is not None and not 11 <= duration_s <= 43516):
        raise HTTPException(422, "Invalid segment bounds")
    try:
        return preview(dependencies.repository(), alias, activity_ref, start_s, duration_s, test_mode)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Speed test preview is unavailable") from exc
