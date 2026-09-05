"""Internal contracts shared by the durable sync worker.

The public HTTP schemas live in :mod:`apps.api.schemas`.  These dataclasses are
deliberately small and dependency-free so importing the worker never reads
credentials or creates network clients.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Mapping


JOB_KINDS = frozenset({"FULL_SYNC", "WELLNESS_SYNC", "RECOVERY_RESTORE"})
PUBLIC_SCOPE_BY_JOB_KIND = {
    "FULL_SYNC": "FULL",
    "WELLNESS_SYNC": "WELLNESS",
    "RECOVERY_RESTORE": "RECOVERY",
}


class SyncContractError(ValueError):
    """A malformed internal queue/store contract."""


def _required_text(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise SyncContractError(f"Sync claim field {name!r} is invalid")
    return value.strip()


def _required_positive_int(payload: Mapping[str, Any], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SyncContractError(f"Sync claim field {name!r} is invalid")
    return value


@dataclass(frozen=True)
class ClaimedSyncJob:
    """One fenced lease returned by ``claim_sync_job``."""

    job_id: str
    athlete_alias: str
    job_kind: str
    request_payload: Mapping[str, Any]
    request_sequence: int
    generation_id: str
    attempt_no: int
    base_generation_id: str | None
    base_revision: int
    base_activity_set_hash: str | None
    lease_token: str
    lease_expires_at: datetime

    @property
    def public_scope(self) -> str:
        return PUBLIC_SCOPE_BY_JOB_KIND[self.job_kind]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ClaimedSyncJob":
        if not isinstance(payload, Mapping):
            raise SyncContractError("Sync claim must be a mapping")
        job_kind = _required_text(payload, "job_kind")
        if job_kind not in JOB_KINDS:
            raise SyncContractError("Sync claim job kind is unsupported")
        request_payload = payload.get("request_payload")
        if request_payload is None:
            request_payload = {}
        if not isinstance(request_payload, Mapping):
            raise SyncContractError("Sync claim request payload is invalid")
        base_generation_id = payload.get("base_generation_id")
        if base_generation_id is not None and (
            not isinstance(base_generation_id, str) or not base_generation_id.strip()
        ):
            raise SyncContractError("Sync claim base generation is invalid")
        base_revision = payload.get("base_revision")
        if (
            isinstance(base_revision, bool)
            or not isinstance(base_revision, int)
            or base_revision < 0
        ):
            raise SyncContractError("Sync claim base revision is invalid")
        base_activity_set_hash = payload.get("base_activity_set_hash")
        if base_activity_set_hash is not None and (
            not isinstance(base_activity_set_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", base_activity_set_hash)
        ):
            raise SyncContractError("Sync claim base activity hash is invalid")
        lease_raw = _required_text(payload, "lease_expires_at")
        try:
            lease_expires_at = datetime.fromisoformat(lease_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SyncContractError("Sync claim lease expiry is invalid") from exc
        if lease_expires_at.tzinfo is None:
            raise SyncContractError("Sync claim lease expiry must be timezone-aware")
        return cls(
            job_id=_required_text(payload, "job_id"),
            athlete_alias=_required_text(payload, "athlete_alias"),
            job_kind=job_kind,
            request_payload=dict(request_payload),
            request_sequence=_required_positive_int(payload, "request_sequence"),
            generation_id=_required_text(payload, "generation_id"),
            attempt_no=_required_positive_int(payload, "attempt_no"),
            base_generation_id=(
                base_generation_id.strip()
                if isinstance(base_generation_id, str)
                else None
            ),
            base_revision=base_revision,
            base_activity_set_hash=base_activity_set_hash,
            lease_token=_required_text(payload, "lease_token"),
            lease_expires_at=lease_expires_at,
        )


@dataclass(frozen=True)
class JobProcessResult:
    """Privacy-safe result used by the worker loop and unit tests."""

    job_id: str
    outcome: str
    generation_id: str | None = None
    active_revision: int | None = None
    failure_code: str | None = None


__all__ = [
    "ClaimedSyncJob",
    "JOB_KINDS",
    "JobProcessResult",
    "PUBLIC_SCOPE_BY_JOB_KIND",
    "SyncContractError",
]
