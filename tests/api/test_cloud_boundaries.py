from datetime import date, datetime, timezone
import math
from fastapi.testclient import TestClient
import pytest

from apps.api.cloud import AthleteContext, AthleteModelSettings, AthletePlanningProfile, InMemorySnapshotRepository, normalize_wellness, service_token_valid
from apps.api.hrmod import calculate_hrmod
from apps.api import main as api_main
from apps.api.main import app
from apps.api.oauth_service import OAuthFlowError


def test_athlete_configuration_validation_and_private_fingerprint():
    context = AthleteContext("pilot", "private-123", (90, 110, 130, 150, 170, 190), "Europe/Sofia", "intra_zone_linear_v1", "tref-300-180-70-20-20-v1", "recovery-v1")
    assert len(context.validate().fingerprint) == 64
    assert "private-123" not in context.fingerprint


def test_athlete_model_settings_require_real_boundaries_and_timezone():
    settings = AthleteModelSettings((100, 120, 140, 160, 180, 200), "Europe/Sofia")
    assert settings.validate() is settings
    with pytest.raises(ValueError, match="strictly increasing"):
        AthleteModelSettings((100, 120, 140, 140, 180, 200), "Europe/Sofia").validate()
    with pytest.raises(ValueError, match="IANA timezone"):
        AthleteModelSettings((100, 120, 140, 160, 180, 200), "Sofia").validate()


def planning_profile(**overrides):
    values = {
        "season_start": date(2026, 1, 1),
        "season_end": date(2026, 12, 31),
        "annual_target_hours": 600.0,
        "sessions_per_week": 9,
        "rest_days": (0,),
        "double_session_days": (2, 5),
        "long_session_day": 6,
        "intensity_days": (2, 5),
        "strength_days": (1, 4),
        "max_key_sessions_per_week": 3,
        "mesocycle_anchor_date": date(2026, 1, 1),
        "mesocycle_length_weeks": 4,
        "camp_default_accent_limit": 2,
        "double_threshold_enabled": False,
        "double_threshold_day": 2,
        "double_threshold_components": ("Z3", "Z4"),
    }
    values.update(overrides)
    return AthletePlanningProfile(**values)


def test_planning_profile_contains_only_individual_inputs_and_validates_structure():
    profile = planning_profile().validate()
    assert profile.to_payload()["schema_version"] == "planning-profile-v1"
    assert AthletePlanningProfile.from_mapping(profile.to_payload()) == profile
    assert "double_threshold_min_readiness" not in profile.to_payload()
    assert "annual_goal_influence" not in profile.to_payload()
    with pytest.raises(ValueError, match="session count"):
        planning_profile(sessions_per_week=13, rest_days=(0,)).validate()
    with pytest.raises(ValueError, match="cannot be a rest day"):
        planning_profile(
            double_threshold_enabled=True,
            double_threshold_day=0,
        ).validate()
    with pytest.raises(ValueError, match="fields are invalid"):
        AthletePlanningProfile.from_mapping(
            {**profile.to_payload(), "sessions_per_week": True}
        )


def test_wellness_preserves_missing_stale_invalid_and_units():
    result = normalize_wellness({"id": "2026-01-01", "sleepSecs": 28800, "fatigue": float("nan"), "illness": False}, now=datetime(2026, 1, 5, tzinfo=timezone.utc))
    assert result["freshness"] == "stale"
    assert result["values"]["sleep_duration"] == {"value": 28800.0, "unit": "s", "state": "valid"}
    assert result["values"]["fatigue"]["state"] == "invalid"
    assert result["values"]["stress"]["state"] == "missing"
    assert result["values"]["illness"]["value"] is False


def test_hrmod_is_deterministic_hr_only_and_finite():
    hr = [120, 121, None, 123, 124, 130, 131, 132, 133, 134]
    first = calculate_hrmod(hr)
    assert first == calculate_hrmod(hr)
    assert first["affects_final_decision"] is False and first["coverage"] == .9
    assert all(x is None or math.isfinite(x) for x in first["hrmod"])


def test_snapshot_atomic_replacement_retains_last_valid():
    repo = InMemorySnapshotRepository(); repo.replace("pilot", {"version": 1})
    assert repo.latest("pilot") == {"version": 1}


def test_real_endpoint_auth_and_no_snapshot(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "secret-value")
    client = TestClient(app)
    assert client.get("/api/v2/real/training-status").status_code == 401
    assert client.get("/api/v2/real/training-status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/v2/real/training-status", headers={"Authorization": "Bearer secret-value"}).status_code == 503
    assert client.get("/api/v2/real/load-history").status_code == 401
    assert client.get("/api/v2/real/volume-history").status_code == 401
    assert client.get("/api/v2/real/load-history", headers={"Authorization": "Bearer secret-value"}).status_code == 503
    assert client.get("/api/v2/real/completed-work").status_code == 401
    assert client.get("/api/v2/real/completed-work", headers={"Authorization": "Bearer secret-value"}).status_code == 503
    assert client.get("/api/v2/real/recovery-history").status_code == 401
    assert client.get("/api/v2/real/recovery-history", headers={"Authorization": "Bearer secret-value"}).status_code == 503


def test_constant_time_boundary_semantics():
    assert service_token_valid("same", "same")
    assert not service_token_valid(None, "same")
    assert not service_token_valid("same", "")


def test_real_endpoint_selects_only_the_server_authenticated_athlete(monkeypatch):
    class Repository:
        def __init__(self):
            self.requested_alias = None

        def latest(self, athlete_alias):
            self.requested_alias = athlete_alias
            return None

    repository = Repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "secret-value")
    monkeypatch.setenv("ONFLOWS_ATHLETE_ALIAS", "pilot")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)
    client = TestClient(app)

    response = client.get(
        "/api/v2/real/training-status",
        headers={
            "Authorization": "Bearer secret-value",
            "X-OnFlows-Athlete-Alias": "ath-second-profile",
        },
    )

    assert response.status_code == 503
    assert repository.requested_alias == "ath-second-profile"


def test_athlete_settings_are_scoped_to_the_authenticated_profile(monkeypatch):
    class Connection:
        status = "CONNECTED"

    class Repository:
        def __init__(self):
            self.items = {}

        def connection(self, athlete_alias):
            return Connection()

        def athlete_settings(self, athlete_alias):
            return self.items.get(athlete_alias)

        def save_athlete_settings(self, athlete_alias, settings):
            self.items[athlete_alias] = settings

    repository = Repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "secret-value")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer secret-value",
        "X-OnFlows-Athlete-Alias": "ath-second-profile",
    }
    body = {
        "hr_zone_bounds_bpm": [100, 120, 140, 160, 180, 200],
        "timezone": "Europe/Sofia",
    }

    saved = client.put("/api/v2/athlete/settings", headers=headers, json=body)
    loaded = client.get("/api/v2/athlete/settings", headers=headers)

    assert saved.status_code == 200
    assert loaded.json() == {"configured": True, **body}
    assert list(repository.items) == ["ath-second-profile"]


def test_planning_profile_is_scoped_and_rejects_inconsistent_structure(monkeypatch):
    class Repository:
        def __init__(self):
            self.profiles = {}

        def athlete_settings(self, athlete_alias):
            return AthleteModelSettings(
                (100, 120, 140, 160, 180, 200), "Europe/Sofia"
            )

        def athlete_planning_profile(self, athlete_alias):
            return self.profiles.get(athlete_alias)

        def save_athlete_planning_profile(self, athlete_alias, profile):
            self.profiles[athlete_alias] = profile

    repository = Repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "secret-value")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer secret-value",
        "X-OnFlows-Athlete-Alias": "ath-second-profile",
    }
    body = planning_profile().to_payload()

    saved = client.put(
        "/api/v2/athlete/planning-profile", headers=headers, json=body
    )
    loaded = client.get("/api/v2/athlete/planning-profile", headers=headers)

    assert saved.status_code == 200
    assert loaded.json() == {"configured": True, "profile": body}
    assert list(repository.profiles) == ["ath-second-profile"]

    invalid = {
        **body,
        "double_threshold_enabled": True,
        "double_threshold_day": 0,
    }
    rejected = client.put(
        "/api/v2/athlete/planning-profile", headers=headers, json=invalid
    )
    assert rejected.status_code == 422
    assert repository.profiles["ath-second-profile"].to_payload() == body


def test_login_ticket_is_consumed_through_the_protected_server_boundary(monkeypatch):
    class Repository:
        def __init__(self):
            self.tickets = {"one-time-ticket-value-with-32-chars": "ath-profile"}

        def consume_login_ticket(self, ticket):
            return self.tickets.pop(ticket, None)

    repository = Repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "secret-value")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)
    client = TestClient(app)
    body = {"ticket": "one-time-ticket-value-with-32-chars"}

    first = client.post(
        "/api/v2/session/exchange",
        headers={"Authorization": "Bearer secret-value"},
        json=body,
    )
    replay = client.post(
        "/api/v2/session/exchange",
        headers={"Authorization": "Bearer secret-value"},
        json=body,
    )

    assert first.status_code == 200
    assert first.json() == {"athlete_alias": "ath-profile"}
    assert replay.status_code == 401


def test_oauth_callback_exposes_only_safe_failure_stage(monkeypatch):
    monkeypatch.setenv("INTERVALS_CLIENT_ID", "client-id")
    monkeypatch.setenv("INTERVALS_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv(
        "INTERVALS_REDIRECT_URI",
        "https://api.example.test/api/v2/integrations/intervals/callback",
    )
    monkeypatch.setenv("OAUTH_STATE_SECRET", "state-secret")
    monkeypatch.setenv("ONFLOWS_WEB_BASE_URL", "https://web.example.test")
    monkeypatch.setattr(api_main, "_repository", lambda: object())

    def fail_safely(*_args, **_kwargs):
        raise OAuthFlowError("provider detail remains private", stage="permissions")

    monkeypatch.setattr(api_main, "complete_authorization", fail_safely)
    response = TestClient(app).get(
        "/api/v2/integrations/intervals/callback?code=private&state=private",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "https://web.example.test/?intervals=error-permissions"
    )
    assert "provider detail" not in response.headers["location"]
