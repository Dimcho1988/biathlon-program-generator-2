from __future__ import annotations

import base64
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from apps.api import oauth_service
from apps.api.oauth_service import OAuthFlowError, begin_authorization, complete_authorization
from apps.api.oauth_store import PendingOAuthState, SupabasePilotRepository, TokenCipher
from intervals_inspector.oauth import OAuthGrant, READ_ONLY_SCOPES


ENV = {
    "INTERVALS_CLIENT_ID": "client-id",
    "INTERVALS_CLIENT_SECRET": "client-secret",
    "INTERVALS_REDIRECT_URI": "https://api.example.test/api/v2/integrations/intervals/callback",
    "OAUTH_STATE_SECRET": "independent-state-secret",
    "ONFLOWS_ATHLETE_ALIAS": "pilot",
    "ONFLOWS_WEB_BASE_URL": "https://web.example.test",
}


class Repository:
    def __init__(self):
        self.pending = None
        self.saved = None

    def create_oauth_state(self, **values):
        self.pending = values

    def consume_oauth_state(self, nonce):
        if self.pending is None or self.pending["nonce"] != nonce:
            return None
        self.pending = None
        return PendingOAuthState("pilot", ENV["INTERVALS_REDIRECT_URI"])

    def save_connection(self, **values):
        self.saved = values


class CapturingClient:
    def __init__(self):
        self.headers = None

    def request(self, method, url, *, headers, **kwargs):
        self.headers = headers
        return httpx.Response(201)


def test_token_cipher_round_trip_is_bound_to_alias():
    encoded_key = base64.urlsafe_b64encode(bytes(range(32))).decode()
    cipher = TokenCipher(encoded_key)
    envelope = cipher.encrypt("provider-token", athlete_alias="pilot")
    assert "provider-token" not in envelope
    assert cipher.decrypt(envelope, athlete_alias="pilot") == "provider-token"
    with pytest.raises(Exception):
        cipher.decrypt(envelope, athlete_alias="another-athlete")


@pytest.mark.parametrize(
    ("secret_key", "expected_authorization"),
    [
        ("sb_secret_server-key", None),
        ("legacy.service.role", "Bearer legacy.service.role"),
    ],
)
def test_supabase_auth_headers_support_current_and_legacy_server_keys(
    secret_key, expected_authorization
):
    client = CapturingClient()
    repository = SupabasePilotRepository(
        supabase_url="https://project.supabase.co",
        secret_key=secret_key,
        encryption_key=base64.urlsafe_b64encode(bytes(range(32))).decode(),
        client=client,
    )
    repository.create_oauth_state(
        nonce="nonce",
        athlete_alias="pilot",
        redirect_uri=ENV["INTERVALS_REDIRECT_URI"],
        expires_at=datetime(2026, 8, 15, 18, tzinfo=timezone.utc),
    )
    assert client.headers["apikey"] == secret_key
    assert client.headers.get("Authorization") == expected_authorization


def test_oauth_state_is_persisted_consumed_once_and_grant_is_stored(monkeypatch):
    repository = Repository()
    now = datetime(2026, 8, 15, 18, tzinfo=timezone.utc)
    url = begin_authorization(repository, environ=ENV, now=now)
    query = parse_qs(urlparse(url).query)
    state = query["state"][0]
    assert query["scope"] == [",".join(READ_ONLY_SCOPES)]
    assert repository.pending is not None

    monkeypatch.setattr(
        oauth_service,
        "exchange_authorization_code",
        lambda **_: OAuthGrant(
            access_token="provider-token",
            athlete_id="12345",
            athlete_name="Private name",
            scopes=READ_ONLY_SCOPES,
            token_type="Bearer",
        ),
    )
    complete_authorization(
        repository,
        {"code": "one-time-code", "state": state},
        environ=ENV,
        now=now,
    )
    assert repository.saved == {
        "athlete_alias": "pilot",
        "provider_athlete_id": "12345",
        "access_token": "provider-token",
        "scopes": READ_ONLY_SCOPES,
    }
    with pytest.raises(OAuthFlowError, match="invalid, expired or already used"):
        complete_authorization(
            repository,
            {"code": "replayed-code", "state": state},
            environ=ENV,
            now=now,
        )


def test_missing_read_scope_is_rejected(monkeypatch):
    repository = Repository()
    now = datetime(2026, 8, 15, 18, tzinfo=timezone.utc)
    url = begin_authorization(repository, environ=ENV, now=now)
    state = parse_qs(urlparse(url).query)["state"][0]
    monkeypatch.setattr(
        oauth_service,
        "exchange_authorization_code",
        lambda **_: OAuthGrant(
            access_token="provider-token",
            athlete_id="12345",
            athlete_name=None,
            scopes=("ACTIVITY:READ",),
            token_type="Bearer",
        ),
    )
    with pytest.raises(OAuthFlowError, match="missing required read permissions"):
        complete_authorization(
            repository,
            {"code": "one-time-code", "state": state},
            environ=ENV,
            now=now,
        )
