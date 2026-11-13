"""Sync request assembly and public state serialization."""

from datetime import datetime, timezone
import os
import secrets
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .oauth_store import PersistentStoreFailure, SupabasePilotRepository
from .real_service import recovery_source_supports_restore
from .schemas import SyncEnqueueResponse, SyncStateResponse
from .sync_contracts import PUBLIC_SCOPE_BY_JOB_KIND

SYNC_SCOPE_BY_JOB_KIND = dict(PUBLIC_SCOPE_BY_JOB_KIND)
SYNC_JOB_KIND_BY_SCOPE = {
    scope: job_kind for job_kind, scope in SYNC_SCOPE_BY_JOB_KIND.items()
}


def optional_iso(value: object) -> str | None:
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


def enqueue_sync_job(
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


def public_sync_state(row: Mapping[str, object]) -> SyncStateResponse:
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
        requested_at=optional_iso(row.get("requested_at")),
        started_at=optional_iso(row.get("started_at")),
        finished_at=optional_iso(row.get("completed_at")),
        retry_at=(
            optional_iso(row.get("available_at"))
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
        analysis_as_of=optional_iso(row.get("active_as_of")),
        activated_at=optional_iso(row.get("activated_at")),
    )
