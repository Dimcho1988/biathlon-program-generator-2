"""Sync HTTP routes."""

import logging
from typing import Annotated
from fastapi import Header, HTTPException
from ..oauth_store import PersistentStoreFailure
from ..schemas import SyncEnqueueResponse, SyncJobRequest, SyncStateResponse
from fastapi import APIRouter
from .. import dependencies
from ..sync_service import enqueue_sync_job, public_sync_state

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
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


@router.post(
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
    dependencies.authorize(authorization)
    try:
        repository = dependencies.repository()
        resolved_alias = dependencies.validated_alias(athlete_alias)
        return enqueue_sync_job(
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


@router.get(
    "/api/v2/real/sync-status",
    response_model=SyncStateResponse,
)
def real_sync_status(
    authorization: Annotated[str | None, Header()] = None,
    athlete_alias: Annotated[
        str | None, Header(alias="X-OnFlows-Athlete-Alias")
    ] = None,
):
    dependencies.authorize(authorization)
    try:
        row = dependencies.repository().sync_state(dependencies.validated_alias(athlete_alias))
        return public_sync_state(row)
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
    dependencies.authorize(authorization)
    try:
        repository = dependencies.repository()
        resolved_alias = dependencies.validated_alias(athlete_alias)
        return enqueue_sync_job(
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


@router.post(
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


@router.post(
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
