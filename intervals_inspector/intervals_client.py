"""Small read-only client for the Intervals.icu API.

The client intentionally exposes GET operations only.  It keeps the bearer
token in memory, never logs response bodies, and turns HTTP failures into
short messages that are safe to display in the Streamlit inspector.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import math
import re
from threading import Lock
import time
from typing import Any
from urllib.parse import quote

import requests


API_BASE_URL = "https://intervals.icu"
ACTIVITY_LIST_LIMIT = 20_000
DEFAULT_TIMEOUT = (5.0, 30.0)
DEFAULT_MAX_RETRIES = 2
MAX_RETRY_DELAY_SECONDS = 5.0
# Preserve a provider-requested cooldown for the durable worker without ever
# sleeping this long inside one HTTP call. Values above one day are bounded to
# protect queue availability from malformed headers.
MAX_RETRY_AFTER_SECONDS = 86_400.0
MAX_REQUESTS_PER_SECOND = 8.0
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class IntervalsAPIError(RuntimeError):
    """A sanitized Intervals.icu error suitable for displaying to a user."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        retryable: bool = False,
        terminal: bool = False,
    ) -> None:
        if retryable and terminal:
            raise ValueError("an Intervals error cannot be retryable and terminal")
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = _sanitized_retry_after(retry_after_seconds)
        self.retryable = bool(retryable)
        self.terminal = bool(terminal)
        self.classification = (
            "RETRYABLE" if self.retryable else "TERMINAL" if self.terminal else "UNKNOWN"
        )


class IntervalsRequestPacer:
    """Thread-safe leaky-bucket pacing shared by all default clients.

    A caller may inject a clock and sleeper for deterministic tests.  The
    reservation is made while holding the lock and the sleep happens after the
    lock is released, so concurrent callers cannot reserve the same slot.
    """

    def __init__(
        self,
        *,
        requests_per_second: float = MAX_REQUESTS_PER_SECOND,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            not isinstance(requests_per_second, (int, float))
            or isinstance(requests_per_second, bool)
            or not math.isfinite(float(requests_per_second))
            or not 0 < float(requests_per_second) <= MAX_REQUESTS_PER_SECOND
        ):
            raise ValueError("requests_per_second must be between 0 and 8")
        if not callable(clock) or not callable(sleeper):
            raise ValueError("clock and sleeper must be callable")
        self._interval_seconds = 1.0 / float(requests_per_second)
        self._clock = clock
        self._sleeper = sleeper
        self._lock = Lock()
        self._next_request_at = 0.0

    def wait(self, *, minimum_delay_seconds: float = 0.0) -> float:
        """Reserve one request slot and wait once for pacing plus retry delay."""

        if (
            not isinstance(minimum_delay_seconds, (int, float))
            or isinstance(minimum_delay_seconds, bool)
            or not math.isfinite(float(minimum_delay_seconds))
            or minimum_delay_seconds < 0
        ):
            raise ValueError("minimum_delay_seconds must be finite and non-negative")
        with self._lock:
            now = float(self._clock())
            request_at = max(
                now + float(minimum_delay_seconds), self._next_request_at
            )
            self._next_request_at = request_at + self._interval_seconds
        delay = max(0.0, request_at - now)
        if delay > 0:
            self._sleeper(delay)
        return delay


_PROCESS_WIDE_PACER = IntervalsRequestPacer()


@dataclass(frozen=True, repr=False)
class IntervalsResponse:
    """Successful API response without a repr that could expose its payload."""

    status_code: int
    payload: Any = field(repr=False)


class IntervalsClient:
    """Read-only Intervals.icu API client bound to one OAuth athlete."""

    def __init__(
        self,
        access_token: str,
        athlete_id: str,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
        request_pacer: IntervalsRequestPacer | None = None,
    ) -> None:
        if not isinstance(access_token, str) or not access_token.strip():
            raise ValueError("access_token must be a non-empty string")
        self._athlete_id = _validated_identifier(
            athlete_id, field_name="athlete_id", reject_zero=True
        )
        if (
            not isinstance(timeout, tuple)
            or len(timeout) != 2
            or any(not isinstance(value, (int, float)) or value <= 0 for value in timeout)
        ):
            raise ValueError("timeout must contain positive connect/read values")
        if not isinstance(max_retries, int) or not 0 <= max_retries <= 2:
            raise ValueError("max_retries must be between 0 and 2")
        if request_pacer is not None and (sleeper is not None or clock is not None):
            raise ValueError(
                "request_pacer cannot be combined with clock or sleeper"
            )
        if request_pacer is not None and not isinstance(
            request_pacer, IntervalsRequestPacer
        ):
            raise ValueError("request_pacer must be an IntervalsRequestPacer")
        if sleeper is not None and not callable(sleeper):
            raise ValueError("sleeper must be callable")
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable")

        self._access_token = access_token.strip()
        self._session = session or requests.Session()
        self._timeout = (float(timeout[0]), float(timeout[1]))
        self._max_retries = max_retries
        if request_pacer is not None:
            self._request_pacer = request_pacer
        elif sleeper is not None or clock is not None:
            # Tests and specialized callers get an isolated limiter, preventing
            # state from leaking into or out of the process-wide default.
            self._request_pacer = IntervalsRequestPacer(
                clock=clock or time.monotonic,
                sleeper=sleeper or time.sleep,
            )
        else:
            self._request_pacer = _PROCESS_WIDE_PACER

    def get_athlete(self) -> Any:
        return self.get_athlete_result().payload

    def get_athlete_result(self) -> IntervalsResponse:
        return self._get_result(
            f"/api/v1/athlete/{_path_segment(self._athlete_id)}"
        )

    def get_sport_settings(self) -> Any:
        return self.get_sport_settings_result().payload

    def get_sport_settings_result(self) -> IntervalsResponse:
        return self._get_result(
            f"/api/v1/athlete/{_path_segment(self._athlete_id)}/sport-settings"
        )

    def get_activities(self, oldest: str, newest: str) -> Any:
        return self.get_activities_result(oldest, newest).payload

    def get_activities_result(
        self, oldest: str, newest: str
    ) -> IntervalsResponse:
        return self._dated_get_result(
            f"/api/v1/athlete/{_path_segment(self._athlete_id)}/activities",
            oldest,
            newest,
            extra_params={"limit": str(ACTIVITY_LIST_LIMIT)},
        )

    def get_wellness(self, oldest: str, newest: str) -> Any:
        return self.get_wellness_result(oldest, newest).payload

    def get_wellness_result(
        self, oldest: str, newest: str
    ) -> IntervalsResponse:
        return self._dated_get_result(
            f"/api/v1/athlete/{_path_segment(self._athlete_id)}/wellness",
            oldest,
            newest,
        )

    def get_events(
        self, oldest: str, newest: str, *, category: str | None = None
    ) -> Any:
        return self.get_events_result(
            oldest, newest, category=category
        ).payload

    def get_events_result(
        self, oldest: str, newest: str, *, category: str | None = None
    ) -> IntervalsResponse:
        extra_params: dict[str, str] | None = None
        if category is not None:
            if category != "WORKOUT":
                raise ValueError("only the read-only WORKOUT category is supported")
            extra_params = {"category": category}
        return self._dated_get_result(
            f"/api/v1/athlete/{_path_segment(self._athlete_id)}/events",
            oldest,
            newest,
            extra_params=extra_params,
        )

    def get_activity(
        self, activity_id: str, *, include_intervals: bool = False
    ) -> Any:
        return self.get_activity_result(
            activity_id, include_intervals=include_intervals
        ).payload

    def get_activity_result(
        self, activity_id: str, *, include_intervals: bool = False
    ) -> IntervalsResponse:
        safe_activity_id = _validated_identifier(
            activity_id, field_name="activity_id"
        )
        return self._get_result(
            f"/api/v1/activity/{_path_segment(safe_activity_id)}",
            params={
                "intervals": "true" if include_intervals else "false",
            },
        )

    def get_streams(self, activity_id: str) -> Any:
        return self.get_streams_result(activity_id).payload

    def get_streams_result(self, activity_id: str) -> IntervalsResponse:
        safe_activity_id = _validated_identifier(
            activity_id, field_name="activity_id"
        )
        return self._get_result(
            f"/api/v1/activity/{_path_segment(safe_activity_id)}/streams.json"
        )

    def _dated_get_result(
        self,
        path: str,
        oldest: str,
        newest: str,
        *,
        extra_params: dict[str, str] | None = None,
    ) -> IntervalsResponse:
        oldest_value = _validated_date(oldest, field_name="oldest")
        newest_value = _validated_date(newest, field_name="newest")
        if oldest_value > newest_value:
            raise ValueError("oldest must not be after newest")
        params = {"oldest": oldest, "newest": newest}
        if extra_params:
            params.update(extra_params)
        return self._get_result(path, params=params)

    def _get_result(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> IntervalsResponse:
        url = f"{API_BASE_URL}{path}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token}",
        }

        minimum_delay = 0.0
        for attempt in range(self._max_retries + 1):
            self._request_pacer.wait(minimum_delay_seconds=minimum_delay)
            minimum_delay = 0.0
            try:
                response = self._session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                if attempt < self._max_retries:
                    minimum_delay = _exponential_retry_delay(attempt)
                    continue
                raise IntervalsAPIError(
                    "Няма връзка с Intervals.icu. Опитайте отново.",
                    retryable=True,
                ) from exc

            status = response.status_code
            retryable = status == 429 or 500 <= status <= 599
            retry_after_seconds = _retry_after_seconds(response)
            if (
                retryable
                and retry_after_seconds is not None
                and retry_after_seconds > MAX_RETRY_DELAY_SECONDS
            ):
                # A web/API process must not be held for a provider-requested
                # long cooldown.  Preserve the sanitized hint for the durable
                # worker scheduler instead of performing a bounded-but-early
                # in-request retry.
                raise _error_for_status(
                    status,
                    retry_after_seconds=retry_after_seconds,
                )
            if retryable and attempt < self._max_retries:
                minimum_delay = _retry_delay(
                    retry_after_seconds=retry_after_seconds,
                    attempt=attempt,
                )
                continue
            if status >= 400:
                raise _error_for_status(
                    status,
                    retry_after_seconds=retry_after_seconds,
                )
            if status == 204:
                return IntervalsResponse(status_code=status, payload=None)

            try:
                payload = response.json()
            except (requests.JSONDecodeError, ValueError) as exc:
                raise IntervalsAPIError(
                    "Intervals.icu върна невалиден JSON отговор.",
                    status_code=status,
                    terminal=True,
                ) from exc
            return IntervalsResponse(status_code=status, payload=payload)

        raise AssertionError("unreachable")


def _validated_identifier(
    value: str, *, field_name: str, reject_zero: bool = False
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    candidate = value.strip()
    if not _SAFE_IDENTIFIER.fullmatch(candidate):
        raise ValueError(f"{field_name} contains unsafe characters")
    if reject_zero and candidate == "0":
        raise ValueError("athlete_id must be the exact non-zero OAuth athlete id")
    return candidate


def _path_segment(value: str) -> str:
    return quote(value, safe="")


def _validated_date(value: str, *, field_name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date string") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} must use YYYY-MM-DD")
    return parsed


def _retry_delay(*, retry_after_seconds: float | None, attempt: int) -> float:
    if retry_after_seconds is not None:
        return min(retry_after_seconds, MAX_RETRY_DELAY_SECONDS)
    return _exponential_retry_delay(attempt)


def _exponential_retry_delay(attempt: int) -> float:
    return min(0.5 * (2**attempt), MAX_RETRY_DELAY_SECONDS)


def _retry_after_seconds(response: requests.Response) -> float | None:
    retry_after = response.headers.get("Retry-After")
    if not retry_after:
        return None
    return _sanitized_retry_after(_parse_retry_after(retry_after))


def _sanitized_retry_after(value: float | None) -> float | None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        return None
    return min(max(float(value), 0.0), MAX_RETRY_AFTER_SECONDS)


def _parse_retry_after(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return (retry_at - datetime.now(timezone.utc)).total_seconds()


def _error_for_status(
    status: int, *, retry_after_seconds: float | None = None
) -> IntervalsAPIError:
    if status == 401:
        message = "OAuth достъпът е невалиден или отнет (401). Свържете профила отново."
    elif status == 403:
        message = "Липсва необходимо разрешение за тези данни (403)."
    elif status == 429:
        message = "Intervals.icu временно ограничи заявките (429). Опитайте по-късно."
    elif 500 <= status <= 599:
        message = (
            f"Intervals.icu временно не е достъпен ({status}). "
            "Опитайте по-късно."
        )
    else:
        message = f"Intervals.icu върна неуспешен отговор ({status})."
    retryable = status == 429 or 500 <= status <= 599
    terminal = 400 <= status <= 499 and status != 429
    return IntervalsAPIError(
        message,
        status_code=status,
        retry_after_seconds=retry_after_seconds,
        retryable=retryable,
        terminal=terminal,
    )
