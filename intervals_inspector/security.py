"""Small, dependency-free security helpers for the OAuth callback."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any


DEFAULT_STATE_MAX_AGE_SECONDS = 600
MAX_FUTURE_CLOCK_SKEW_SECONDS = 30
MAX_STATE_LENGTH = 4096


class StateValidationError(ValueError):
    """Raised when an OAuth state value cannot be trusted."""


class StateExpiredError(StateValidationError):
    """Raised when a correctly signed OAuth state is too old."""


@dataclass(frozen=True)
class SignedStatePayload:
    """Verified, non-secret state data."""

    nonce: str
    issued_at: int
    redirect_uri: str


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise StateValidationError("Невалиден OAuth state.") from exc


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} трябва да е непразен текст.")
    return value


def create_signed_state(
    secret: str,
    *,
    redirect_uri: str,
    now: float | int | None = None,
    nonce: str | None = None,
) -> str:
    """Create an HMAC-SHA256 state bound to one exact redirect URI."""

    secret = _required_text(secret, "secret")
    redirect_uri = _required_text(redirect_uri, "redirect_uri")
    nonce_value = nonce if nonce is not None else secrets.token_urlsafe(24)
    nonce_value = _required_text(nonce_value, "nonce")
    issued_at = int(time.time() if now is None else now)
    payload = {
        "iat": issued_at,
        "nonce": nonce_value,
        "redirect_uri": redirect_uri,
    }
    encoded_payload = _b64url_encode(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    signature = hmac.new(
        secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_payload}.{_b64url_encode(signature)}"


def verify_signed_state(
    state: str,
    secret: str,
    *,
    expected_redirect_uri: str,
    max_age_seconds: int = DEFAULT_STATE_MAX_AGE_SECONDS,
    now: float | int | None = None,
) -> SignedStatePayload:
    """Verify signature, shape, redirect binding and bounded lifetime."""

    state = _required_text(state, "state")
    secret = _required_text(secret, "secret")
    expected_redirect_uri = _required_text(
        expected_redirect_uri, "expected_redirect_uri"
    )
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds трябва да е положително.")
    if len(state) > MAX_STATE_LENGTH:
        raise StateValidationError("Невалиден OAuth state.")
    try:
        state.encode("ascii")
    except UnicodeEncodeError:
        raise StateValidationError("Невалиден OAuth state.") from None

    try:
        encoded_payload, encoded_signature = state.split(".")
    except ValueError as exc:
        raise StateValidationError("Невалиден OAuth state.") from exc
    if not encoded_payload or not encoded_signature:
        raise StateValidationError("Невалиден OAuth state.")

    supplied_signature = _b64url_decode(encoded_signature)
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise StateValidationError("Невалиден OAuth state.")

    try:
        raw_payload = json.loads(_b64url_decode(encoded_payload))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StateValidationError("Невалиден OAuth state.") from exc
    if not isinstance(raw_payload, dict):
        raise StateValidationError("Невалиден OAuth state.")

    nonce = raw_payload.get("nonce")
    issued_at = raw_payload.get("iat")
    redirect_uri = raw_payload.get("redirect_uri")
    if (
        not isinstance(nonce, str)
        or not nonce
        or isinstance(issued_at, bool)
        or not isinstance(issued_at, int)
        or not isinstance(redirect_uri, str)
        or not redirect_uri
    ):
        raise StateValidationError("Невалиден OAuth state.")
    if not hmac.compare_digest(redirect_uri, expected_redirect_uri):
        raise StateValidationError("OAuth state не съвпада с redirect URI.")

    current_time = int(time.time() if now is None else now)
    if issued_at > current_time + MAX_FUTURE_CLOCK_SKEW_SECONDS:
        raise StateValidationError("OAuth state има невалидно време.")
    if current_time - issued_at > max_age_seconds:
        raise StateExpiredError("OAuth state е изтекъл.")

    return SignedStatePayload(
        nonce=nonce,
        issued_at=issued_at,
        redirect_uri=redirect_uri,
    )
