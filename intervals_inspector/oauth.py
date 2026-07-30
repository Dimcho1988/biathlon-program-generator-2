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
_PROVIDER_ERROR_MAX_LENGTH = 240
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"authorization[_ -]?code|code|state|client[_ -]?secret|"
    r"access[_ -]?token|refresh[_ -]?token|id[_ -]?token|token"
    r")\b\s*[:=]\s*[^\s,;]+"
)
_BEARER_VALUE_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_TOKEN_LABEL_VALUE_RE = re.compile(
    r"(?i)\b((?:access|refresh|id)[_ -]?token|token)\b\s+[^\s,;]+"
)
_OPAQUE_CREDENTIAL_RE = re.compile(
    r"(?<![A-Za-z0-9._~+/=-])[A-Za-z0-9._~+/=-]{32,}"
    r"(?![A-Za-z0-9._~+/=-])"
)
_MARKDOWN_META_RE = re.compile(r"([\\`*\[\]()<>!])")


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


def _safe_provider_error_value(
    value: Any,
    *,
    redactions: tuple[str, ...],
) -> str | None:
    """Return one bounded provider error field with credentials redacted."""

    if not isinstance(value, str):
        return None
    sensitive_values = sorted(
        (item for item in redactions if item),
        key=len,
        reverse=True,
    )
    safe_value = value
    for sensitive_value in sensitive_values:
        safe_value = safe_value.replace(sensitive_value, "[redacted]")
    safe_value = _CONTROL_CHARACTER_RE.sub("", safe_value)
    safe_value = " ".join(safe_value.split())
    for sensitive_value in sensitive_values:
        normalized_sensitive_value = _CONTROL_CHARACTER_RE.sub(
            "",
            sensitive_value,
        )
        normalized_sensitive_value = " ".join(
            normalized_sensitive_value.split()
        )
        if normalized_sensitive_value:
            safe_value = safe_value.replace(
                normalized_sensitive_value,
                "[redacted]",
            )
    safe_value = _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=[redacted]",
        safe_value,
    )
    safe_value = _BEARER_VALUE_RE.sub("Bearer [redacted]", safe_value)
    safe_value = _TOKEN_LABEL_VALUE_RE.sub(
        lambda match: f"{match.group(1)}=[redacted]",
        safe_value,
    )
    safe_value = _OPAQUE_CREDENTIAL_RE.sub("[redacted]", safe_value)
    if len(safe_value) > _PROVIDER_ERROR_MAX_LENGTH:
        safe_value = (
            safe_value[:_PROVIDER_ERROR_MAX_LENGTH].rstrip() + "…"
        )
    safe_value = _MARKDOWN_META_RE.sub(r"\\\1", safe_value)
    return safe_value or None


def _provider_token_error_details(
    response: Any,
    *,
    redactions: tuple[str, ...],
) -> tuple[str, ...]:
    """Extract only safe OAuth error fields from a failed token response."""

    try:
        payload = response.json()
    except Exception:
        return ()
    if not isinstance(payload, Mapping):
        return ()

    provider_redactions = list(redactions)
    for sensitive_field in (
        "code",
        "state",
        "client_secret",
        "access_token",
        "refresh_token",
        "id_token",
        "token",
    ):
        sensitive_value = payload.get(sensitive_field)
        if isinstance(sensitive_value, str) and sensitive_value:
            provider_redactions.append(sensitive_value)

    details: list[str] = []
    for field_name in ("error", "error_description"):
        safe_value = _safe_provider_error_value(
            payload.get(field_name),
            redactions=tuple(provider_redactions),
        )
        if safe_value:
            details.append(f"{field_name}={safe_value}")
    return tuple(details)


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
    redact_values: tuple[str, ...] = (),
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
        safe_details = _provider_token_error_details(
            response,
            redactions=(
                form["client_secret"],
                form["code"],
                *redact_values,
            ),
        )
        diagnostic_parts = (
            ([f"HTTP {status_code}"] if isinstance(status_code, int) else [])
            + list(safe_details)
        )
        diagnostic_suffix = (
            f" ({'; '.join(diagnostic_parts)})"
            if diagnostic_parts
            else ""
        )
        raise OAuthExchangeError(
            "Intervals.icu отказа OAuth token заявката"
            f"{diagnostic_suffix}."
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
