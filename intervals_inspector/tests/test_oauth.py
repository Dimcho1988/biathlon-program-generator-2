from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
import requests

from intervals_inspector.oauth import (
    AUTHORIZATION_ENDPOINT,
    READ_ONLY_SCOPES,
    TOKEN_ENDPOINT,
    OAuthAccessDenied,
    OAuthCallbackError,
    OAuthExchangeError,
    build_authorization_url,
    exchange_authorization_code,
    parse_callback,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


def test_authorization_url_has_exact_read_only_scopes() -> None:
    url = build_authorization_url(
        client_id="test-client",
        redirect_uri="http://localhost:8501/",
        state="signed-state",
    )

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        AUTHORIZATION_ENDPOINT
    )
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["test-client"]
    assert query["redirect_uri"] == ["http://localhost:8501/"]
    assert query["state"] == ["signed-state"]
    assert query["scope"][0].split(",") == list(READ_ONLY_SCOPES)
    assert all("WRITE" not in scope for scope in READ_ONLY_SCOPES)


def test_callback_parses_scalar_and_streamlit_list_values() -> None:
    callback = parse_callback({"code": ["one-time"], "state": "signed"})
    assert callback.code == "one-time"
    assert callback.state == "signed"


def test_callback_reports_denied_and_other_errors_without_description() -> None:
    with pytest.raises(OAuthAccessDenied):
        parse_callback(
            {
                "error": "access_denied",
                "error_description": "sensitive provider detail",
            }
        )
    with pytest.raises(OAuthCallbackError) as caught:
        parse_callback(
            {
                "error": "server_error",
                "error_description": "sensitive provider detail",
            }
        )
    assert "sensitive provider detail" not in str(caught.value)


def test_callback_requires_code_and_state() -> None:
    with pytest.raises(OAuthCallbackError):
        parse_callback({"code": "one-time"})
    with pytest.raises(OAuthCallbackError):
        parse_callback({"state": "signed"})


def test_token_exchange_posts_form_and_requires_oauth_athlete_id() -> None:
    observed: dict[str, object] = {}

    def post(url: str, **kwargs: object) -> FakeResponse:
        observed["url"] = url
        observed.update(kwargs)
        return FakeResponse(
            200,
            {
                "access_token": "session-token",
                "token_type": "Bearer",
                "scope": "ACTIVITY:READ WELLNESS:READ",
                "athlete": {"id": "i123", "name": "Test Athlete"},
            },
        )

    grant = exchange_authorization_code(
        client_id="test-client",
        client_secret="test-secret",
        code="one-time-code",
        http_post=post,
    )

    assert observed["url"] == TOKEN_ENDPOINT
    assert observed["data"] == {
        "client_id": "test-client",
        "client_secret": "test-secret",
        "code": "one-time-code",
    }
    assert observed["allow_redirects"] is False
    assert grant.access_token == "session-token"
    assert grant.athlete_id == "i123"
    assert grant.athlete_name == "Test Athlete"
    assert grant.scopes == ("ACTIVITY:READ", "WELLNESS:READ")
    assert "session-token" not in repr(grant)


def test_token_exchange_rejects_missing_athlete_id() -> None:
    def post(url: str, **kwargs: object) -> FakeResponse:
        return FakeResponse(
            200,
            {
                "access_token": "session-token",
                "token_type": "Bearer",
                "athlete": {"name": "No ID"},
            },
        )

    with pytest.raises(OAuthExchangeError):
        exchange_authorization_code(
            client_id="test-client",
            client_secret="test-secret",
            code="one-time-code",
            http_post=post,
        )


@pytest.mark.parametrize(
    "scope",
    [
        "ACTIVITY:READ,CALENDAR:WRITE",
        "ACTIVITY:READ,CHATS:READ",
    ],
)
def test_token_exchange_rejects_non_allowlisted_scopes(scope: str) -> None:
    def post(url: str, **kwargs: object) -> FakeResponse:
        return FakeResponse(
            200,
            {
                "access_token": "overprivileged-token",
                "token_type": "Bearer",
                "scope": scope,
                "athlete": {"id": "i123", "name": "Test Athlete"},
            },
        )

    with pytest.raises(OAuthExchangeError) as caught:
        exchange_authorization_code(
            client_id="test-client",
            client_secret="test-secret",
            code="one-time-code",
            http_post=post,
        )

    assert "overprivileged-token" not in str(caught.value)


@pytest.mark.parametrize(
    "athlete_id",
    [None, "", "0", 0, False, " i123", "i123/other", [], 1.5],
)
def test_token_exchange_rejects_invalid_athlete_id(athlete_id: object) -> None:
    def post(url: str, **kwargs: object) -> FakeResponse:
        return FakeResponse(
            200,
            {
                "access_token": "session-token",
                "token_type": "Bearer",
                "scope": "ACTIVITY:READ",
                "athlete": {"id": athlete_id},
            },
        )

    with pytest.raises(OAuthExchangeError):
        exchange_authorization_code(
            client_id="test-client",
            client_secret="test-secret",
            code="one-time-code",
            http_post=post,
        )


@pytest.mark.parametrize("token_type", [None, "", "Basic", 123])
def test_token_exchange_requires_bearer_token_type(token_type: object) -> None:
    def post(url: str, **kwargs: object) -> FakeResponse:
        return FakeResponse(
            200,
            {
                "access_token": "session-token",
                "token_type": token_type,
                "scope": "ACTIVITY:READ",
                "athlete": {"id": "i123"},
            },
        )

    with pytest.raises(OAuthExchangeError):
        exchange_authorization_code(
            client_id="test-client",
            client_secret="test-secret",
            code="one-time-code",
            http_post=post,
        )


@pytest.mark.parametrize(
    "status_code", [307, 308, 400, 401, 403, 429, 500, 503]
)
def test_token_exchange_http_failures_are_sanitized(
    status_code: int,
) -> None:
    def post(url: str, **kwargs: object) -> FakeResponse:
        return FakeResponse(
            status_code,
            {
                "error": "provider_error",
                "error_description": "secret provider detail",
            },
        )

    with pytest.raises(OAuthExchangeError) as caught:
        exchange_authorization_code(
            client_id="test-client",
            client_secret="do-not-leak",
            code="one-time-code",
            http_post=post,
        )
    message = str(caught.value)
    assert f"HTTP {status_code}" in message
    assert "do-not-leak" not in message
    assert "secret provider detail" not in message


def test_token_exchange_network_failure_is_sanitized() -> None:
    def post(url: str, **kwargs: object) -> FakeResponse:
        raise requests.Timeout("contains request details")

    with pytest.raises(OAuthExchangeError) as caught:
        exchange_authorization_code(
            client_id="test-client",
            client_secret="do-not-leak",
            code="one-time-code",
            http_post=post,
        )
    assert "do-not-leak" not in str(caught.value)
    assert "request details" not in str(caught.value)
