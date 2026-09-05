"""FastAPI entry point for the onFlows read-only API."""

from datetime import date, datetime, timedelta, timezone
import logging
import os
import re
import secrets
from typing import Annotated, Mapping
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from biathlon.methodology import (
    CANONICAL_METHODOLOGY_VERSION,
    canonical_methodology,
)
from hrmod_lab.schemas import MODEL_VERSION as HRMOD_MODEL_VERSION
from vflat_b65 import MODEL_VERSION as VFLAT_MODEL_VERSION
from vflat_b65 import SPRINT_STR_MODEL_VERSION

from .cloud import (
    MESOCYCLE_ACCENT_COMPONENTS,
    AthleteMesocycleAccentPreferences,
    AthleteModelSettings,
    AthletePlanningCalendar,
    AthletePlanningCalendarEvent,
    AthletePlanningProfile,
    planning_generation_context,
    service_token_valid,
)
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
from .activity_catalog import (
    ACTIVITY_REF_PATTERN,
    activity_calendar_payload,
    activity_detail_payload,
    downsample_model_input,
)
from .real_service import (
    completed_work_from_load_history,
    load_history_from_persisted,
    recovery_source_supports_restore,
    recovery_history_from_persisted,
    training_status_from_persisted,
    volume_history_from_load_history,
)
from .schemas import (
    AthleteSettingsInput,
    AthleteSettingsResponse,
    AthleteSnapshot,
    ActivityCalendarResponse,
    ActivityDetailResponse,
    ActivitySeriesResponse,
    ActivityViewResponse,
    AthletePlanningProfileInput,
    AthletePlanningProfileResponse,
    CompletedWorkResponse,
    DashboardViewResponse,
    HealthResponse,
    LoadHistoryResponse,
    MesocycleAccentPreferencesInput,
    MesocycleAccentPreferencesResponse,
    MesocycleAccentResolution,
    ModelHealthResponse,
    OAuthAuthorizationResponse,
    OAuthConnectionStatusResponse,
    PlanningCalendarInput,
    PlanningCalendarResponse,
    PlanningGenerationContext,
    PlanningMethodologyMetadata,
    RecoveryHistoryResponse,
    SessionExchangeRequest,
    SessionExchangeResponse,
    SyncEnqueueResponse,
    SyncJobRequest,
    SyncStateResponse,
    TrainingStatusResponse,
    VolumeHistoryResponse,
)
from .sync_contracts import PUBLIC_SCOPE_BY_JOB_KIND
from .training_status import build_demo_training_status

app = FastAPI(title="onFlows API", version="1.0.0")
logger = logging.getLogger(__name__)
ATHLETE_ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
SAFE_WEB_NOTICE_PATTERN = re.compile(r"^[a-z0-9-]{1,64}$")
ACTIVITY_SHADOW_REF_PATTERN = re.compile(r"^(?:shadow-|act_)[a-f0-9]{32}$")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Keep Render liveness independent from the synchronous worker pool."""

    return HealthResponse(status="ok")


@app.get("/health/models", response_model=ModelHealthResponse)
async def model_health() -> ModelHealthResponse:
    """Expose non-sensitive shadow versions for deployment verification."""

    from .shadow_models.hrmod_v4 import SOURCE_COMMIT

    return ModelHealthResponse(
        status="ok",
        vflat_model_version=VFLAT_MODEL_VERSION,
        sprint_str_model_version=SPRINT_STR_MODEL_VERSION,
        hrmod_model_version=HRMOD_MODEL_VERSION,
        hrmod_source_commit=SOURCE_COMMIT,
    )


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


SYNC_SCOPE_BY_JOB_KIND = dict(PUBLIC_SCOPE_BY_JOB_KIND)
SYNC_JOB_KIND_BY_SCOPE = {
    scope: job_kind for job_kind, scope in SYNC_SCOPE_BY_JOB_KIND.items()
}


def _optional_iso(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        rendered = value.strip()
        if not rendered or len(rendered) > 64:
            raise ValueError("Sync timestamp is invalid")
        return rendered
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        rendered = isoformat()
        if isinstance(rendered, str) and 0 < len(rendered) <= 64:
            return rendered
    raise ValueError("Sync timestamp is invalid")


def _enqueue_sync_job(
    *,
    repository: SupabasePilotRepository,
    athlete_alias: str,
    scope: str,
) -> SyncEnqueueResponse:
    effective_scope = scope
    if scope in {"WELLNESS", "RECOVERY"}:
        active_reader = getattr(repository, "active_analysis", None)
        active = active_reader(athlete_alias) if callable(active_reader) else None
        source = active.get("snapshot_payload") if isinstance(active, Mapping) else None
        generation_id = (
            active.get("generation_id") if isinstance(active, Mapping) else None
        )
        has_generation_base = (
            isinstance(generation_id, str) and bool(generation_id.strip())
        )
        if not has_generation_base or (
            scope == "RECOVERY"
            and (
                not isinstance(source, Mapping)
                or not recovery_source_supports_restore(source)
            )
        ):
            effective_scope = "FULL"
    job_kind = SYNC_JOB_KIND_BY_SCOPE[effective_scope]
    settings = repository.athlete_settings(athlete_alias)
    timezone_name = (
        settings.get("timezone")
        if isinstance(settings, Mapping)
        else getattr(settings, "timezone", None)
    )
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        configured_alias = os.environ.get("ONFLOWS_ATHLETE_ALIAS", "").strip()
        if athlete_alias == configured_alias:
            timezone_name = os.environ.get(
                "ONFLOWS_ATHLETE_TIMEZONE", ""
            ).strip()
    try:
        athlete_timezone = ZoneInfo(str(timezone_name).strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Athlete timezone is unavailable") from exc
    analysis_date = datetime.now(timezone.utc).astimezone(athlete_timezone).date()
    row = repository.enqueue_sync_job(
        athlete_alias=athlete_alias,
        job_kind=job_kind,
        # A retried HTTP request is coalesced with any unfinished job of the
        # same kind by the repository. A fresh intent after success receives
        # a new key and therefore starts a new generation.
        idempotency_key=secrets.token_hex(32),
        request_payload={
            "schema_version": "sync-request-v1",
            "scope": effective_scope,
            "as_of": analysis_date.isoformat(),
        },
    )
    status = str(row.get("status") or "")
    if status == "RETRY_WAIT":
        status = "QUEUED"
    if status not in {"QUEUED", "RUNNING"}:
        raise PersistentStoreFailure("Sync queue returned an invalid state")
    job_id = row.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise PersistentStoreFailure("Sync queue returned an invalid job")
    return SyncEnqueueResponse(
        schema_version="sync-enqueue-v1",
        job_id=job_id,
        scope=effective_scope,
        state=status,
        coalesced=bool(row.get("deduplicated", False)),
    )


def _public_sync_state(row: Mapping[str, object]) -> SyncStateResponse:
    internal_state = row.get("status")
    state = "IDLE" if internal_state is None else str(internal_state)
    if state not in {
        "IDLE",
        "QUEUED",
        "RUNNING",
        "RETRY_WAIT",
        "SUCCEEDED",
        "FAILED",
        "SUPERSEDED",
    }:
        raise ValueError("Sync state is invalid")
    internal_kind = row.get("job_kind")
    scope = None if internal_kind is None else SYNC_SCOPE_BY_JOB_KIND.get(
        str(internal_kind)
    )
    if internal_kind is not None and scope is None:
        raise ValueError("Sync job kind is invalid")
    raw_progress = row.get("progress_percent")
    progress = (
        100.0
        if state == "SUCCEEDED"
        else 0.0
        if raw_progress is None
        else float(raw_progress)
    )
    if not 0.0 <= progress <= 100.0:
        raise ValueError("Sync progress is invalid")
    revision = int(row.get("active_revision") or 0)
    if revision < 0:
        raise ValueError("Active revision is invalid")
    job_id = row.get("job_id")
    generation_id = row.get("active_generation_id")
    if job_id is not None and not isinstance(job_id, str):
        raise ValueError("Sync job identity is invalid")
    if generation_id is not None and not isinstance(generation_id, str):
        raise ValueError("Generation identity is invalid")
    return SyncStateResponse(
        schema_version="sync-state-v1",
        job_id=job_id,
        scope=scope,
        state=state,
        stage=(
            str(row["progress_stage"])
            if row.get("progress_stage") is not None
            else None
        ),
        progress_percent=progress,
        requested_at=_optional_iso(row.get("requested_at")),
        started_at=_optional_iso(row.get("started_at")),
        finished_at=_optional_iso(row.get("completed_at")),
        retry_at=(
            _optional_iso(row.get("available_at"))
            if state == "RETRY_WAIT"
            else None
        ),
        failure_code=(
            str(row["error_code"])
            if row.get("error_code") is not None
            else None
        ),
        active_generation_id=generation_id,
        active_revision=revision,
        analysis_as_of=_optional_iso(row.get("active_as_of")),
        activated_at=_optional_iso(row.get("activated_at")),
    )


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


@app.get(
    "/api/v2/real/dashboard-view",
    response_model=DashboardViewResponse,
)
def real_dashboard_view(
    period_start: date | None = None,
    period_end: date | None = None,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    """Read all dashboard aggregates from one activated generation."""

    _authorize(authorization)
    try:
        envelope = _repository().active_analysis(_validated_alias(athlete_alias))
        if not isinstance(envelope, Mapping):
            raise ValueError("No active analysis is available")
        payload = envelope.get("snapshot_payload")
        if not isinstance(payload, Mapping):
            raise ValueError("Active analysis payload is invalid")
        snapshot = AthleteSnapshot.model_validate(payload)
        revision = int(envelope.get("revision") or 0)
        if revision < 0:
            raise ValueError("Active revision is invalid")
        generation_id = envelope.get("generation_id")
        if generation_id is not None and not isinstance(generation_id, str):
            raise ValueError("Active generation identity is invalid")
        try:
            completed_work = completed_work_from_load_history(
                snapshot.load_history,
                period_start,
                period_end,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Requested period must be within the active analysis",
            ) from exc
        volume_history = volume_history_from_load_history(snapshot.load_history)
        return DashboardViewResponse(
            schema_version="dashboard-view-v1",
            generation_id=generation_id,
            revision=revision,
            analysis_as_of=_optional_iso(envelope.get("analysis_as_of")),
            activated_at=_optional_iso(envelope.get("activated_at")),
            training_status=snapshot.training_status,
            completed_work=completed_work,
            load_history=snapshot.load_history,
            recovery_history=snapshot.recovery_history,
            volume_history=volume_history,
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="No coherent active analysis is available"
        ) from exc


@app.post(
    "/api/v2/real/recovery/restore",
    response_model=SyncEnqueueResponse,
    status_code=202,
)
def restore_real_recovery_history(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    """Compatibility route: enqueue recovery restoration for a worker."""

    return _enqueue_legacy_scope(
        scope="RECOVERY",
        authorization=authorization,
        athlete_alias=athlete_alias,
    )


@app.get("/api/v2/real/activity-shadows")
def real_activity_shadow_index(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        rows = _repository().activity_shadow_index(_validated_alias(athlete_alias))
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    return {"schema_version": "activity-shadow-index-v1", "activities": rows}


@app.get("/api/v2/real/activities", response_model=ActivityCalendarResponse)
def real_activity_calendar(
    period_start: date | None = None,
    period_end: date | None = None,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    end = period_end or date.today()
    start = period_start or end - timedelta(days=89)
    if start > end or (end - start).days >= 90:
        raise HTTPException(status_code=422, detail="Activity period must contain 1–90 days")
    alias = _validated_alias(athlete_alias)
    try:
        repository = _repository()
        active_calendar = getattr(repository, "active_activity_calendar", None)
        if callable(active_calendar):
            envelope = active_calendar(alias, start, end)
            if not isinstance(envelope, Mapping):
                raise ValueError("No active activity generation is available")
            raw_rows = envelope.get("activities")
            snapshot = envelope.get("snapshot_payload")
            if not isinstance(raw_rows, list) or not isinstance(snapshot, Mapping):
                raise ValueError("Active activity generation is invalid")
            rows = tuple(
                row for row in raw_rows if isinstance(row, Mapping)
            )
            if len(rows) != len(raw_rows):
                raise ValueError("Active activity rows are invalid")
            shadow_zones = {}
            for row in rows:
                raw_zone_summary = row.get("hrmod_zone_summary")
                if raw_zone_summary is None:
                    raw_zone_summary = []
                if not isinstance(raw_zone_summary, list) or not all(
                    isinstance(item, Mapping) for item in raw_zone_summary
                ):
                    raise ValueError("Pinned HRmod summary is invalid")
                shadow_zones[str(row.get("activity_ref") or "")] = [
                    dict(item) for item in raw_zone_summary
                ]
            generation_metadata: Mapping[str, object] | None = {
                "generation_id": envelope.get("generation_id"),
                "revision": int(envelope.get("revision") or 0),
                "analysis_as_of": _optional_iso(
                    envelope.get("analysis_as_of")
                ),
                "activated_at": _optional_iso(envelope.get("activated_at")),
            }
        else:
            # Test/rollout compatibility only. The production repository
            # implements the one-RPC generation-pinned read above.
            rows = repository.activity_calendar(alias, start, end)
            shadow_zones = repository.activity_shadow_zone_summaries(
                alias,
                tuple(str(row.get("activity_ref") or "") for row in rows),
            )
            snapshot = repository.latest(alias)
            generation_metadata = None
    except (PersistentStoreFailure, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    snapshot_mapping = snapshot if isinstance(snapshot, Mapping) else {}
    snapshot_has_wellness_calendar = "wellness_calendar" in snapshot_mapping
    wellness_days = snapshot_mapping.get("wellness_calendar", [])
    wellness_days = wellness_days if isinstance(wellness_days, list) else []
    recovery_history = snapshot_mapping.get("recovery_history")
    recovery_history = recovery_history if isinstance(recovery_history, Mapping) else {}
    diagnostics = recovery_history.get("wellness_diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    records_received = diagnostics.get("records_received", 0)
    records_received = records_received if isinstance(records_received, int) else 0
    latest_observed_date = diagnostics.get("latest_observed_date")
    latest_observed_date = latest_observed_date if isinstance(latest_observed_date, str) else None
    displayed_wellness_days = [
        day
        for day in wellness_days
        if isinstance(day, Mapping)
        and start.isoformat() <= str(day.get("date") or "") <= end.isoformat()
    ]
    if not snapshot_has_wellness_calendar:
        wellness_state = "refresh_required"
    elif displayed_wellness_days:
        wellness_state = "available"
    elif wellness_days:
        wellness_state = "outside_snapshot_period"
    elif records_received == 0:
        wellness_state = "no_provider_records"
    else:
        wellness_state = "no_recognized_values"
    return activity_calendar_payload(
        athlete_alias=alias,
        period_start=start,
        period_end=end,
        rows=rows,
        shadow_zones=shadow_zones,
        wellness_days=displayed_wellness_days,
        wellness_status={
            "state": wellness_state,
            "records_received": records_received,
            "stored_days": len(wellness_days),
            "displayed_days": len(displayed_wellness_days),
            "latest_observed_date": latest_observed_date,
        },
        generation_metadata=generation_metadata,
    )


def _activity_view_payload(
    row: Mapping[str, object], activity_ref: str
) -> dict[str, object]:
    catalog = row.get("catalog_payload")
    raw_series = row.get("series_payload")
    raw_shadow = row.get("shadow_payload")
    if not isinstance(catalog, Mapping):
        raise ValueError("Activity view catalog is invalid")
    if raw_series is not None and not isinstance(raw_series, Mapping):
        raise ValueError("Activity view series is invalid")
    if raw_shadow is not None and not isinstance(raw_shadow, Mapping):
        raise ValueError("Activity view shadow is invalid")

    revision = row.get("revision")
    generation_id = row.get("generation_id")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or (generation_id is not None and not isinstance(generation_id, str))
        or (generation_id is None) != (revision == 0)
    ):
        raise ValueError("Activity view generation is invalid")

    pointers = {
        "input_key": raw_series,
        "shadow_run_key": raw_shadow,
    }
    for key, payload in pointers.items():
        pointer = row.get(key)
        if pointer is not None and (
            not isinstance(pointer, str) or len(pointer) != 64
        ):
            raise ValueError("Activity view pointer is invalid")
        if (pointer is None) != (payload is None):
            raise ValueError("Activity view payload is not pinned")
    canonical_key = row.get("canonical_run_key")
    if canonical_key is not None and (
        not isinstance(canonical_key, str) or len(canonical_key) != 64
    ):
        raise ValueError("Activity view canonical pointer is invalid")

    shadow_zone_summary = (
        raw_shadow.get("zone_summary")
        if isinstance(raw_shadow, Mapping)
        and isinstance(raw_shadow.get("zone_summary"), list)
        else []
    )
    detail = activity_detail_payload(
        {
            **dict(catalog),
            "activity_ref": activity_ref,
            "latest_canonical_run_key": canonical_key,
            "latest_shadow_run_key": row.get("shadow_run_key"),
            "previous_activity_ref": row.get("previous_activity_ref"),
            "next_activity_ref": row.get("next_activity_ref"),
            "shadow_available": raw_shadow is not None,
        },
        [item for item in shadow_zone_summary if isinstance(item, Mapping)],
    )
    series = None
    if isinstance(raw_series, Mapping):
        series = {
            **downsample_model_input(raw_series),
            "activity_ref": activity_ref,
        }
    return {
        "schema_version": "activity-view-v1",
        "generation_id": generation_id,
        "revision": revision,
        "analysis_as_of": _optional_iso(row.get("analysis_as_of")),
        "activated_at": _optional_iso(row.get("activated_at")),
        "activity": detail,
        "series": series,
        "shadow": dict(raw_shadow) if isinstance(raw_shadow, Mapping) else None,
    }


@app.get(
    "/api/v2/real/activities/{activity_ref}/view",
    response_model=ActivityViewResponse,
)
def real_activity_view(
    activity_ref: str,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    """Return detail, charts and shadow pinned to one active generation."""

    _authorize(authorization)
    if not ACTIVITY_REF_PATTERN.fullmatch(activity_ref):
        raise HTTPException(status_code=422, detail="Invalid activity reference")
    try:
        row = _repository().active_activity_view(
            _validated_alias(athlete_alias), activity_ref
        )
        payload = (
            _activity_view_payload(row, activity_ref)
            if isinstance(row, Mapping)
            else None
        )
    except (PersistentStoreFailure, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="Activity is unavailable")
    return payload


@app.get(
    "/api/v2/real/activities/{activity_ref}",
    response_model=ActivityDetailResponse,
)
def real_activity_detail(
    activity_ref: str,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    if not ACTIVITY_REF_PATTERN.fullmatch(activity_ref):
        raise HTTPException(status_code=422, detail="Invalid activity reference")
    try:
        row = _repository().activity_detail(
            _validated_alias(athlete_alias), activity_ref
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Activity is unavailable")
    return activity_detail_payload(row)


@app.get(
    "/api/v2/real/activities/{activity_ref}/series",
    response_model=ActivitySeriesResponse,
)
def real_activity_series(
    activity_ref: str,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    if not ACTIVITY_REF_PATTERN.fullmatch(activity_ref):
        raise HTTPException(status_code=422, detail="Invalid activity reference")
    try:
        payload = _repository().activity_series(
            _validated_alias(athlete_alias), activity_ref
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="Activity series is unavailable")
    return {**payload, "activity_ref": activity_ref}


@app.get("/api/v2/real/activities/{activity_ref}/shadow")
def real_activity_shadow_detail(
    activity_ref: str,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    if not ACTIVITY_REF_PATTERN.fullmatch(activity_ref):
        raise HTTPException(status_code=422, detail="Invalid activity reference")
    try:
        payload = _repository().activity_shadow(
            _validated_alias(athlete_alias), activity_ref
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="Activity shadow result is unavailable")
    return payload


@app.get("/api/v2/real/activity-shadow")
def real_activity_shadow(
    activity_ref: str,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    if not ACTIVITY_SHADOW_REF_PATTERN.fullmatch(activity_ref):
        raise HTTPException(status_code=422, detail="Invalid activity shadow reference")
    try:
        payload = _repository().activity_shadow(
            _validated_alias(athlete_alias), activity_ref
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="Activity shadow result is unavailable")
    return payload


@app.post(
    "/api/v2/real/sync-jobs",
    response_model=SyncEnqueueResponse,
    status_code=202,
)
def enqueue_real_sync_job(
    body: SyncJobRequest,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        repository = _repository()
        resolved_alias = _validated_alias(athlete_alias)
        return _enqueue_sync_job(
            repository=repository,
            athlete_alias=resolved_alias,
            scope=body.scope,
        )
    except (PersistentStoreFailure, ValueError) as exc:
        logger.warning(
            "sync_enqueue_failed scope=%s error_type=%s",
            body.scope,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc


@app.get(
    "/api/v2/real/sync-status",
    response_model=SyncStateResponse,
)
def real_sync_status(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        row = _repository().sync_state(_validated_alias(athlete_alias))
        return _public_sync_state(row)
    except (PersistentStoreFailure, ValueError, TypeError) as exc:
        logger.warning("sync_status_failed error_type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc


def _enqueue_legacy_scope(
    *,
    scope: str,
    authorization: str | None,
    athlete_alias: str | None,
) -> SyncEnqueueResponse:
    _authorize(authorization)
    try:
        repository = _repository()
        resolved_alias = _validated_alias(athlete_alias)
        return _enqueue_sync_job(
            repository=repository,
            athlete_alias=resolved_alias,
            scope=scope,
        )
    except (PersistentStoreFailure, ValueError) as exc:
        logger.warning(
            "legacy_sync_enqueue_failed scope=%s error_type=%s",
            scope,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc


@app.post(
    "/api/v2/real/refresh",
    response_model=SyncEnqueueResponse,
    status_code=202,
)
def refresh_real_data(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    """Compatibility route: enqueue a full sync; never run it in the API."""

    return _enqueue_legacy_scope(
        scope="FULL",
        authorization=authorization,
        athlete_alias=athlete_alias,
    )


@app.post(
    "/api/v2/real/wellness/refresh",
    response_model=SyncEnqueueResponse,
    status_code=202,
)
def refresh_real_wellness(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    """Compatibility route: enqueue wellness sync; never run it in the API."""

    return _enqueue_legacy_scope(
        scope="WELLNESS",
        authorization=authorization,
        athlete_alias=athlete_alias,
    )


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
        hrmax_bpm=settings.hrmax_bpm,
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
            body.hr_zone_bounds_bpm, body.timezone.strip(), body.hrmax_bpm
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
        hrmax_bpm=settings.hrmax_bpm,
    )


@app.get(
    "/api/v2/planning/methodology",
    response_model=PlanningMethodologyMetadata,
)
def planning_methodology(
    authorization: Annotated[str | None, Header()] = None,
):
    _authorize(authorization)
    return PlanningMethodologyMetadata.model_validate(canonical_methodology())


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


@app.get(
    "/api/v2/athlete/mesocycle-accent-preferences",
    response_model=MesocycleAccentPreferencesResponse,
)
def athlete_mesocycle_accent_preferences(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    try:
        preferences = _repository().athlete_mesocycle_accent_preferences(
            _validated_alias(athlete_alias)
        )
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    return _mesocycle_accent_response(preferences)


@app.put(
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
    _authorize(authorization)
    resolved_alias = _validated_alias(athlete_alias)
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
        repository = _repository()
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
        training_snapshot=repository.latest(athlete_alias),
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


@app.get(
    "/api/v2/athlete/planning-calendar",
    response_model=PlanningCalendarResponse,
)
def athlete_planning_calendar(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    _authorize(authorization)
    resolved_alias = _validated_alias(athlete_alias)
    try:
        repository = _repository()
        calendar = repository.athlete_planning_calendar(resolved_alias)
        return _planning_calendar_response(repository, resolved_alias, calendar)
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc


@app.put(
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
    _authorize(authorization)
    resolved_alias = _validated_alias(athlete_alias)
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
        repository = _repository()
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
