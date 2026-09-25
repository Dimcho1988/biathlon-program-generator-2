"""Settings HTTP routes."""

from datetime import date
from typing import Annotated
from fastapi import Header, HTTPException
from biathlon.methodology import CANONICAL_METHODOLOGY_VERSION, canonical_methodology
from ..cloud import (
    MESOCYCLE_ACCENT_COMPONENTS,
    AthleteMesocycleAccentPreferences,
    AthleteModelSettings,
    AthletePlanningCalendar,
    AthletePlanningCalendarEvent,
    AthletePlanningProfile,
    planning_generation_context,
)
from ..oauth_store import PersistentStoreFailure, SupabasePilotRepository
from ..schemas import (
    AthleteSettingsInput,
    AthleteSettingsResponse,
    AthletePlanningProfileInput,
    AthletePlanningProfileResponse,
    MesocycleAccentPreferencesInput,
    MesocycleAccentPreferencesResponse,
    MesocycleAccentResolution,
    PlanningCalendarInput,
    PlanningCalendarResponse,
    PlanningGenerationContext,
    PlanningMethodologyMetadata,
)
from .. import model_service
from fastapi import APIRouter
from .. import dependencies

router = APIRouter()


def _model_snapshot(repository, alias):
    return model_service.project_recovery(repository,alias,repository.latest(alias))


@router.get("/api/v2/athlete/settings", response_model=AthleteSettingsResponse)
def athlete_settings(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    try:
        settings = dependencies.repository().athlete_settings(dependencies.validated_alias(athlete_alias))
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
        hrmax_bpm=settings.hrmax_bpm,
    )


@router.put("/api/v2/athlete/settings", response_model=AthleteSettingsResponse)
def update_athlete_settings(
    body: AthleteSettingsInput,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    resolved_alias = dependencies.validated_alias(athlete_alias)
    try:
        settings = AthleteModelSettings(
            body.hr_zone_bounds_bpm, body.timezone.strip(), body.hrmax_bpm
        ).validate()
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Six increasing HR boundaries and a valid timezone are required",
        ) from exc
    try:
        repository = dependencies.repository()
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
        hrmax_bpm=settings.hrmax_bpm,
    )


@router.get(
    "/api/v2/planning/methodology",
    response_model=PlanningMethodologyMetadata,
)
def planning_methodology(
    authorization: Annotated[str | None, Header()] = None,
):
    dependencies.authorize(authorization)
    return PlanningMethodologyMetadata.model_validate(canonical_methodology())


@router.get(
    "/api/v2/athlete/planning-profile",
    response_model=AthletePlanningProfileResponse,
)
def athlete_planning_profile(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    try:
        profile = dependencies.repository().athlete_planning_profile(
            dependencies.validated_alias(athlete_alias)
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


@router.put(
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
    dependencies.authorize(authorization)
    resolved_alias = dependencies.validated_alias(athlete_alias)
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
        repository = dependencies.repository()
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


def _mesocycle_accent_response(
    preferences: AthleteMesocycleAccentPreferences | None,
) -> MesocycleAccentPreferencesResponse:
    if preferences is None:
        return MesocycleAccentPreferencesResponse(configured=False)
    return MesocycleAccentPreferencesResponse(
        configured=True,
        preferences=MesocycleAccentPreferencesInput.model_validate(
            preferences.to_payload()
        ),
        resolution=MesocycleAccentResolution.model_validate(
            {
                "methodology_version": CANONICAL_METHODOLOGY_VERSION,
                **preferences.resolution_preview(),
            }
        ),
    )


@router.get(
    "/api/v2/athlete/mesocycle-accent-preferences",
    response_model=MesocycleAccentPreferencesResponse,
)
def athlete_mesocycle_accent_preferences(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    try:
        preferences = dependencies.repository().athlete_mesocycle_accent_preferences(
            dependencies.validated_alias(athlete_alias)
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    return _mesocycle_accent_response(preferences)


@router.put(
    "/api/v2/athlete/mesocycle-accent-preferences",
    response_model=MesocycleAccentPreferencesResponse,
)
def update_athlete_mesocycle_accent_preferences(
    body: MesocycleAccentPreferencesInput,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    resolved_alias = dependencies.validated_alias(athlete_alias)
    selected = set(body.manual_components)
    try:
        if len(selected) != len(body.manual_components):
            raise ValueError("manual mesocycle accents must be unique")
        preferences = AthleteMesocycleAccentPreferences(
            schema_version=body.schema_version,
            accent_mode=body.accent_mode,
            accent_limit=body.accent_limit,
            manual_components=tuple(
                component
                for component in MESOCYCLE_ACCENT_COMPONENTS
                if component in selected
            ),
        ).validate()
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Mesocycle accent preferences are inconsistent",
        ) from exc
    try:
        repository = dependencies.repository()
        if repository.athlete_planning_profile(resolved_alias) is None:
            raise HTTPException(
                status_code=409,
                detail="Athlete planning profile must be configured first",
            )
        repository.save_athlete_mesocycle_accent_preferences(
            resolved_alias, preferences
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    return _mesocycle_accent_response(preferences)


def _planning_calendar_response(
    repository: SupabasePilotRepository,
    athlete_alias: str,
    calendar: AthletePlanningCalendar | None,
) -> PlanningCalendarResponse:
    context = planning_generation_context(
        calendar=calendar,
        profile=repository.athlete_planning_profile(athlete_alias),
        accent_preferences=repository.athlete_mesocycle_accent_preferences(
            athlete_alias
        ),
        training_snapshot=_model_snapshot(repository,athlete_alias),
        as_of=date.today(),
    )
    return PlanningCalendarResponse(
        configured=calendar is not None,
        calendar=(
            PlanningCalendarInput.model_validate(calendar.to_payload())
            if calendar is not None
            else None
        ),
        context=PlanningGenerationContext.model_validate(context),
    )


@router.get(
    "/api/v2/athlete/planning-calendar",
    response_model=PlanningCalendarResponse,
)
def athlete_planning_calendar(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    resolved_alias = dependencies.validated_alias(athlete_alias)
    try:
        repository = dependencies.repository()
        calendar = repository.athlete_planning_calendar(resolved_alias)
        return _planning_calendar_response(repository, resolved_alias, calendar)
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc


@router.put(
    "/api/v2/athlete/planning-calendar",
    response_model=PlanningCalendarResponse,
)
def update_athlete_planning_calendar(
    body: PlanningCalendarInput,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    resolved_alias = dependencies.validated_alias(athlete_alias)
    try:
        calendar = AthletePlanningCalendar(
            schema_version=body.schema_version,
            events=tuple(
                AthletePlanningCalendarEvent(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    name=event.name,
                    start_date=event.start_date,
                    end_date=event.end_date,
                )
                for event in body.events
            ),
        ).validate()
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Planning calendar values are inconsistent"
        ) from exc
    try:
        repository = dependencies.repository()
        if repository.athlete_settings(resolved_alias) is None:
            raise HTTPException(
                status_code=409,
                detail="Athlete HR zones and timezone must be configured first",
            )
        repository.save_athlete_planning_calendar(resolved_alias, calendar)
        return _planning_calendar_response(repository, resolved_alias, calendar)
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
