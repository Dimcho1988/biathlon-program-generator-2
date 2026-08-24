from __future__ import annotations

import base64
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from apps.api import oauth_service
from apps.api.cloud import (
    AthleteMesocycleAccentPreferences,
    AthletePlanningCalendar,
    AthletePlanningCalendarEvent,
    AthletePlanningProfile,
)
from apps.api.oauth_service import (
    OAuthFlowError,
    begin_authorization,
    complete_authorization,
    issue_login_ticket,
)
from apps.api.oauth_store import (
    PendingOAuthState,
    PersistentStoreFailure,
    SupabasePilotRepository,
    TokenCipher,
)
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
        self.aliases_by_provider = {"12345": "pilot"}
        self.providers_by_alias = {"pilot": "12345"}
        self.ticket = None

    def create_oauth_state(self, **values):
        self.pending = values

    def consume_oauth_state(self, nonce):
        if self.pending is None or self.pending["nonce"] != nonce:
            return None
        self.pending = None
        return PendingOAuthState(
            self.pending_alias, ENV["INTERVALS_REDIRECT_URI"]
        )

    @property
    def pending_alias(self):
        return "pilot"

    def alias_for_provider(self, provider_athlete_id):
        return self.aliases_by_provider.get(provider_athlete_id)

    def provider_for_alias(self, athlete_alias):
        return self.providers_by_alias.get(athlete_alias)

    def save_connection(self, **values):
        self.saved = values

    def create_login_ticket(self, **values):
        self.ticket = values


class CapturingClient:
    def __init__(self):
        self.headers = None

    def request(self, method, url, *, headers, **kwargs):
        self.headers = headers
        return httpx.Response(201)


class FailingClient:
    def request(self, method, url, *, headers, **kwargs):
        return httpx.Response(
            400,
            json={
                "code": "23514",
                "message": 'new row violates check constraint "hr_coverage_percent_check"',
                "details": "Failing row contains Private athlete note",
            },
        )


class TransientAuthClient:
    def __init__(self):
        self.calls = 0

    def request(self, method, url, *, headers, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return httpx.Response(
                401,
                json={
                    "code": "PGRST303",
                    "message": "JWT claims validation failed",
                },
            )
        return httpx.Response(200, json=[])


class PlanningProfileClient:
    def __init__(self, payload):
        self.payload = payload
        self.saved = None
        self.url = None

    def request(self, method, url, *, headers, **kwargs):
        self.url = url
        if method == "GET":
            return httpx.Response(200, json=[{"planning_profile": self.payload}])
        self.saved = kwargs["json"]["planning_profile"]
        return httpx.Response(204)


class MesocycleAccentPreferencesClient:
    def __init__(self, payload):
        self.payload = payload
        self.saved = None
        self.url = None

    def request(self, method, url, *, headers, **kwargs):
        self.url = url
        if method == "GET":
            return httpx.Response(
                200,
                json=[{"mesocycle_accent_preferences": self.payload}],
            )
        self.saved = kwargs["json"]["mesocycle_accent_preferences"]
        return httpx.Response(204)


class PlanningCalendarClient:
    def __init__(self, payload):
        self.payload = payload
        self.saved = None
        self.url = None

    def request(self, method, url, *, headers, **kwargs):
        self.url = url
        if method == "GET":
            return httpx.Response(200, json=[{"planning_calendar": self.payload}])
        self.saved = kwargs["json"]["planning_calendar"]
        return httpx.Response(204)


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


def test_supabase_failure_logs_only_safe_resource_and_status(caplog):
    repository = SupabasePilotRepository(
        supabase_url="https://project.supabase.co",
        secret_key="sb_secret_server-key",
        encryption_key=base64.urlsafe_b64encode(bytes(range(32))).decode(),
        client=FailingClient(),
    )

    with caplog.at_level("WARNING"), pytest.raises(PersistentStoreFailure) as error:
        repository.activity_calendar(
            "private-athlete-alias", date(2026, 8, 1), date(2026, 8, 23)
        )

    assert "GET /onflows_activity_catalog (400; code=23514 category=check" in str(
        error.value
    )
    assert (
        "resource=/onflows_activity_catalog status=400 "
        "code=23514 category=check target=hr_coverage_percent_check"
    ) in caplog.text
    assert "private-athlete-alias" not in caplog.text
    assert "Private athlete note" not in caplog.text


def test_supabase_retries_one_transient_get_auth_failure(caplog):
    client = TransientAuthClient()
    repository = SupabasePilotRepository(
        supabase_url="https://project.supabase.co",
        secret_key="sb_secret_server-key",
        encryption_key=base64.urlsafe_b64encode(bytes(range(32))).decode(),
        client=client,
    )

    with caplog.at_level("WARNING"):
        rows = repository.activity_calendar(
            "private-athlete-alias", date(2026, 8, 1), date(2026, 8, 23)
        )

    assert rows == ()
    assert client.calls == 2
    assert (
        "persistent_store_request_retry method=GET "
        "resource=/onflows_activity_catalog status=401 code=PGRST303"
    ) in caplog.text
    assert "private-athlete-alias" not in caplog.text


def test_supabase_never_retries_a_write_after_auth_failure():
    client = TransientAuthClient()
    repository = SupabasePilotRepository(
        supabase_url="https://project.supabase.co",
        secret_key="sb_secret_server-key",
        encryption_key=base64.urlsafe_b64encode(bytes(range(32))).decode(),
        client=client,
    )

    with pytest.raises(PersistentStoreFailure):
        repository.create_oauth_state(
            nonce="nonce",
            athlete_alias="pilot",
            redirect_uri=ENV["INTERVALS_REDIRECT_URI"],
            expires_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )

    assert client.calls == 1


def test_supabase_planning_profile_round_trip_is_scoped_to_alias():
    profile = AthletePlanningProfile(
        season_start=date(2026, 1, 1),
        season_end=date(2026, 12, 31),
        annual_target_hours=600.0,
        sessions_per_week=9,
        rest_days=(0,),
        double_session_days=(2, 5),
        long_session_day=6,
        intensity_days=(2, 5),
        strength_days=(1, 4),
        max_key_sessions_per_week=3,
        mesocycle_anchor_date=date(2026, 1, 1),
        mesocycle_length_weeks=4,
        camp_default_accent_limit=2,
        double_threshold_enabled=False,
        double_threshold_day=2,
        double_threshold_components=("Z3", "Z4"),
    ).validate()
    client = PlanningProfileClient(profile.to_payload())
    repository = SupabasePilotRepository(
        supabase_url="https://project.supabase.co",
        secret_key="sb_secret_server-key",
        encryption_key=base64.urlsafe_b64encode(bytes(range(32))).decode(),
        client=client,
    )

    assert repository.athlete_planning_profile("ath-profile") == profile
    assert "athlete_alias=eq.ath-profile" in client.url

    repository.save_athlete_planning_profile("ath-profile", profile)
    assert client.saved == profile.to_payload()
    assert "athlete_alias=eq.ath-profile" in client.url


def test_supabase_mesocycle_accent_preferences_round_trip_is_scoped_to_alias():
    preferences = AthleteMesocycleAccentPreferences(
        accent_mode="HYBRID",
        accent_limit=3,
        manual_components=("Z5",),
    ).validate()
    client = MesocycleAccentPreferencesClient(preferences.to_payload())
    repository = SupabasePilotRepository(
        supabase_url="https://project.supabase.co",
        secret_key="sb_secret_server-key",
        encryption_key=base64.urlsafe_b64encode(bytes(range(32))).decode(),
        client=client,
    )

    assert (
        repository.athlete_mesocycle_accent_preferences("ath-profile")
        == preferences
    )
    assert "athlete_alias=eq.ath-profile" in client.url

    repository.save_athlete_mesocycle_accent_preferences(
        "ath-profile", preferences
    )
    assert client.saved == preferences.to_payload()
    assert "athlete_alias=eq.ath-profile" in client.url


def test_supabase_planning_calendar_round_trip_is_scoped_to_alias():
    calendar = AthletePlanningCalendar(
        events=(
            AthletePlanningCalendarEvent(
                event_id="event-main-0001",
                event_type="MAIN_RACE",
                name="Основен старт",
                start_date=date(2026, 12, 12),
                end_date=date(2026, 12, 13),
            ),
        )
    ).validate()
    client = PlanningCalendarClient(calendar.to_payload())
    repository = SupabasePilotRepository(
        supabase_url="https://project.supabase.co",
        secret_key="sb_secret_server-key",
        encryption_key=base64.urlsafe_b64encode(bytes(range(32))).decode(),
        client=client,
    )

    assert repository.athlete_planning_calendar("ath-profile") == calendar
    assert "athlete_alias=eq.ath-profile" in client.url

    repository.save_athlete_planning_calendar("ath-profile", calendar)
    assert client.saved == calendar.to_payload()
    assert "athlete_alias=eq.ath-profile" in client.url


def test_oauth_state_is_persisted_consumed_once_and_grant_is_stored(monkeypatch):
    repository = Repository()
    now = datetime(2026, 8, 15, 18, tzinfo=timezone.utc)
    url = begin_authorization(repository, environ=ENV, now=now, athlete_alias="pilot")
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
    alias = complete_authorization(
        repository,
        {"code": "one-time-code", "state": state},
        environ=ENV,
        now=now,
    )
    assert alias == "pilot"
    assert repository.saved == {
        "athlete_alias": "pilot",
        "provider_athlete_id": "12345",
        "access_token": "provider-token",
        "scopes": READ_ONLY_SCOPES,
    }
    with pytest.raises(OAuthFlowError, match="invalid, expired or already used") as error:
        complete_authorization(
            repository,
            {"code": "replayed-code", "state": state},
            environ=ENV,
            now=now,
        )
    assert error.value.stage == "state"


def test_missing_read_scope_is_rejected(monkeypatch):
    repository = Repository()
    now = datetime(2026, 8, 15, 18, tzinfo=timezone.utc)
    url = begin_authorization(repository, environ=ENV, now=now, athlete_alias="pilot")
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
    with pytest.raises(OAuthFlowError, match="missing required read permissions") as error:
        complete_authorization(
            repository,
            {"code": "one-time-code", "state": state},
            environ=ENV,
            now=now,
        )
    assert error.value.stage == "permissions"


def test_new_provider_gets_opaque_alias_and_short_lived_login_ticket(monkeypatch):
    class NewRepository(Repository):
        @property
        def pending_alias(self):
            return None

    repository = NewRepository()
    repository.aliases_by_provider = {}
    repository.providers_by_alias = {}
    now = datetime(2026, 8, 15, 18, tzinfo=timezone.utc)
    url = begin_authorization(repository, environ=ENV, now=now)
    state = parse_qs(urlparse(url).query)["state"][0]
    monkeypatch.setattr(
        oauth_service,
        "exchange_authorization_code",
        lambda **_: OAuthGrant(
            access_token="provider-token",
            athlete_id="new-provider-id",
            athlete_name=None,
            scopes=READ_ONLY_SCOPES,
            token_type="Bearer",
        ),
    )

    alias = complete_authorization(
        repository,
        {"code": "one-time-code", "state": state},
        environ=ENV,
        now=now,
    )
    assert alias.startswith("ath-")
    assert "new-provider-id" not in alias
    ticket = issue_login_ticket(repository, alias, now=now)
    assert len(ticket) >= 32
    assert repository.ticket["athlete_alias"] == alias
    assert repository.ticket["expires_at"] > now
