"""Process-shared, one-time storage for pending OAuth states."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import hmac
import math
import threading
import time


PENDING_STATE_TTL_SECONDS = 10 * 60
MAX_PENDING_STATES = 256
MAX_STATE_LENGTH = 4096
MAX_FUTURE_CLOCK_SKEW_SECONDS = 30
MAX_POLICY_VERSION_LENGTH = 64


def _required_state(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_STATE_LENGTH
    ):
        raise ValueError("state must be bounded non-empty text")
    return value


def _state_digest(state: str) -> str:
    return hashlib.sha256(_required_state(state).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PendingConsentEvidence:
    """Bounded proof containing the policy version, time and three booleans."""

    policy_version: str
    confirmed_at: float
    privacy_and_general_consent: bool
    wellness_health_explicit_consent: bool
    adult_confirmed: bool

    @property
    def is_complete(self) -> bool:
        return (
            isinstance(self.policy_version, str)
            and 0 < len(self.policy_version) <= MAX_POLICY_VERSION_LENGTH
            and math.isfinite(self.confirmed_at)
            and self.privacy_and_general_consent is True
            and self.wellness_health_explicit_consent is True
            and self.adult_confirmed is True
        )


@dataclass(frozen=True)
class _PendingStateRecord:
    issued_at: float
    consent: PendingConsentEvidence | None


class PendingStateStore:
    """Thread-safe bounded store of digests and short-lived consent evidence."""

    def __init__(
        self,
        *,
        ttl_seconds: int = PENDING_STATE_TTL_SECONDS,
        max_entries: int = MAX_PENDING_STATES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._issued: dict[str, _PendingStateRecord] = {}

    def register(
        self,
        state: str,
        *,
        issued_at: float | None = None,
        consent: PendingConsentEvidence | None = None,
    ) -> None:
        digest = _state_digest(state)
        now = float(self._clock())
        created = now if issued_at is None else float(issued_at)
        if not math.isfinite(created):
            raise ValueError("issued_at must be finite")
        if consent is not None and not consent.is_complete:
            raise ValueError("consent evidence must be complete")
        with self._lock:
            self._prune_locked(now)
            if digest not in self._issued and len(self._issued) >= self._max_entries:
                oldest = min(
                    self._issued,
                    key=lambda candidate: self._issued[candidate].issued_at,
                )
                self._issued.pop(oldest, None)
            self._issued[digest] = _PendingStateRecord(
                issued_at=created,
                consent=consent,
            )

    def consume(self, state: str) -> bool:
        """Atomically remove one matching, unexpired state."""

        return self._consume_record(state) is not None

    def consume_with_consent(
        self,
        state: str,
    ) -> PendingConsentEvidence | None:
        """Atomically consume state and return its complete consent evidence."""

        record = self._consume_record(state)
        if record is None:
            return None
        return record.consent

    def _consume_record(self, state: str) -> _PendingStateRecord | None:
        digest = _state_digest(state)
        now = float(self._clock())
        with self._lock:
            self._prune_locked(now)
            matching_digest = next(
                (
                    candidate
                    for candidate in self._issued
                    if hmac.compare_digest(candidate, digest)
                ),
                None,
            )
            if matching_digest is None:
                return None
            return self._issued.pop(matching_digest)

    def is_pending(self, state: str) -> bool:
        digest = _state_digest(state)
        now = float(self._clock())
        with self._lock:
            self._prune_locked(now)
            return any(
                hmac.compare_digest(candidate, digest)
                for candidate in self._issued
            )

    def clear(self) -> None:
        with self._lock:
            self._issued.clear()

    def _prune_locked(self, now: float) -> None:
        expired = [
            digest
            for digest, record in self._issued.items()
            if (
                now - record.issued_at > self._ttl_seconds
                or record.issued_at - now > MAX_FUTURE_CLOCK_SKEW_SECONDS
            )
        ]
        for digest in expired:
            self._issued.pop(digest, None)


# Imported modules remain shared while Streamlit executes the app script in
# fresh session modules. It holds state digests, timestamps and bounded consent
# evidence—never codes, tokens, athlete data, client secrets or raw state
# values.
_PROCESS_PENDING_STATES = PendingStateStore()


def register_pending_state(
    state: str,
    *,
    consent: PendingConsentEvidence | None = None,
) -> None:
    _PROCESS_PENDING_STATES.register(state, consent=consent)


def consume_pending_state(state: str) -> bool:
    return _PROCESS_PENDING_STATES.consume(state)


def consume_pending_state_with_consent(
    state: str,
) -> PendingConsentEvidence | None:
    return _PROCESS_PENDING_STATES.consume_with_consent(state)


def is_pending_state(state: str) -> bool:
    return _PROCESS_PENDING_STATES.is_pending(state)


def clear_pending_states_for_tests() -> None:
    _PROCESS_PENDING_STATES.clear()


__all__ = [
    "MAX_PENDING_STATES",
    "PENDING_STATE_TTL_SECONDS",
    "PendingConsentEvidence",
    "PendingStateStore",
    "clear_pending_states_for_tests",
    "consume_pending_state",
    "consume_pending_state_with_consent",
    "is_pending_state",
    "register_pending_state",
]
