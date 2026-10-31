"""Intervals OAuth orchestration for the FastAPI cloud boundary."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
import os
import secrets
from typing import Mapping

from .oauth_store import SupabasePilotRepository


READ_ONLY_SCOPES = (
    "ACTIVITY:READ",
    "WELLNESS:READ",
    "SETTINGS:READ",
    "CALENDAR:READ",
)


def exchange_authorization_code(**kwargs):
    """Lazy provider import keeps FastAPI startup credential-free."""
    from intervals_inspector.oauth import exchange_authorization_code as exchange

    return exchange(**kwargs)


class OAuthConfigurationError(RuntimeError):
    """Safe OAuth configuration failure."""


class OAuthFlowError(RuntimeError):
    """Safe OAuth flow failure without codes, tokens or provider identity."""

    def __init__(self, message: str, *, stage: str = "callback") -> None:
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True)
class OAuthSettings:
    client_id: str
    client_secret: str
    redirect_uri: str
    state_secret: str
    web_base_url: str


@dataclass(frozen=True)
class OAuthStatus:
    connected: bool
    scopes: tuple[str, ...]


def settings_from_environment(
    environ: Mapping[str, str] | None = None,
) -> OAuthSettings:
    env = environ or os.environ
    values = {
        "client_id": env.get("INTERVALS_CLIENT_ID", "").strip(),
        "client_secret": env.get("INTERVALS_CLIENT_SECRET", "").strip(),
        "redirect_uri": env.get("INTERVALS_REDIRECT_URI", "").strip(),
        "state_secret": env.get("OAUTH_STATE_SECRET", "").strip(),
        "web_base_url": env.get("ONFLOWS_WEB_BASE_URL", "").strip(),
    }
    if any(not value for value in values.values()):
        raise OAuthConfigurationError("OAuth server configuration is incomplete")
    if not values["redirect_uri"].startswith("https://"):
        raise OAuthConfigurationError("OAuth redirect URI must use HTTPS")
    if not values["web_base_url"].startswith("https://"):
        raise OAuthConfigurationError("Web application URL must use HTTPS")
    return OAuthSettings(**values)


def begin_authorization(
    repository: SupabasePilotRepository,
    *,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
    athlete_alias: str | None = None,
) -> str:
    from intervals_inspector.oauth import (
        build_authorization_url,
        create_signed_state,
        verify_signed_state,
    )

    settings = settings_from_environment(environ)
    clock = now or datetime.now(timezone.utc)
    state = create_signed_state(
        settings.state_secret,
        redirect_uri=settings.redirect_uri,
        now=clock.timestamp(),
    )
    verified = verify_signed_state(
        state,
        settings.state_secret,
        expected_redirect_uri=settings.redirect_uri,
        now=clock.timestamp(),
    )
    repository.create_oauth_state(
        nonce=verified.nonce,
        athlete_alias=athlete_alias,
        redirect_uri=settings.redirect_uri,
        expires_at=clock + timedelta(minutes=10),
    )
    return build_authorization_url(
        client_id=settings.client_id,
        redirect_uri=settings.redirect_uri,
        state=state,
    )


def complete_authorization(
    repository: SupabasePilotRepository,
    query: Mapping[str, object],
    *,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> str:
    from intervals_inspector.oauth import (
        OAuthCallbackError,
        OAuthExchangeError,
        parse_callback,
        verify_signed_state,
    )

    settings = settings_from_environment(environ)
    clock = now or datetime.now(timezone.utc)
    try:
        callback = parse_callback(query)
    except (OAuthCallbackError, ValueError) as exc:
        raise OAuthFlowError(
            "Intervals OAuth callback is invalid", stage="callback"
        ) from exc
    try:
        verified = verify_signed_state(
            callback.state,
            settings.state_secret,
            expected_redirect_uri=settings.redirect_uri,
            now=clock.timestamp(),
        )
        pending = repository.consume_oauth_state(verified.nonce)
        if pending is None:
            raise OAuthFlowError(
                "OAuth state is invalid, expired or already used", stage="state"
            )
        if not hmac.compare_digest(pending.redirect_uri, settings.redirect_uri):
            raise OAuthFlowError("OAuth redirect binding is invalid", stage="state")
    except OAuthFlowError:
        raise
    except ValueError as exc:
        raise OAuthFlowError("OAuth state is invalid", stage="state") from exc
    try:
        grant = exchange_authorization_code(
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            code=callback.code,
            redact_values=(callback.state,),
        )
    except (OAuthExchangeError, ValueError) as exc:
        raise OAuthFlowError(
            "Intervals OAuth connection could not be completed", stage="exchange"
        ) from exc
    granted = set(grant.scopes)
    if not set(READ_ONLY_SCOPES).issubset(granted):
        raise OAuthFlowError(
            "Intervals OAuth grant is missing required read permissions",
            stage="permissions",
        )

    existing_alias = repository.alias_for_provider(grant.athlete_id)
    if pending.athlete_alias is not None:
        alias_provider = repository.provider_for_alias(pending.athlete_alias)
        if existing_alias is not None and not hmac.compare_digest(
            existing_alias, pending.athlete_alias
        ):
            raise OAuthFlowError(
                "Intervals profile is already bound to another athlete",
                stage="binding",
            )
        if alias_provider is not None and not hmac.compare_digest(
            alias_provider, grant.athlete_id
        ):
            raise OAuthFlowError(
                "Athlete session is bound to another Intervals profile",
                stage="binding",
            )
        athlete_alias = pending.athlete_alias
    elif existing_alias is not None:
        athlete_alias = existing_alias
    else:
        athlete_alias = _new_athlete_alias(repository)

    repository.save_connection(
        athlete_alias=athlete_alias,
        provider_athlete_id=grant.athlete_id,
        access_token=grant.access_token,
        scopes=grant.scopes,
    )
    return athlete_alias


def _new_athlete_alias(repository: SupabasePilotRepository) -> str:
    for _ in range(5):
        candidate = f"ath-{secrets.token_hex(10)}"
        if repository.provider_for_alias(candidate) is None:
            return candidate
    raise OAuthFlowError(
        "A unique athlete identity could not be created", stage="identity"
    )


def issue_login_ticket(
    repository: SupabasePilotRepository,
    athlete_alias: str,
    *,
    now: datetime | None = None,
) -> str:
    clock = now or datetime.now(timezone.utc)
    ticket = secrets.token_urlsafe(32)
    repository.create_login_ticket(
        ticket=ticket,
        athlete_alias=athlete_alias,
        expires_at=clock + timedelta(minutes=5),
    )
    return ticket


def connection_status(
    repository: SupabasePilotRepository,
    *,
    athlete_alias: str,
) -> OAuthStatus:
    connection = repository.connection(athlete_alias)
    if connection is None or connection.status != "CONNECTED":
        return OAuthStatus(False, ())
    return OAuthStatus(True, connection.scopes)


__all__ = [
    "OAuthConfigurationError",
    "OAuthFlowError",
    "OAuthSettings",
    "OAuthStatus",
    "begin_authorization",
    "complete_authorization",
    "connection_status",
    "issue_login_ticket",
    "settings_from_environment",
]
