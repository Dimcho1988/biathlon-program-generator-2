"""Process-shared, one-time storage for pending OAuth states."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import hmac
import threading
import time


PENDING_STATE_TTL_SECONDS = 10 * 60
MAX_PENDING_STATES = 256
MAX_STATE_LENGTH = 4096
MAX_FUTURE_CLOCK_SKEW_SECONDS = 30


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


class PendingStateStore:
    """Thread-safe bounded store containing state digests and issue times only."""

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
        self._issued: dict[str, float] = {}

    def register(self, state: str, *, issued_at: float | None = None) -> None:
        digest = _state_digest(state)
        now = float(self._clock())
        created = now if issued_at is None else float(issued_at)
        with self._lock:
            self._prune_locked(now)
            if digest not in self._issued and len(self._issued) >= self._max_entries:
                oldest = min(self._issued, key=self._issued.get)
                self._issued.pop(oldest, None)
            self._issued[digest] = created

    def consume(self, state: str) -> bool:
        """Atomically remove one matching, unexpired state."""

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
                return False
            self._issued.pop(matching_digest, None)
            return True

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
            for digest, issued_at in self._issued.items()
            if (
                now - issued_at > self._ttl_seconds
                or issued_at - now > MAX_FUTURE_CLOCK_SKEW_SECONDS
            )
        ]
        for digest in expired:
            self._issued.pop(digest, None)


# Imported modules remain shared while Streamlit executes the app script in
# fresh session modules. Only state digests live here—never codes, tokens,
# athlete data, client secrets, or raw state values.
_PROCESS_PENDING_STATES = PendingStateStore()


def register_pending_state(state: str) -> None:
    _PROCESS_PENDING_STATES.register(state)


def consume_pending_state(state: str) -> bool:
    return _PROCESS_PENDING_STATES.consume(state)


def is_pending_state(state: str) -> bool:
    return _PROCESS_PENDING_STATES.is_pending(state)


def clear_pending_states_for_tests() -> None:
    _PROCESS_PENDING_STATES.clear()


__all__ = [
    "MAX_PENDING_STATES",
    "PENDING_STATE_TTL_SECONDS",
    "PendingStateStore",
    "clear_pending_states_for_tests",
    "consume_pending_state",
    "is_pending_state",
    "register_pending_state",
]
