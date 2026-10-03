"""Expert HRmax fallback is explicit, scoped and cannot overwrite manual zones."""
import base64
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api import dependencies
from apps.api.cloud import AthleteModelSettings
from apps.api.main import app
from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from biathlon.hr_zones import bounds_from_hrmax

# Synthetic percentages for contract testing, not a recommended expert scheme.
PERCENTAGES = (50, 60, 70, 80, 90, 100)
BOUNDS = (100, 120, 140, 160, 180, 200)
PATH = "/api/v2/athlete/settings"


@pytest.fixture
def settings_client(monkeypatch):
    items = {}
    repository = SimpleNamespace(
        connection=lambda _: SimpleNamespace(status="CONNECTED"),
        athlete_settings=lambda alias: items.get(alias),
        save_athlete_settings=lambda alias, settings: items.__setitem__(alias, settings),
    )
    monkeypatch.delenv("ONFLOWS_HRMAX_ZONE_PERCENTAGES", raising=False)
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "test-token")
    monkeypatch.setattr(dependencies, "repository", lambda: repository)
    client = TestClient(app)
    client.headers.update({"Authorization": "Bearer test-token", "X-OnFlows-Athlete-Alias": "ath-test"})
    yield client, items


def test_missing_scheme_does_not_invent_zones(settings_client):
    client, items = settings_client
    assert client.get(PATH).json()["automatic_hr_zone_percentages"] is None
    response = client.put(PATH, json={"timezone": "Europe/Sofia", "hrmax_bpm": 200})
    assert response.status_code == 422
    assert not items


@pytest.mark.parametrize("invalid", ["50,60,70,80,90", "50,60,60,80,90,100", "50,60,70,80,90,nan", "0,60,70,80,90,100", "0.5,0.6,0.7,0.8,0.9,1"])
def test_invalid_configuration_never_enables_automatic_mode(settings_client, monkeypatch, invalid):
    client, _ = settings_client
    monkeypatch.setenv("ONFLOWS_HRMAX_ZONE_PERCENTAGES", invalid)
    assert client.get(PATH).json()["automatic_hr_zone_percentages"] is None
    assert client.put(PATH, json={"timezone": "UTC", "hrmax_bpm": 200}).status_code == 422


def test_hrmax_only_creates_estimated_zones_with_saved_provenance(settings_client, monkeypatch):
    client, items = settings_client
    monkeypatch.setenv("ONFLOWS_HRMAX_ZONE_PERCENTAGES", ",".join(map(str, PERCENTAGES)))
    assert client.get(PATH).json()["automatic_hr_zone_percentages"] == list(PERCENTAGES)
    response = client.put(PATH, json={"timezone": "Europe/Sofia", "hrmax_bpm": 200})
    assert response.status_code == 200
    result = response.json()
    assert result["hr_zone_bounds_bpm"] == list(BOUNDS)
    assert result["hr_zone_source"] == "AUTOMATIC_HRMAX"
    assert result["hr_zone_percentages"] == list(PERCENTAGES)
    assert list(items) == ["ath-test"]
    assert client.get(PATH).json() == result

    # A deployment changing/removing its default cannot silently change this athlete's scheme.
    monkeypatch.delenv("ONFLOWS_HRMAX_ZONE_PERCENTAGES")
    updated = client.put(PATH, json={"timezone": "Europe/Sofia", "hrmax_bpm": 190}).json()
    assert updated["hr_zone_bounds_bpm"] == [95, 114, 133, 152, 171, 190]
    assert updated["hr_zone_percentages"] == list(PERCENTAGES)
    assert updated["automatic_hr_zone_percentages"] is None


def test_manual_boundaries_survive_hrmax_only_update_and_active_default(settings_client, monkeypatch):
    client, items = settings_client
    manual = (91, 123, 144, 166, 179, 199)
    items["ath-test"] = AthleteModelSettings(manual, "UTC", 200)
    monkeypatch.setenv("ONFLOWS_HRMAX_ZONE_PERCENTAGES", ",".join(map(str, PERCENTAGES)))
    result = client.put(PATH, json={"timezone": "UTC", "hrmax_bpm": 205}).json()
    assert result["hr_zone_bounds_bpm"] == list(manual)
    assert result["hr_zone_source"] == "MANUAL"
    assert result["hr_zone_percentages"] is None
    # Changing source must be deliberate.
    result = client.put(PATH, json={"timezone": "UTC", "hrmax_bpm": 200, "hr_zone_source": "AUTOMATIC_HRMAX"}).json()
    assert result["hr_zone_bounds_bpm"] == list(BOUNDS)
    assert result["hr_zone_source"] == "AUTOMATIC_HRMAX"


def test_explicit_manual_edit_replaces_automatic_provenance(settings_client):
    client, items = settings_client
    items["ath-test"] = AthleteModelSettings(BOUNDS, "UTC", 200, "AUTOMATIC_HRMAX", PERCENTAGES)
    changed = (99, 119, 139, 159, 179, 199)
    result = client.put(PATH, json={"timezone": "UTC", "hrmax_bpm": 200, "hr_zone_bounds_bpm": changed}).json()
    assert result["hr_zone_bounds_bpm"] == list(changed)
    assert result["hr_zone_source"] == "MANUAL"
    assert result["hr_zone_percentages"] is None


@pytest.mark.parametrize("hrmax", [None, 0, 241])
def test_automatic_mode_requires_known_valid_hrmax(settings_client, monkeypatch, hrmax):
    client, items = settings_client
    monkeypatch.setenv("ONFLOWS_HRMAX_ZONE_PERCENTAGES", ",".join(map(str, PERCENTAGES)))
    assert client.put(PATH, json={"timezone": "UTC", "hrmax_bpm": hrmax}).status_code == 422
    assert not items


def test_rounding_and_unusable_rounded_scheme():
    assert bounds_from_hrmax(195, PERCENTAGES) == (98, 117, 137, 156, 176, 195)
    with pytest.raises(ValueError, match="increasing boundaries"):
        bounds_from_hrmax(200, (97.5, 97.6, 97.7, 97.8, 97.9, 100))


def test_repository_roundtrips_provenance_and_rejects_inconsistent_estimates():
    saved = {}

    def transport(request):
        import json
        if request.method == "POST":
            saved.update(json.loads(request.content))
            return httpx.Response(204)
        return httpx.Response(200, json=[saved])

    repository = SupabasePilotRepository(
        supabase_url="https://project.supabase.co", secret_key="sb_secret_test",
        encryption_key=base64.urlsafe_b64encode(bytes(range(32))).decode(),
        client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    settings = AthleteModelSettings(BOUNDS, "Europe/Sofia", 200, "AUTOMATIC_HRMAX", PERCENTAGES)
    repository.save_athlete_settings("ath-test", settings)
    assert saved["hr_zone_source"] == "AUTOMATIC_HRMAX"
    assert saved["hr_zone_percentages"] == list(PERCENTAGES)
    assert repository.athlete_settings("ath-test") == settings
    saved["hr_zone_bounds"][0] = 99
    with pytest.raises(PersistentStoreFailure, match="invalid"):
        repository.athlete_settings("ath-test")
