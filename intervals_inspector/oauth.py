"""Read-only Intervals.icu OAuth authorization-code helpers."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import requests

from intervals_inspector.security import (
    SignedStatePayload,
    StateExpiredError,
    StateValidationError,
    create_signed_state,
    verify_signed_state,
)


AUTHORIZATION_ENDPOINT = "https://intervals.icu/oauth/authorize"
TOKEN_ENDPOINT = "https://intervals.icu/api/oauth/token"
READ_ONLY_SCOPES = (
    "ACTIVITY:READ",
    "WELLNESS:READ",
    "SETTINGS:READ",
    "CALENDAR:READ",
)
_SAFE_ATHLETE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class OAuthCallbackError(ValueError):
    """A safe, user-presentable OAuth callback failure."""


class OAuthAccessDenied(OAuthCallbackError):
    """The resource owner declined the OAuth grant."""


class OAuthExchangeError(RuntimeError):
    """A sanitized token-exchange failure."""


@dataclass(frozen=True)
class OAuthCallback:
    code: str = field(repr=False)
    state: str = field(repr=False)


@dataclass(frozen=True)
class OAuthGrant:
    """Session-only OAuth result; token fields are hidden from repr."""

    access_token: str = field(repr=False)
    athlete_id: str
    athlete_name: str | None
    scopes: tuple[str, ...]
    token_type: str | None = None
    expires_in: int | None = None


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} трябва да е непразен текст.")
    return value


def _first_query_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return value if isinstance(value, str) else str(value or "")


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
) -> str:
    """Build an authorization URL with the exact approved read scopes."""

    params = {
        "client_id": _required_text(client_id, "client_id"),
        "redirect_uri": _required_text(redirect_uri, "redirect_uri"),
        "response_type": "code",
        # Intervals.icu explicitly requires comma-separated scopes.
        "scope": ",".join(READ_ONLY_SCOPES),
        "state": _required_text(state, "state"),
    }
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


def parse_callback(query: Mapping[str, Any]) -> OAuthCallback:
    """Parse callback parameters without retaining an error description."""

    error = _first_query_value(query.get("error")).strip()
    if error:
        if error.lower() == "access_denied":
            raise OAuthAccessDenied("OAuth достъпът е отказан.")
        raise OAuthCallbackError("Intervals.icu върна OAuth грешка.")

    code = _first_query_value(query.get("code")).strip()
    state = _first_query_value(query.get("state")).strip()
    if not code or not state:
        raise OAuthCallbackError("OAuth callback-ът е непълен.")
    return OAuthCallback(code=code, state=state)


def _parse_scopes(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = re.split(r"[\s,]+", value.strip())
    elif isinstance(value, (list, tuple, set)):
        candidates = [str(item).strip() for item in value]
    else:
        candidates = []
    return tuple(dict.fromkeys(item.upper() for item in candidates if item))


def _parse_athlete_id(value: Any) -> str:
    if isinstance(value, bool):
        raise OAuthExchangeError(
            "OAuth отговорът съдържа невалиден athlete.id."
        )
    if isinstance(value, int):
        rendered = str(value)
    elif isinstance(value, str):
        rendered = value
    else:
        raise OAuthExchangeError(
            "OAuth отговорът съдържа невалиден athlete.id."
        )
    if rendered == "0" or not _SAFE_ATHLETE_ID_RE.fullmatch(rendered):
        raise OAuthExchangeError(
            "OAuth отговорът съдържа невалиден athlete.id."
        )
    return rendered


def exchange_authorization_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    http_post: Callable[..., Any] | None = None,
    timeout: tuple[float, float] = (3.05, 15.0),
) -> OAuthGrant:
    """Exchange a short-lived code and return only a session grant object."""

    form = {
        "client_id": _required_text(client_id, "client_id"),
        "client_secret": _required_text(client_secret, "client_secret"),
        "code": _required_text(code, "code"),
    }
    post = requests.post if http_post is None else http_post
    try:
        response = post(
            TOKEN_ENDPOINT,
            data=form,
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.RequestException:
        raise OAuthExchangeError(
            "OAuth token заявката не беше успешна."
        ) from None
    except Exception:
        raise OAuthExchangeError(
            "OAuth token заявката не беше успешна."
        ) from None

    status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int) or not 200 <= status_code < 300:
        status_suffix = (
            f" (HTTP {status_code})"
            if isinstance(status_code, int)
            else ""
        )
        raise OAuthExchangeError(
            "Intervals.icu отказа OAuth token заявката"
            f"{status_suffix}."
        )
    try:
        payload = response.json()
    except Exception:
        raise OAuthExchangeError(
            "Intervals.icu върна невалиден OAuth отговор."
        ) from None
    if not isinstance(payload, Mapping):
        raise OAuthExchangeError(
            "Intervals.icu върна невалиден OAuth отговор."
        )

    access_token = payload.get("access_token")
    athlete = payload.get("athlete")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthExchangeError(
            "OAuth отговорът не съдържа access token."
        )
    if not isinstance(athlete, Mapping):
        raise OAuthExchangeError(
            "OAuth отговорът не съдържа athlete.id."
        )
    athlete_id = _parse_athlete_id(athlete.get("id"))

    token_type_value = payload.get("token_type")
    if (
        not isinstance(token_type_value, str)
        or token_type_value.casefold() != "bearer"
    ):
        raise OAuthExchangeError(
            "OAuth отговорът не съдържа валиден Bearer token."
        )

    athlete_name_value = athlete.get("name")
    athlete_name = (
        str(athlete_name_value) if athlete_name_value not in (None, "") else None
    )
    expires_value = payload.get("expires_in")
    try:
        expires_in = int(expires_value) if expires_value is not None else None
    except (TypeError, ValueError):
        expires_in = None
    granted_scopes = _parse_scopes(
        payload.get("scope", payload.get("scopes"))
    )
    if any(scope not in READ_ONLY_SCOPES for scope in granted_scopes):
        raise OAuthExchangeError(
            "OAuth отговорът съдържа непозволени права."
        )

    return OAuthGrant(
        access_token=access_token,
        athlete_id=athlete_id,
        athlete_name=athlete_name,
        scopes=granted_scopes,
        token_type=token_type_value,
        expires_in=expires_in,
    )


__all__ = [
    "AUTHORIZATION_ENDPOINT",
    "TOKEN_ENDPOINT",
    "READ_ONLY_SCOPES",
    "OAuthAccessDenied",
    "OAuthCallback",
    "OAuthCallbackError",
    "OAuthExchangeError",
    "OAuthGrant",
    "SignedStatePayload",
    "StateExpiredError",
    "StateValidationError",
    "build_authorization_url",
    "create_signed_state",
    "exchange_authorization_code",
    "parse_callback",
    "verify_signed_state",
]
