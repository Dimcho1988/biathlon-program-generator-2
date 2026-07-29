"""Small read-only client for the Intervals.icu API.

The client intentionally exposes GET operations only.  It keeps the bearer
token in memory, never logs response bodies, and turns HTTP failures into
short messages that are safe to display in the Streamlit inspector.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import re
import time
from typing import Any
from urllib.parse import quote

import requests


API_BASE_URL = "https://intervals.icu"
DEFAULT_TIMEOUT = (5.0, 30.0)
DEFAULT_MAX_RETRIES = 2
MAX_RETRY_DELAY_SECONDS = 5.0
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class IntervalsAPIError(RuntimeError):
    """A sanitized Intervals.icu error suitable for displaying to a user."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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
        sleeper: Callable[[float], None] = time.sleep,
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

        self._access_token = access_token.strip()
        self._session = session or requests.Session()
        self._timeout = (float(timeout[0]), float(timeout[1]))
        self._max_retries = max_retries
        self._sleeper = sleeper

    def get_athlete(self) -> Any:
        return self._get(f"/api/v1/athlete/{_path_segment(self._athlete_id)}")

    def get_sport_settings(self) -> Any:
        return self._get(
            f"/api/v1/athlete/{_path_segment(self._athlete_id)}/sport-settings"
        )

    def get_activities(self, oldest: str, newest: str) -> Any:
        return self._dated_get(
            f"/api/v1/athlete/{_path_segment(self._athlete_id)}/activities",
            oldest,
            newest,
        )

    def get_wellness(self, oldest: str, newest: str) -> Any:
        return self._dated_get(
            f"/api/v1/athlete/{_path_segment(self._athlete_id)}/wellness",
            oldest,
            newest,
        )

    def get_events(self, oldest: str, newest: str) -> Any:
        return self._dated_get(
            f"/api/v1/athlete/{_path_segment(self._athlete_id)}/events",
            oldest,
            newest,
        )

    def get_streams(self, activity_id: str) -> Any:
        safe_activity_id = _validated_identifier(
            activity_id, field_name="activity_id"
        )
        return self._get(
            f"/api/v1/activity/{_path_segment(safe_activity_id)}/streams.json"
        )

    def _dated_get(self, path: str, oldest: str, newest: str) -> Any:
        oldest_value = _validated_date(oldest, field_name="oldest")
        newest_value = _validated_date(newest, field_name="newest")
        if oldest_value > newest_value:
            raise ValueError("oldest must not be after newest")
        return self._get(path, params={"oldest": oldest, "newest": newest})

    def _get(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        url = f"{API_BASE_URL}{path}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token}",
        }

        for attempt in range(self._max_retries + 1):
            try:
                response = self._session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                raise IntervalsAPIError(
                    "Няма връзка с Intervals.icu. Опитайте отново."
                ) from exc

            status = response.status_code
            retryable = status == 429 or 500 <= status <= 599
            if retryable and attempt < self._max_retries:
                self._sleeper(_retry_delay(response, attempt))
                continue
            if status >= 400:
                raise _error_for_status(status)

            try:
                return response.json()
            except (requests.JSONDecodeError, ValueError) as exc:
                raise IntervalsAPIError(
                    "Intervals.icu върна невалиден JSON отговор."
                ) from exc

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


def _retry_delay(response: requests.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        parsed_delay = _parse_retry_after(retry_after)
        if parsed_delay is not None:
            return min(max(parsed_delay, 0.0), MAX_RETRY_DELAY_SECONDS)
    return min(0.5 * (2**attempt), MAX_RETRY_DELAY_SECONDS)


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


def _error_for_status(status: int) -> IntervalsAPIError:
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
    return IntervalsAPIError(message, status_code=status)
