"""Management HTTP routes."""

from datetime import date, datetime, timezone
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo
from fastapi import Header, HTTPException, Query, Response
from ..oauth_store import PersistentStoreFailure
from ..component_load_context import ComponentCapacityUnavailable
from ..component_load_projection import ComponentLoadRefreshRequired
from .. import race_duration
from ..management_schemas import (
    ManagementProfileWrite,
    ManagementGenerateRequest,
    ManagementActivateRequest,
    ManagementPlanAction,
    ManagementDayAction,
    RaceDurationRequest,
    MethodLibraryPreferencesWrite,
)
from ..management_store import ManagementStore
from ..management_plan_store import PlanStore
from .. import management_service
from .. import management_lifecycle
from .. import method_library_service
from ..read_session import ReadSession
from fastapi import APIRouter
from .. import dependencies
from ..sync_service import public_sync_state
from ..management_projection import public_management
from ..request_metrics import timed_stage

router = APIRouter()


def _active_response(value, check=None):
    result = {**value, "capabilities": {"unchanged_checks": True}}
    if check is not None:
        result["action_result"] = {"operation": "CHECK_NO_CHANGE", "check_id": check["check_id"],
            "checked_revision": check["checked_revision"], "recorded_at": check["recorded_at"]}
    return result


@router.get("/api/v2/athlete/management/methods")
def management_methods(
    phase: Literal["GENERAL_PREPARATION", "SPECIAL_PREPARATION", "PRECOMPETITION", "COMPETITION", "RE_ENTRY", "TRANSITION"] | None = None,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    alias = dependencies.model_alias(authorization, athlete_alias)
    try:
        return method_library_service.view(ReadSession(dependencies.repository()), alias, phase=phase)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Training methods are temporarily unavailable") from exc


@router.put("/api/v2/athlete/management/methods/preferences")
def save_management_method_preferences(
    body: MethodLibraryPreferencesWrite,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
    actor: Annotated[UUID | None, Header(alias="X-OnFlows-Actor-Id")] = None,
):
    alias = dependencies.model_alias(authorization, athlete_alias)
    if actor is None:
        raise HTTPException(401, "Actor session is required")
    try:
        return method_library_service.save_preferences(dependencies.repository(), alias, body, actor)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Training method preferences could not be saved") from exc


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
        return public_management(management_service.profile_view(dependencies.repository(), alias, now=datetime.now(timezone.utc)))
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Management storage is unavailable") from exc


@router.get("/api/v2/athlete/management/view")
def management_view(
    view: Literal["week", "overview"] = "week",
    projection: Literal["overview-v1"] | None = None,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    """Assemble one page with request-local read reuse; never modify a plan."""
    alias = dependencies.model_alias(authorization, athlete_alias)
    repository = ReadSession(dependencies.repository(), planning_settings_bundle=True)
    compact = view == "overview" and projection == "overview-v1"
    try:
        capture = getattr(repository, "capture_planning_inputs", None)
        if callable(capture):
            capture(alias)
        with timed_stage("profile"):
            profile = management_service.profile_view(repository, alias, include_history=False) if compact else management_service.profile_view(repository, alias)
        with timed_stage("overview_active" if compact else "current"):
            active = management_lifecycle.current_overview(repository, alias) if compact else management_lifecycle.current(repository, alias)
        with timed_stage("drafts"):
            drafts = management_service.history(repository, alias) if view == "week" else {"drafts": []}
        with timed_stage("outlook"):
            outlook = management_service.outlook(repository, alias) if view == "overview" else None
        try:
            sync = public_sync_state(repository.sync_state(alias))
        except (PersistentStoreFailure, ValueError):
            sync = None
        with timed_stage("projection"):
            result = {"profile": profile, "drafts": drafts, "outlook": outlook, "sync": sync}
            result.update({"schema_version": "management-overview-v1", "overview_active": active} if compact else {"active": _active_response(active)})
            return public_management(result)
    except (ComponentCapacityUnavailable, ComponentLoadRefreshRequired) as exc:
        raise HTTPException(409, exc.user_message) from exc
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
        repository = dependencies.repository()
        store = ManagementStore(repository)
        payload = body.profile.model_dump(mode="json")
        retained_profile = None
        if "method_library" in body.profile.model_fields_set:
            payload["method_library"] = method_library_service.validate_preferences(payload["method_library"], payload)
        else:
            # Older profile editors do not know the new section. Their writes
            # must not reset settings saved on the method-library page.
            current = store.profile(alias)
            retained_profile = current.get("profile") or {}
            payload["method_library"] = (current.get("profile") or {}).get("method_library", payload["method_library"])
        if "ant_references" not in body.profile.model_fields_set:
            # An older editor cannot erase a measured/manual reference merely
            # by omitting a field it does not yet understand.
            if retained_profile is None:
                retained_profile = (store.profile(alias).get("profile") or {})
            payload["ant_references"] = retained_profile.get("ant_references", [])
        if body.profile.lactate_profiles:
            settings = repository.athlete_settings(alias)
            today = datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone if settings else "UTC")).date()
            if any(p.assessed_on > today for p in body.profile.lactate_profiles):
                raise HTTPException(422, "A lactate observation cannot be in the future")
        result = store.save_profile(
            alias, payload, body.expected_revision, actor,
        )
        return {"configured": True, "profile": payload,
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
        return public_management(management_service.outlook(dependencies.repository(), alias))
    except (ComponentCapacityUnavailable, ComponentLoadRefreshRequired) as exc:
        raise HTTPException(409, exc.user_message) from exc
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
        return public_management(management_service.history(dependencies.repository(), alias, start_date=start_date))
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
        return public_management(management_service.generate(dependencies.repository(), alias, body, actor))
    except (ComponentCapacityUnavailable, ComponentLoadRefreshRequired) as exc:
        raise HTTPException(409, exc.user_message) from exc
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "A management draft could not be saved") from exc


@router.get("/api/v2/athlete/management/active")
def management_active(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    alias = dependencies.model_alias(authorization, athlete_alias)
    try:
        return public_management(_active_response(management_lifecycle.current(
            ReadSession(dependencies.repository(), planning_settings_bundle=True), alias)))
    except (ComponentCapacityUnavailable, ComponentLoadRefreshRequired) as exc:
        raise HTTPException(409, exc.user_message) from exc
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Active planning is temporarily unavailable") from exc


@router.get("/api/v2/athlete/management/checks")
def management_checks(
    response: Response,
    before: Annotated[int | None, Query(ge=1, le=9007199254740991)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    alias = dependencies.model_alias(authorization, athlete_alias)
    response.headers["Cache-Control"] = "no-store"
    try:
        return PlanStore(dependencies.repository()).checks(alias, before=before, limit=limit)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Plan check history is temporarily unavailable") from exc


@router.get("/api/v2/athlete/management/state")
def management_state(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[str | None, Header(alias="X-OnFlows-Athlete-Alias")] = None,
):
    alias = dependencies.model_alias(authorization, athlete_alias)
    try:
        return management_lifecycle.state(
            ReadSession(dependencies.repository(), planning_settings_bundle=True), alias)
    except PersistentStoreFailure as exc:
        raise HTTPException(503, "Planning status is temporarily unavailable") from exc


def _management_action(operation, body, authorization, athlete_alias, actor):
    alias = dependencies.model_alias(authorization, athlete_alias)
    if actor is None:
        raise HTTPException(401, "Actor session is required")
    try:
        repository = dependencies.repository()
        if operation == "activate":
            saved = management_lifecycle.activate(repository, alias, body, actor, include_saved_payload=False)
        elif operation == "day":
            saved = management_lifecycle.refresh(repository, alias, actor, expected_revision=body.expected_revision,
                                         force=True, day_action=body, include_saved_payload=False)
        else:
            saved = management_lifecycle.action(repository, alias, body, actor, include_saved_payload=False)
        check = saved.get("unchanged_check") if isinstance(saved, dict) else None
        return public_management(_active_response(management_lifecycle.current(repository, alias), check))
    except (ComponentCapacityUnavailable, ComponentLoadRefreshRequired) as exc:
        raise HTTPException(409, exc.user_message) from exc
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
