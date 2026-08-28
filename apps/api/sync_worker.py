"""Durable background worker for profile-scoped onFlows analysis generations.

The worker intentionally has no broker-specific dependency.  Queue ownership,
leases, fencing and atomic activation are provided by PostgreSQL RPCs exposed by
the repository.  Importing this module does not read credentials, construct a
repository, or open a network connection.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import math
import os
import re
import signal
import socket
from threading import Event, Thread
import time
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .sync_contracts import (
    ClaimedSyncJob,
    JobProcessResult,
    SyncContractError,
)


logger = logging.getLogger(__name__)
_SAFE_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")
_ACTIVATION_OUTCOMES = frozenset({"ACTIVATED", "STALE", "LEASE_LOST"})
_STAGE_OUTCOMES = frozenset({"READY", "ALREADY_READY"})
_MAINTENANCE_INTERVAL_SECONDS = 6 * 60 * 60


class SyncRepository(Protocol):
    """Repository surface used by the worker.

    The concrete implementation is the server-only Supabase repository.  The
    protocol stays here to keep the worker independently testable.
    """

    def claim_sync_job(
        self, *, worker_id: str, lease_seconds: int = 300
    ) -> Mapping[str, Any] | None: ...

    def renew_sync_lease(
        self,
        *,
        job_id: str,
        generation_id: str,
        lease_token: str,
        lease_seconds: int = 300,
    ) -> bool: ...

    def stage_analysis_generation(
        self,
        *,
        job_id: str,
        generation_id: str,
        lease_token: str,
        snapshot_payload: Mapping[str, Any],
        snapshot_hash: str,
        period_start: date,
        period_end: date,
        as_of: date,
        provenance: Mapping[str, Any],
        activities: list[Mapping[str, Any]],
        inherit_activities: bool = False,
    ) -> Mapping[str, Any]: ...

    def activate_analysis_generation(
        self, *, job_id: str, generation_id: str, lease_token: str
    ) -> Mapping[str, Any]: ...

    def fail_sync_job(
        self,
        *,
        job_id: str,
        generation_id: str,
        lease_token: str,
        error_code: str,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class SyncPipelines:
    """Injectable scientific operations; defaults are loaded only at runtime."""

    full: Callable[..., Any]
    wellness: Callable[..., Any]
    recovery: Callable[..., Any]


@dataclass(frozen=True)
class _Failure:
    code: str
    retryable: bool
    retry_after_seconds: int | None = None


class _WorkerFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class _GenerationCaptureRepository:
    """Turn legacy pipeline writes into one staged generation payload.

    Shadow inputs/results and canonical runs are immutable and may be inserted
    while a generation is being built.  Mutable catalog and snapshot writes are
    captured locally and remain invisible until the activation RPC commits.
    """

    def __init__(
        self,
        repository: Any,
        *,
        athlete_alias: str,
        pinned_snapshot: Mapping[str, Any] | None = None,
    ) -> None:
        self._repository = repository
        self._athlete_alias = athlete_alias
        self._pinned_snapshot = (
            deepcopy(dict(pinned_snapshot))
            if isinstance(pinned_snapshot, Mapping)
            else None
        )
        self._snapshot: dict[str, Any] | None = None
        self._activities: dict[str, dict[str, Any]] = {}
        self._catalog_captured = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    @property
    def snapshot(self) -> Mapping[str, Any]:
        if self._snapshot is None:
            raise SyncContractError("Scientific pipeline did not produce a snapshot")
        return deepcopy(self._snapshot)

    @property
    def activities(self) -> list[Mapping[str, Any]]:
        return [
            deepcopy(self._activities[key]) for key in sorted(self._activities)
        ]

    @property
    def catalog_captured(self) -> bool:
        return self._catalog_captured

    def latest(self, athlete_alias: str) -> Mapping[str, Any] | None:
        self._require_alias(athlete_alias)
        if self._snapshot is not None:
            return deepcopy(self._snapshot)
        if self._pinned_snapshot is not None:
            return deepcopy(self._pinned_snapshot)
        value = self._repository.latest(athlete_alias)
        return deepcopy(dict(value)) if isinstance(value, Mapping) else None

    def replace(self, athlete_alias: str, snapshot: Mapping[str, Any]) -> None:
        self._require_alias(athlete_alias)
        if not isinstance(snapshot, Mapping):
            raise SyncContractError("Scientific snapshot is invalid")
        self._snapshot = deepcopy(dict(snapshot))

    def upsert_activity_catalog(
        self, athlete_alias: str, activities: list[Mapping[str, Any]]
    ) -> None:
        self._require_alias(athlete_alias)
        if not isinstance(activities, list):
            raise SyncContractError("Scientific activity catalog is invalid")
        self._catalog_captured = True
        for activity in activities:
            if not isinstance(activity, Mapping):
                raise SyncContractError("Scientific activity catalog is invalid")
            activity_ref = activity.get("activity_ref")
            if not isinstance(activity_ref, str) or not activity_ref:
                raise SyncContractError("Scientific activity identity is invalid")
            existing = self._activities.get(activity_ref, {})
            self._activities[activity_ref] = {
                **deepcopy(existing),
                **deepcopy(dict(activity)),
            }

    def publish_canonical_activity_result(
        self,
        *,
        athlete_alias: str,
        activity_ref: str,
        scientific_input_hash: str,
        result_payload: Mapping[str, Any],
    ) -> str:
        self._require_alias(athlete_alias)
        return self._repository.store_canonical_activity_result(
            athlete_alias=athlete_alias,
            activity_ref=activity_ref,
            scientific_input_hash=scientific_input_hash,
            result_payload=result_payload,
        )

    def _require_alias(self, athlete_alias: str) -> None:
        if athlete_alias != self._athlete_alias:
            raise SyncContractError("Scientific pipeline crossed an athlete boundary")


class _LeaseHeartbeat:
    """Renew one fencing lease while synchronous provider/model code runs."""

    def __init__(
        self,
        repository: SyncRepository,
        job: ClaimedSyncJob,
        *,
        lease_seconds: int,
        interval_seconds: float,
    ) -> None:
        self._repository = repository
        self._job = job
        self._lease_seconds = lease_seconds
        self._interval_seconds = interval_seconds
        self._stop = Event()
        self._lost = Event()
        self._thread = Thread(
            target=self._run,
            name=f"sync-lease-{job.job_id[:8]}",
            daemon=True,
        )

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self._interval_seconds + 1.0))

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                renewed = self._repository.renew_sync_lease(
                    job_id=self._job.job_id,
                    generation_id=self._job.generation_id,
                    lease_token=self._job.lease_token,
                    lease_seconds=self._lease_seconds,
                )
            except Exception as exc:  # the stage/activation RPC remains the fence
                logger.warning(
                    "sync_worker_heartbeat_failed job_id=%s error_type=%s",
                    self._job.job_id,
                    type(exc).__name__,
                )
                continue
            if not renewed:
                self._lost.set()
                return


def _load_default_pipelines() -> SyncPipelines:
    from .real_service import (
        refresh,
        refresh_wellness_calendar,
        restore_recovery_history_from_snapshot,
    )

    return SyncPipelines(
        full=refresh,
        wellness=refresh_wellness_calendar,
        recovery=restore_recovery_history_from_snapshot,
    )


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _operation_date(
    job: ClaimedSyncJob,
    athlete_settings: Any,
    *,
    now: datetime,
) -> date:
    for name in ("as_of", "period_end"):
        raw = job.request_payload.get(name)
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise _WorkerFailure("INVALID_REQUEST", retryable=False)
        try:
            parsed = date.fromisoformat(raw)
        except ValueError as exc:
            raise _WorkerFailure("INVALID_REQUEST", retryable=False) from exc
        if parsed.isoformat() != raw:
            raise _WorkerFailure("INVALID_REQUEST", retryable=False)
        return parsed
    timezone_name = _field(athlete_settings, "timezone") or "UTC"
    try:
        athlete_timezone = ZoneInfo(str(timezone_name))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise _WorkerFailure("INVALID_ATHLETE_SETTINGS", retryable=False) from exc
    return now.astimezone(athlete_timezone).date()


def _canonical_hash(value: Any) -> str:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SyncContractError("Generation payload is not canonical JSON") from exc
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _generation_window(
    snapshot: Mapping[str, Any],
) -> tuple[date, date, date]:
    load_history = snapshot.get("load_history")
    training_status = snapshot.get("training_status")
    if not isinstance(load_history, Mapping) or not isinstance(
        training_status, Mapping
    ):
        raise SyncContractError("Generation snapshot has no canonical analysis")
    try:
        period_start = date.fromisoformat(str(load_history["period_start"]))
        period_end = date.fromisoformat(str(load_history["period_end"]))
        as_of = date.fromisoformat(str(training_status.get("as_of") or period_end))
    except (KeyError, TypeError, ValueError) as exc:
        raise SyncContractError("Generation analysis period is invalid") from exc
    if period_end < period_start:
        raise SyncContractError("Generation analysis period is invalid")
    return period_start, period_end, as_of


def _safe_commit() -> str | None:
    value = os.environ.get("RENDER_GIT_COMMIT", "").strip().lower()
    return value if _SAFE_COMMIT.fullmatch(value) else None


def _provenance(
    job: ClaimedSyncJob,
    snapshot: Mapping[str, Any],
    activities: list[Mapping[str, Any]],
) -> Mapping[str, Any]:
    training_status = snapshot.get("training_status")
    recovery_history = snapshot.get("recovery_history")
    load_history = snapshot.get("load_history")
    tref_provenance = _tref_provenance(load_history)
    activity_identity = [
        {
            "activity_ref": activity.get("activity_ref"),
            "payload_hash": activity.get("payload_hash")
            or _canonical_hash(activity),
            "input_key": activity.get("input_key"),
            "canonical_run_key": activity.get("canonical_run_key")
            or activity.get("latest_canonical_run_key"),
            "shadow_run_key": activity.get("shadow_run_key")
            or activity.get("latest_shadow_run_key"),
        }
        for activity in sorted(
            activities, key=lambda row: str(row.get("activity_ref") or "")
        )
    ]
    return {
        "schema_version": "analysis-generation-provenance-v1",
        "operation": job.public_scope,
        "request_sequence": job.request_sequence,
        "attempt_no": job.attempt_no,
        "base_generation_id": job.base_generation_id,
        "base_revision": job.base_revision,
        "snapshot_schema_version": snapshot.get("schema_version"),
        "load_history_schema_version": (
            load_history.get("schema_version")
            if isinstance(load_history, Mapping)
            else None
        ),
        "tref_bounds_profile_version": tref_provenance["bounds_profile_version"],
        "tref_current_min": {
            "zones": tref_provenance["zones"],
            "strength": tref_provenance["strength"],
        },
        "training_model": deepcopy(training_status.get("model"))
        if isinstance(training_status, Mapping)
        else None,
        "recovery_model": deepcopy(recovery_history.get("model"))
        if isinstance(recovery_history, Mapping)
        else None,
        "activity_set_hash": (
            job.base_activity_set_hash
            if job.job_kind != "FULL_SYNC"
            else _canonical_hash(activity_identity)
        ),
        "worker_git_commit": _safe_commit(),
    }


def _tref_provenance(load_history: Any) -> Mapping[str, Any]:
    """Pin the bounded, real 40-day Tref values used by this snapshot."""

    if not isinstance(load_history, Mapping):
        return {"bounds_profile_version": None, "zones": [], "strength": None}
    profile = load_history.get("tref_bounds_profile_version")
    zones = load_history.get("zones")
    pinned_zones = []
    if isinstance(zones, list):
        for row in zones:
            if not isinstance(row, Mapping):
                continue
            zone = row.get("zone")
            tref = row.get("tref_min")
            if (
                isinstance(zone, str)
                and isinstance(tref, (int, float))
                and not isinstance(tref, bool)
                and math.isfinite(float(tref))
                and float(tref) > 0.0
            ):
                pinned_zones.append({"zone": zone, "tref_min": float(tref)})
    strength = load_history.get("strength")
    strength_summary = (
        strength.get("summary") if isinstance(strength, Mapping) else None
    )
    strength_tref = (
        strength_summary.get("tref_min")
        if isinstance(strength_summary, Mapping)
        else None
    )
    pinned_strength = (
        float(strength_tref)
        if isinstance(strength_tref, (int, float))
        and not isinstance(strength_tref, bool)
        and math.isfinite(float(strength_tref))
        and float(strength_tref) > 0.0
        else None
    )
    return {
        "bounds_profile_version": profile if isinstance(profile, str) else None,
        "zones": pinned_zones,
        "strength": pinned_strength,
    }


def _execute_pipeline(
    repository: Any,
    job: ClaimedSyncJob,
    *,
    pipelines: SyncPipelines,
    now: datetime,
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]], bool]:
    connection = repository.connection(job.athlete_alias)
    if connection is None or _field(connection, "status") != "CONNECTED":
        raise _WorkerFailure("PROFILE_NOT_CONNECTED", retryable=False)
    provider_athlete_id = _field(connection, "provider_athlete_id")
    access_token = _field(connection, "access_token")
    if not isinstance(provider_athlete_id, str) or not provider_athlete_id:
        raise _WorkerFailure("PROFILE_CONNECTION_INVALID", retryable=False)
    athlete_settings = repository.athlete_settings(job.athlete_alias)
    period_end = _operation_date(job, athlete_settings, now=now)
    pinned_snapshot: Mapping[str, Any] | None = None
    if job.job_kind != "FULL_SYNC":
        if job.base_generation_id is None:
            raise _WorkerFailure("ACTIVE_GENERATION_REQUIRED", retryable=False)
        envelope = repository.latest_envelope(job.athlete_alias)
        if not isinstance(envelope, Mapping):
            raise _WorkerFailure("BASE_GENERATION_CHANGED", retryable=True)
        revision = envelope.get("revision")
        if (
            envelope.get("generation_id") != job.base_generation_id
            or isinstance(revision, bool)
            or revision != job.base_revision
            or not isinstance(envelope.get("payload"), Mapping)
        ):
            raise _WorkerFailure("BASE_GENERATION_CHANGED", retryable=True)
        pinned_snapshot = envelope["payload"]
    captured = _GenerationCaptureRepository(
        repository,
        athlete_alias=job.athlete_alias,
        pinned_snapshot=pinned_snapshot,
    )

    if job.job_kind == "FULL_SYNC":
        if not isinstance(access_token, str) or not access_token:
            raise _WorkerFailure("PROFILE_CONNECTION_INVALID", retryable=False)
        pipelines.full(
            captured,
            access_token=access_token,
            provider_athlete_id=provider_athlete_id,
            athlete_alias=job.athlete_alias,
            athlete_settings=athlete_settings,
            period_end=period_end,
            now=now,
        )
        if not captured.catalog_captured:
            raise SyncContractError("Full sync did not produce an activity catalog")
        return captured.snapshot, captured.activities, False

    if job.job_kind == "WELLNESS_SYNC":
        if not isinstance(access_token, str) or not access_token:
            raise _WorkerFailure("PROFILE_CONNECTION_INVALID", retryable=False)
        pipelines.wellness(
            captured,
            access_token=access_token,
            provider_athlete_id=provider_athlete_id,
            athlete_alias=job.athlete_alias,
            period_end=period_end,
            now=now,
        )
    elif job.job_kind == "RECOVERY_RESTORE":
        pipelines.recovery(
            captured,
            athlete_alias=job.athlete_alias,
            provider_athlete_id=provider_athlete_id,
            athlete_settings=athlete_settings,
        )
    else:  # ClaimedSyncJob already validates this; keep fail-closed.
        raise SyncContractError("Sync job kind is unsupported")
    return captured.snapshot, [], True


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _classify_failure(error: BaseException) -> _Failure:
    if isinstance(error, _WorkerFailure):
        return _Failure(error.code, error.retryable)
    if isinstance(error, SyncContractError):
        return _Failure("GENERATION_CONTRACT_INVALID", False)

    chain = _exception_chain(error)
    status = next(
        (
            getattr(item, "status_code")
            for item in chain
            if isinstance(getattr(item, "status_code", None), int)
        ),
        None,
    )
    retry_after_seconds = _retry_after_seconds(chain)
    explicitly_terminal = any(
        getattr(item, "terminal", None) is True for item in chain
    )
    explicitly_retryable = any(
        getattr(item, "retryable", None) is True for item in chain
    )
    names = {type(item).__name__ for item in chain}
    if status in (401, 403):
        return _Failure("PROVIDER_AUTHORIZATION_FAILED", False)
    if status == 429:
        return _Failure(
            "PROVIDER_RATE_LIMITED",
            True,
            retry_after_seconds=retry_after_seconds,
        )
    if isinstance(status, int) and 500 <= status <= 599:
        return _Failure(
            "PROVIDER_UNAVAILABLE",
            True,
            retry_after_seconds=retry_after_seconds,
        )
    if explicitly_terminal:
        return _Failure("PROVIDER_REQUEST_REJECTED", False)
    if explicitly_retryable:
        return _Failure(
            "PROVIDER_UNAVAILABLE",
            True,
            retry_after_seconds=retry_after_seconds,
        )
    if "PersistentStoreFailure" in names:
        return _Failure("PERSISTENT_STORE_UNAVAILABLE", True)
    if "RecoverySourceRefreshRequired" in names:
        return _Failure("RECOVERY_SOURCE_REFRESH_REQUIRED", False)
    if "ConfigurationError" in names:
        return _Failure("SCIENTIFIC_CONFIGURATION_INVALID", False)
    if names.intersection({"TypeError", "ValueError", "AssertionError"}):
        return _Failure("SCIENTIFIC_RESULT_INVALID", False)
    if "ProviderFailure" in names or "IntervalsAPIError" in names:
        return _Failure("PROVIDER_UNAVAILABLE", True)
    if names.intersection({"TimeoutError", "ConnectionError"}):
        return _Failure("TRANSIENT_NETWORK_FAILURE", True)
    return _Failure("INTERNAL_WORKER_ERROR", False)


def _retry_after_seconds(chain: list[BaseException]) -> int | None:
    """Return a database-safe provider retry delay without inspecting messages."""

    for item in chain:
        value = getattr(item, "retry_after_seconds", None)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) >= 0
        ):
            # PostgreSQL accepts whole seconds in [1, 86400]. Rounding up never
            # retries before the provider's advertised boundary; zero becomes
            # the smallest representable durable delay.
            return max(1, min(86_400, math.ceil(float(value))))
    return None


def _fail_claim(
    repository: SyncRepository,
    job: ClaimedSyncJob,
    failure: _Failure,
) -> JobProcessResult:
    try:
        result = repository.fail_sync_job(
            job_id=job.job_id,
            generation_id=job.generation_id,
            lease_token=job.lease_token,
            error_code=failure.code,
            retryable=failure.retryable,
            retry_after_seconds=failure.retry_after_seconds,
        )
        status = result.get("status") if isinstance(result, Mapping) else None
    except Exception as exc:
        logger.warning(
            "sync_worker_fail_record_failed job_id=%s error_type=%s",
            job.job_id,
            type(exc).__name__,
        )
        status = None
    if status in {"QUEUED", "RETRY_WAIT"}:
        outcome = "RETRY_SCHEDULED"
    elif status == "LEASE_LOST":
        outcome = "LEASE_LOST"
    elif status == "SUPERSEDED":
        outcome = "SUPERSEDED"
    else:
        outcome = "FAILED"
    return JobProcessResult(
        job_id=job.job_id,
        outcome=outcome,
        generation_id=job.generation_id,
        failure_code=failure.code,
    )


def process_claimed_job(
    repository: SyncRepository,
    claim: Mapping[str, Any],
    *,
    pipelines: SyncPipelines | None = None,
    lease_seconds: int = 300,
    heartbeat_interval_seconds: float | None = None,
    clock: Callable[[], datetime] | None = None,
) -> JobProcessResult:
    """Build, stage and atomically activate one already-claimed job."""

    try:
        job = ClaimedSyncJob.from_mapping(claim)
    except Exception as exc:
        # A malformed claim cannot be fenced safely because its identifiers are
        # untrusted.  Let the DB lease expire instead of guessing identifiers.
        logger.error(
            "sync_worker_claim_invalid error_type=%s", type(exc).__name__
        )
        return JobProcessResult(job_id="invalid", outcome="INVALID_CLAIM")

    selected_pipelines = pipelines or _load_default_pipelines()
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None:
        return _fail_claim(
            repository,
            job,
            _Failure("WORKER_CLOCK_INVALID", False),
        )
    # Fence before spending provider quota.  A claim may have waited in a
    # process queue or been delivered to a worker whose lease was already
    # reclaimed; only the exact live token may begin expensive work.
    try:
        lease_confirmed = repository.renew_sync_lease(
            job_id=job.job_id,
            generation_id=job.generation_id,
            lease_token=job.lease_token,
            lease_seconds=lease_seconds,
        )
    except Exception as exc:
        logger.warning(
            "sync_worker_initial_lease_failed job_id=%s error_type=%s",
            job.job_id,
            type(exc).__name__,
        )
        return JobProcessResult(
            job_id=job.job_id,
            outcome="LEASE_LOST",
            generation_id=job.generation_id,
        )
    if not lease_confirmed:
        return JobProcessResult(
            job_id=job.job_id,
            outcome="LEASE_LOST",
            generation_id=job.generation_id,
        )
    interval = heartbeat_interval_seconds
    if interval is None:
        interval = min(30.0, max(1.0, lease_seconds / 3.0))
    heartbeat = _LeaseHeartbeat(
        repository,
        job,
        lease_seconds=lease_seconds,
        interval_seconds=interval,
    )
    heartbeat.start()
    try:
        snapshot, activities, inherit_activities = _execute_pipeline(
            repository,
            job,
            pipelines=selected_pipelines,
            now=now.astimezone(timezone.utc),
        )
        if heartbeat.lost:
            return JobProcessResult(
                job_id=job.job_id,
                outcome="LEASE_LOST",
                generation_id=job.generation_id,
            )
        period_start, period_end, analysis_as_of = _generation_window(snapshot)
        provenance = _provenance(job, snapshot, activities)
        snapshot_hash = _canonical_hash(snapshot)
        staged = repository.stage_analysis_generation(
            job_id=job.job_id,
            generation_id=job.generation_id,
            lease_token=job.lease_token,
            snapshot_payload=snapshot,
            snapshot_hash=snapshot_hash,
            period_start=period_start,
            period_end=period_end,
            as_of=analysis_as_of,
            provenance=provenance,
            activities=activities,
            inherit_activities=inherit_activities,
        )
        stage_outcome = staged.get("outcome") if isinstance(staged, Mapping) else None
        if stage_outcome not in _STAGE_OUTCOMES:
            raise SyncContractError("Generation stage outcome is invalid")
        if heartbeat.lost:
            return JobProcessResult(
                job_id=job.job_id,
                outcome="LEASE_LOST",
                generation_id=job.generation_id,
            )
    except Exception as exc:
        heartbeat.stop()
        failure = _classify_failure(exc)
        chain = _exception_chain(exc)
        logger.warning(
            "sync_worker_pipeline_failed job_id=%s failure_code=%s "
            "error_type=%s cause_type=%s",
            job.job_id,
            failure.code,
            type(exc).__name__,
            type(chain[-1]).__name__,
        )
        return _fail_claim(repository, job, failure)
    finally:
        heartbeat.stop()

    # Stop the background renewer before finalization to avoid a harmless but
    # noisy heartbeat racing the transaction that marks the job SUCCEEDED.
    try:
        if not repository.renew_sync_lease(
            job_id=job.job_id,
            generation_id=job.generation_id,
            lease_token=job.lease_token,
            lease_seconds=lease_seconds,
        ):
            return JobProcessResult(
                job_id=job.job_id,
                outcome="LEASE_LOST",
                generation_id=job.generation_id,
            )
        activated = repository.activate_analysis_generation(
            job_id=job.job_id,
            generation_id=job.generation_id,
            lease_token=job.lease_token,
        )
        activation_outcome = (
            activated.get("outcome") if isinstance(activated, Mapping) else None
        )
        if activation_outcome not in _ACTIVATION_OUTCOMES:
            raise SyncContractError("Generation activation outcome is invalid")
        revision = activated.get("active_revision")
        return JobProcessResult(
            job_id=job.job_id,
            outcome=activation_outcome,
            generation_id=(
                str(activated.get("active_generation_id"))
                if activated.get("active_generation_id") is not None
                else job.generation_id
            ),
            active_revision=(
                revision
                if isinstance(revision, int) and not isinstance(revision, bool)
                else None
            ),
        )
    except Exception as exc:
        return _fail_claim(repository, job, _classify_failure(exc))


def run_worker(
    repository: SyncRepository,
    *,
    worker_id: str,
    once: bool = False,
    lease_seconds: int = 300,
    poll_seconds: float = 2.0,
    max_idle_seconds: float = 10.0,
    stop_event: Event | None = None,
    pipelines: SyncPipelines | None = None,
    clock: Callable[[], datetime] | None = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> int:
    """Poll and process durable jobs until shutdown or one ``--once`` pass."""

    if lease_seconds < 30 or lease_seconds > 3600:
        raise ValueError("lease_seconds must be between 30 and 3600")
    if poll_seconds <= 0 or max_idle_seconds < poll_seconds:
        raise ValueError("worker polling interval is invalid")
    stopping = stop_event or Event()
    idle_delay = poll_seconds
    maintenance_at_by_alias: dict[str, float] = {}
    while not stopping.is_set():
        try:
            claim = repository.claim_sync_job(
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
        except Exception as exc:
            logger.warning(
                "sync_worker_claim_failed error_type=%s", type(exc).__name__
            )
            if once:
                return 2
            stopping.wait(idle_delay)
            idle_delay = min(max_idle_seconds, idle_delay * 2.0)
            continue
        if claim is None:
            if once:
                return 0
            stopping.wait(idle_delay)
            idle_delay = min(max_idle_seconds, idle_delay * 2.0)
            continue
        idle_delay = poll_seconds
        result = process_claimed_job(
            repository,
            claim,
            pipelines=pipelines,
            lease_seconds=lease_seconds,
            clock=clock,
        )
        logger.info(
            "sync_worker_job_finished job_id=%s outcome=%s failure_code=%s",
            result.job_id,
            result.outcome,
            result.failure_code or "none",
        )
        if once:
            return 0
        athlete_alias = claim.get("athlete_alias")
        if isinstance(athlete_alias, str) and athlete_alias:
            maintenance_now = monotonic_clock()
            last_maintenance = maintenance_at_by_alias.get(athlete_alias)
            if (
                last_maintenance is None
                or maintenance_now - last_maintenance
                >= _MAINTENANCE_INTERVAL_SECONDS
            ):
                # First work for an alias after startup is its first safe
                # maintenance opportunity: the job no longer owns a lease.
                _run_optional_housekeeping(repository, athlete_alias)
                maintenance_at_by_alias[athlete_alias] = maintenance_now
    return 0


def _run_optional_housekeeping(
    repository: SyncRepository,
    athlete_alias: str,
) -> None:
    maintenance = getattr(repository, "prune_analysis_generations", None)
    if not callable(maintenance):
        return
    try:
        result = maintenance(
            athlete_alias=athlete_alias,
            keep_superseded=5,
            terminal_older_than_days=30,
            batch_limit=100,
        )
        required_count_names = (
            "deleted_generations",
            "deleted_activity_rows",
            "deleted_jobs",
        )
        optional_count_names = (
            "deleted_shadow_runs",
            "deleted_canonical_runs",
            "deleted_model_inputs",
            "deleted_catalog_rows",
        )
        counts = tuple(
            result.get(name) if isinstance(result, Mapping) else None
            for name in required_count_names
        )
        optional_counts = tuple(
            result.get(name, 0) if isinstance(result, Mapping) else None
            for name in optional_count_names
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in (*counts, *optional_counts)
        ):
            raise SyncContractError("Analysis maintenance result is invalid")
    except Exception as exc:
        logger.warning(
            "sync_worker_housekeeping_failed error_type=%s",
            type(exc).__name__,
        )
        return
    logger.info(
        "sync_worker_housekeeping_finished deleted_generations=%d "
        "deleted_activity_rows=%d deleted_jobs=%d deleted_shadow_runs=%d "
        "deleted_canonical_runs=%d deleted_model_inputs=%d "
        "deleted_catalog_rows=%d",
        *counts,
        *optional_counts,
    )


def _repository_from_environment() -> SyncRepository:
    # Keep credentials and network-capable client construction outside import.
    from .oauth_store import SupabasePilotRepository

    return SupabasePilotRepository.from_environment()


def _worker_id() -> str:
    configured = os.environ.get("ONFLOWS_WORKER_ID", "").strip()
    if configured and re.fullmatch(r"[A-Za-z0-9_.:-]{3,128}", configured):
        return configured
    host = re.sub(r"[^A-Za-z0-9_.-]", "-", socket.gethostname())[:64]
    return f"{host}-{os.getpid()}-{uuid4().hex[:8]}"


def _positive_float(value: str) -> float:
    rendered = float(value)
    if rendered <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return rendered


def _lease_seconds(value: str) -> int:
    rendered = int(value)
    if not 30 <= rendered <= 3600:
        raise argparse.ArgumentTypeError("lease must be between 30 and 3600 seconds")
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the onFlows durable sync worker")
    parser.add_argument("--once", action="store_true", help="process at most one job")
    parser.add_argument(
        "--poll-seconds",
        type=_positive_float,
        default=2.0,
        help="initial idle polling interval",
    )
    parser.add_argument(
        "--lease-seconds",
        type=_lease_seconds,
        default=300,
        help="database fencing lease duration",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("ONFLOWS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    stopping = Event()

    def request_shutdown(signum: int, frame: Any) -> None:
        del signum, frame
        logger.info("sync_worker_shutdown_requested")
        stopping.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    repository = _repository_from_environment()
    return run_worker(
        repository,
        worker_id=_worker_id(),
        once=args.once,
        lease_seconds=args.lease_seconds,
        poll_seconds=args.poll_seconds,
        stop_event=stopping,
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SyncPipelines",
    "main",
    "process_claimed_job",
    "run_worker",
]
