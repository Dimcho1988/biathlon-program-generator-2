from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

import pytest
import requests

from intervals_inspector.intervals_client import (
    IntervalsAPIError,
    IntervalsClient,
    IntervalsResponse,
)


TOKEN = "test-token-that-must-not-leak"
ATHLETE_ID = "123456"


def _response(
    status: int = 200,
    payload: Any = None,
    *,
    headers: dict[str, str] | None = None,
) -> Mock:
    response = Mock(spec=requests.Response)
    response.status_code = status
    response.headers = headers or {}
    response.json.return_value = {} if payload is None else payload
    return response


def _client(
    responses: list[Mock] | None = None,
    *,
    sleeper: Callable[[float], None] | None = None,
    max_retries: int = 2,
) -> tuple[IntervalsClient, Mock]:
    session = Mock(spec=requests.Session)
    if responses is not None:
        session.get.side_effect = responses
    client = IntervalsClient(
        TOKEN,
        ATHLETE_ID,
        session=session,
        sleeper=sleeper or (lambda _delay: None),
        max_retries=max_retries,
    )
    return client, session


@pytest.mark.parametrize(
    ("method_name", "expected_path"),
    [
        ("get_athlete", f"/api/v1/athlete/{ATHLETE_ID}"),
        (
            "get_sport_settings",
            f"/api/v1/athlete/{ATHLETE_ID}/sport-settings",
        ),
    ],
)
def test_profile_methods_use_exact_oauth_athlete_id(
    method_name: str, expected_path: str
) -> None:
    client, session = _client([_response(payload={"ok": True})])

    result = getattr(client, method_name)()

    assert result == {"ok": True}
    call = session.get.call_args
    assert call.args[0] == f"https://intervals.icu{expected_path}"
    assert call.kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert call.kwargs["headers"]["Accept"] == "application/json"
    assert call.kwargs["timeout"] == (5.0, 30.0)
    assert call.kwargs["params"] is None


@pytest.mark.parametrize(
    ("method_name", "expected_path"),
    [
        ("get_activities", f"/api/v1/athlete/{ATHLETE_ID}/activities"),
        ("get_wellness", f"/api/v1/athlete/{ATHLETE_ID}/wellness"),
        ("get_events", f"/api/v1/athlete/{ATHLETE_ID}/events"),
    ],
)
def test_dated_methods_send_required_date_params(
    method_name: str, expected_path: str
) -> None:
    client, session = _client([_response(payload=[])])

    result = getattr(client, method_name)("2026-06-01", "2026-06-30")

    assert result == []
    call = session.get.call_args
    assert call.args[0] == f"https://intervals.icu{expected_path}"
    expected_params = {
        "oldest": "2026-06-01",
        "newest": "2026-06-30",
    }
    if method_name == "get_activities":
        expected_params["limit"] = "20000"
    assert call.kwargs["params"] == expected_params


def test_get_streams_uses_documented_json_endpoint() -> None:
    client, session = _client([_response(payload={"watts": [1, 2]})])

    result = client.get_streams("i98765")

    assert result == {"watts": [1, 2]}
    assert (
        session.get.call_args.args[0]
        == "https://intervals.icu/api/v1/activity/i98765/streams.json"
    )


def test_result_envelope_preserves_status_without_repr_payload_leak() -> None:
    client, _session = _client(
        [_response(status=200, payload={"private": TOKEN})]
    )

    result = client.get_athlete_result()

    assert isinstance(result, IntervalsResponse)
    assert result.status_code == 200
    assert result.payload == {"private": TOKEN}
    assert TOKEN not in repr(result)


@pytest.mark.parametrize(
    ("include_intervals", "expected_value"),
    [(False, "false"), (True, "true")],
)
def test_activity_detail_is_read_only_and_explicitly_bounds_intervals(
    include_intervals: bool, expected_value: str
) -> None:
    client, session = _client([_response(payload={"id": "i98765"})])

    result = client.get_activity(
        "i98765", include_intervals=include_intervals
    )

    assert result == {"id": "i98765"}
    assert (
        session.get.call_args.args[0]
        == "https://intervals.icu/api/v1/activity/i98765"
    )
    assert session.get.call_args.kwargs["params"] == {
        "intervals": expected_value
    }


def test_planned_workouts_add_only_documented_category_filter() -> None:
    client, session = _client([_response(payload=[])])

    result = client.get_events(
        "2026-06-01",
        "2026-06-30",
        category="WORKOUT",
    )

    assert result == []
    assert session.get.call_args.kwargs["params"] == {
        "oldest": "2026-06-01",
        "newest": "2026-06-30",
        "category": "WORKOUT",
    }


@pytest.mark.parametrize(
    ("athlete_id", "message_fragment"),
    [
        ("0", "non-zero"),
        ("../123", "unsafe"),
        ("123?admin=true", "unsafe"),
        ("123/activities", "unsafe"),
        ("", "unsafe"),
    ],
)
def test_rejects_unsafe_or_zero_athlete_id(
    athlete_id: str, message_fragment: str
) -> None:
    with pytest.raises(ValueError, match=message_fragment):
        IntervalsClient(TOKEN, athlete_id)


@pytest.mark.parametrize(
    "activity_id",
    ["../i123", "i123/streams", "i123?x=1", "https://example.test", ""],
)
def test_rejects_unsafe_activity_id_before_http(activity_id: str) -> None:
    client, session = _client()

    with pytest.raises(ValueError, match="activity_id"):
        client.get_streams(activity_id)

    session.get.assert_not_called()


def test_rejects_unknown_event_category_before_http() -> None:
    client, session = _client()

    with pytest.raises(ValueError, match="WORKOUT"):
        client.get_events(
            "2026-06-01",
            "2026-06-30",
            category="RACE",
        )

    session.get.assert_not_called()


@pytest.mark.parametrize(
    ("oldest", "newest"),
    [
        ("not-a-date", "2026-06-30"),
        ("2026-06-01", "30-06-2026"),
        ("2026-07-01", "2026-06-30"),
    ],
)
def test_rejects_invalid_date_ranges_before_http(
    oldest: str, newest: str
) -> None:
    client, session = _client()

    with pytest.raises(ValueError):
        client.get_activities(oldest, newest)

    session.get.assert_not_called()


def test_429_retries_twice_and_caps_retry_after_without_real_sleep() -> None:
    delays: list[float] = []
    client, session = _client(
        [
            _response(429, headers={"Retry-After": "999"}),
            _response(429, headers={"Retry-After": "2"}),
            _response(200, payload=[{"id": "ok"}]),
        ],
        sleeper=delays.append,
    )

    result = client.get_activities("2026-06-01", "2026-06-30")

    assert result == [{"id": "ok"}]
    assert session.get.call_count == 3
    assert delays == [5.0, 2.0]


def test_5xx_retries_twice_with_bounded_exponential_backoff() -> None:
    delays: list[float] = []
    client, session = _client(
        [_response(500), _response(503), _response(502)],
        sleeper=delays.append,
    )

    with pytest.raises(IntervalsAPIError) as caught:
        client.get_athlete()

    assert caught.value.status_code == 502
    assert session.get.call_count == 3
    assert delays == [0.5, 1.0]


@pytest.mark.parametrize(
    ("status", "expected_fragment"),
    [
        (401, "401"),
        (403, "403"),
        (429, "429"),
        (500, "500"),
        (503, "503"),
    ],
)
def test_status_errors_are_sanitized(
    status: int, expected_fragment: str
) -> None:
    client, _session = _client([_response(status)], max_retries=0)

    with pytest.raises(IntervalsAPIError) as caught:
        client.get_athlete()

    message = str(caught.value)
    assert expected_fragment in message
    assert TOKEN not in message
    assert "https://intervals.icu" not in message
    assert caught.value.status_code == status


def test_other_http_error_is_sanitized() -> None:
    client, _session = _client([_response(404, payload={"secret": TOKEN})])

    with pytest.raises(IntervalsAPIError) as caught:
        client.get_streams("i404")

    assert caught.value.status_code == 404
    assert "404" in str(caught.value)
    assert TOKEN not in str(caught.value)


def test_network_error_does_not_include_request_or_token() -> None:
    client, session = _client()
    session.get.side_effect = requests.ConnectionError(
        f"failed with Authorization: Bearer {TOKEN}"
    )

    with pytest.raises(IntervalsAPIError) as caught:
        client.get_athlete()

    assert TOKEN not in str(caught.value)
    assert "Authorization" not in str(caught.value)


def test_invalid_json_does_not_include_response_payload() -> None:
    response = _response(200)
    response.json.side_effect = ValueError(f"payload contains {TOKEN}")
    client, _session = _client([response])

    with pytest.raises(IntervalsAPIError) as caught:
        client.get_wellness("2026-06-01", "2026-06-30")

    assert TOKEN not in str(caught.value)
    assert "JSON" in str(caught.value)
    assert caught.value.status_code == 200


def test_no_content_is_a_successful_empty_result() -> None:
    response = _response(204)
    response.json.side_effect = AssertionError("JSON must not be parsed")
    client, _session = _client([response])

    result = client.get_wellness_result(
        "2026-06-01", "2026-06-30"
    )

    assert result.status_code == 204
    assert result.payload is None


@pytest.mark.parametrize(
    ("timeout", "max_retries"),
    [
        ((0.0, 30.0), 2),
        ((5.0, -1.0), 2),
        ((5.0, 30.0), -1),
        ((5.0, 30.0), 3),
    ],
)
def test_rejects_unsafe_transport_configuration(
    timeout: tuple[float, float], max_retries: int
) -> None:
    with pytest.raises(ValueError):
        IntervalsClient(
            TOKEN,
            ATHLETE_ID,
            timeout=timeout,
            max_retries=max_retries,
        )
