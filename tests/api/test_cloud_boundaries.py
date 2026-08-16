from datetime import datetime, timezone
import math
from fastapi.testclient import TestClient

from apps.api.cloud import AthleteContext, InMemorySnapshotRepository, normalize_wellness, service_token_valid
from apps.api.hrmod import calculate_hrmod
from apps.api.main import app


def test_athlete_configuration_validation_and_private_fingerprint():
    context = AthleteContext("pilot", "private-123", (90, 110, 130, 150, 170, 190), "Europe/Sofia", "intra_zone_linear_v1", "tref-300-180-70-20-20-v1", "recovery-v1")
    assert len(context.validate().fingerprint) == 64
    assert "private-123" not in context.fingerprint


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


def test_constant_time_boundary_semantics():
    assert service_token_valid("same", "same")
    assert not service_token_valid(None, "same")
    assert not service_token_valid("same", "")
