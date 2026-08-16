from datetime import datetime, timezone
import math
from fastapi.testclient import TestClient
import pytest

from apps.api.cloud import AthleteContext, AthleteModelSettings, InMemorySnapshotRepository, normalize_wellness, service_token_valid
from apps.api.hrmod import calculate_hrmod
from apps.api import main as api_main
from apps.api.main import app


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
    assert client.get("/api/v2/real/load-history", headers={"Authorization": "Bearer secret-value"}).status_code == 503
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
