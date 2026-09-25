"""Dashboard HTTP routes."""

from datetime import date
from typing import Annotated, Mapping
from fastapi import Header, HTTPException
from ..oauth_store import PersistentStoreFailure
from ..real_service import (
    completed_work_from_load_history,
    load_history_from_persisted,
    recovery_history_from_persisted,
    training_status_from_persisted,
    volume_history_from_load_history,
)
from ..schemas import (
    AthleteSnapshot,
    CompletedWorkResponse,
    DashboardViewResponse,
    LoadHistoryResponse,
    RecoveryHistoryResponse,
    TrainingStatusResponse,
    VolumeHistoryResponse,
)
from .. import model_service
from ..model_schemas import RecoveryHistoryV2
from fastapi import APIRouter
from .. import dependencies
from ..sync_service import optional_iso

router = APIRouter()


@router.get("/api/v2/real/training-status", response_model=TrainingStatusResponse)
def real_training_status(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    try:
        snapshot = dependencies.repository().latest(dependencies.validated_alias(athlete_alias))
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if snapshot is None:
        raise HTTPException(
            status_code=503, detail="No valid real-data snapshot is available"
        )
    try:
        snapshot = model_service.project_recovery(dependencies.repository(),dependencies.validated_alias(athlete_alias),snapshot)
        return training_status_from_persisted(snapshot)
    except PersistentStoreFailure as exc:
        raise HTTPException(503,"Model settings are unavailable") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=503, detail="Stored real-data snapshot is invalid"
        ) from exc


@router.get("/api/v2/real/load-history", response_model=LoadHistoryResponse)
def real_load_history(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    try:
        snapshot = dependencies.repository().latest(dependencies.validated_alias(athlete_alias))
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


@router.get("/api/v2/real/completed-work", response_model=CompletedWorkResponse)
def real_completed_work(
    period_start: date | None = None,
    period_end: date | None = None,
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    try:
        snapshot = dependencies.repository().latest(dependencies.validated_alias(athlete_alias))
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


@router.get("/api/v2/real/volume-history", response_model=VolumeHistoryResponse)
def real_volume_history(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    try:
        snapshot = dependencies.repository().latest(dependencies.validated_alias(athlete_alias))
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


@router.get("/api/v2/real/recovery-history", response_model=RecoveryHistoryResponse | RecoveryHistoryV2)
def real_recovery_history(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    try:
        snapshot = dependencies.repository().latest(dependencies.validated_alias(athlete_alias))
    except PersistentStoreFailure as exc:
        raise HTTPException(
            status_code=503, detail="Persistent server storage is unavailable"
        ) from exc
    if snapshot is None:
        raise HTTPException(
            status_code=503, detail="No valid real-data snapshot is available"
        )
    try:
        snapshot = model_service.project_recovery(dependencies.repository(),dependencies.validated_alias(athlete_alias),snapshot)
        return recovery_history_from_persisted(snapshot)
    except PersistentStoreFailure as exc:
        raise HTTPException(503,"Model settings are unavailable") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Recovery history requires a new real-data refresh",
        ) from exc


@router.get(
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

    dependencies.authorize(authorization)
    try:
        repository = dependencies.repository()
        alias = dependencies.validated_alias(athlete_alias)
        envelope = repository.active_analysis(alias)
        if not isinstance(envelope, Mapping):
            raise ValueError("No active analysis is available")
        payload = envelope.get("snapshot_payload")
        if not isinstance(payload, Mapping):
            raise ValueError("Active analysis payload is invalid")
        payload = model_service.project_recovery(repository,alias,payload)
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
            analysis_as_of=optional_iso(envelope.get("analysis_as_of")),
            activated_at=optional_iso(envelope.get("activated_at")),
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
