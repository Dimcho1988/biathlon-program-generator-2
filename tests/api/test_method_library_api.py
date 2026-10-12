"""Method preferences retain athlete scope, immutable sources and profile CAS."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from apps.api import dependencies, main, method_library_service as service
from apps.api.management_schemas import ManagementProfile, MethodLibraryPreferences
from apps.api.oauth_store import PersistentStoreFailure
from apps.api.routes import management


ACTOR = UUID("11111111-1111-4111-8111-111111111111")
HEADERS = {"Authorization": "Bearer service-secret", "X-OnFlows-Athlete-Alias": "ath-test",
           "X-OnFlows-Actor-Id": str(ACTOR)}
PROFILE = {
    "sport": "NordicSki", "actual_sport": "RollerSki", "discipline": "10 km",
    "program_start": "2026-09-01", "program_end": "2027-06-01",
    "available_minutes": [45, 60, 0, 60, 45, 90, 0], "reentry_days": 0,
}
CATALOG = [
    {"id": "TEST-CV", "title": "CV method", "source": {"title": "Publication"},
     "prescription": {"anchor": {"type": "CV"}, "speed_multiplier": [1., 1.05],
                      "work": {"unit": "seconds", "range": [30, 60]}, "repetitions": [4, 8]}},
    {"id": "TEST-REFERENCE", "title": "Power-only reference", "prescription": None},
]


class Store:
    def __init__(self):
        self.current = {"configured": True, "profile": deepcopy(PROFILE), "revision": 4}
        self.reads, self.saved = [], []
        self.conflict = False

    def profile(self, alias):
        self.reads.append(alias)
        return deepcopy(self.current)

    def save_profile(self, alias, payload, expected_revision, actor):
        if self.conflict or expected_revision != self.current["revision"]:
            raise HTTPException(409, "Planning input changed; reload before saving")
        self.saved.append((alias, deepcopy(payload), expected_revision, actor))
        return {"revision": expected_revision + 1}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    store = Store()
    repository = SimpleNamespace(athlete_settings=lambda alias: SimpleNamespace(timezone="Europe/Sofia"))
    monkeypatch.setattr(dependencies, "repository", lambda: repository)
    monkeypatch.setattr(service, "ManagementStore", lambda repository: store)
    monkeypatch.setattr(management, "ManagementStore", lambda repository: store)
    monkeypatch.setattr(service.method_library, "entries", lambda: deepcopy(CATALOG))
    calls = []

    def speed_view(repo, alias, sport):
        calls.append(("speed", alias, sport))
        return {"status": "CALIBRATED", "sport": sport, "source_revision": 9, "model_version": "v1"}

    def catalog(profile, speed, *, phase, today):
        calls.append(("catalog", deepcopy(profile), deepcopy(speed), phase, today))
        return {"version": "test-v1", "methods": [{**deepcopy(entry), "resolution": {
            "status": "REFERENCE_ONLY" if not entry["prescription"] else "UNAVAILABLE",
            "reason": "TEST_ONLY",
        }} for entry in CATALOG]}

    monkeypatch.setattr(service.model_service, "speed_view", speed_view)
    monkeypatch.setattr(service.method_library, "catalog", catalog)
    return TestClient(main.app), store, repository, calls


def test_catalog_requires_auth_and_explicit_athlete_before_any_reads(api):
    client, store, _, calls = api
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer service-secret"}):
        assert client.get("/api/v2/athlete/management/methods", headers=headers).status_code == 401
    assert not store.reads and not calls


def test_catalog_is_read_only_and_uses_actual_sport_once(api):
    client, store, _, calls = api
    before = deepcopy(store.current)
    response = client.get("/api/v2/athlete/management/methods?phase=SPECIAL_PREPARATION",
                          headers={key: value for key, value in HEADERS.items() if key != "X-OnFlows-Actor-Id"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["revision"] == 4 and payload["preferences"] == {"enabled": True, "overrides": {}}
    assert payload["phase"] == "SPECIAL_PREPARATION" and payload["phase_source"] == "SELECTED_PREVIEW"
    assert payload["curve"]["sport"] == "RollerSki" and payload["curve"]["source_revision"] == 9
    assert payload["methods"][0]["source"] == CATALOG[0]["source"]
    assert [call for call in calls if call[0] == "speed"] == [("speed", "ath-test", "RollerSki")]
    assert store.current == before and not store.saved


def test_catalog_unconfigured_is_browsable_without_inventing_curve_or_profile(api):
    client, store, _, calls = api
    store.current = {"configured": False, "profile": None, "revision": 0}
    response = client.get("/api/v2/athlete/management/methods", headers=HEADERS)
    assert response.status_code == 200
    payload = response.json()
    assert not payload["configured"] and payload["revision"] == 0
    assert payload["curve"]["status"] == "UNAVAILABLE" and payload["curve"]["reason"] == "PROFILE_REQUIRED"
    assert not any(call[0] == "speed" for call in calls) and not store.saved


def test_catalog_missing_settings_preserves_references_and_reason(api):
    client, _, repository, calls = api
    repository.athlete_settings = lambda alias: None
    response = client.get("/api/v2/athlete/management/methods?phase=COMPETITION", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["curve"]["reason"] == "ATHLETE_SETTINGS_REQUIRED"
    assert len(response.json()["methods"]) == 2 and not any(call[0] == "speed" for call in calls)


def test_phase_preview_uses_calendar_without_refreshing_plan(api):
    _, store, repository, _ = api
    repository.athlete_planning_calendar = lambda alias: {"events": [{
        "event_id": "race", "event_type": "MAIN_RACE", "name": "10 km",
        "start_date": "2026-10-12", "end_date": "2026-10-12",
    }]}
    result = service.view(repository, "ath-test", now=datetime(2026, 10, 12, 9, tzinfo=timezone.utc))
    assert result["phase"] == "COMPETITION" and result["phase_source"] == "CALENDAR_PREVIEW"
    assert not store.saved


def test_save_preferences_requires_actor_and_scopes_write_to_current_athlete(api):
    client, store, _, _ = api
    body = {"expected_revision": 4, "preferences": {"enabled": False, "overrides": {}}}
    no_actor = {key: value for key, value in HEADERS.items() if key != "X-OnFlows-Actor-Id"}
    assert client.put("/api/v2/athlete/management/methods/preferences", json=body, headers=no_actor).status_code == 401
    assert not store.saved
    other = {**HEADERS, "X-OnFlows-Athlete-Alias": "ath-other"}
    response = client.put("/api/v2/athlete/management/methods/preferences", json=body, headers=other)
    assert response.status_code == 200 and response.json()["revision"] == 5
    assert store.saved[0][0] == "ath-other" and store.saved[0][3] == ACTOR


def test_preference_save_preserves_every_other_profile_field(api):
    client, store, _, _ = api
    store.current["profile"]["future_field"] = {"nested": [1, 2, 3]}
    store.current["profile"]["planning_controls"] = {"sessions_per_week": 13}
    before = deepcopy(store.current)
    preferences = {"enabled": True, "overrides": {"TEST-CV": {
        "enabled": True, "speed_multiplier": [1.02, 1.08], "work_range": [40, 90], "repetitions": [3, 7],
    }}}
    response = client.put("/api/v2/athlete/management/methods/preferences",
                          json={"expected_revision": 4, "preferences": preferences}, headers=HEADERS)
    assert response.status_code == 200
    expected = {**before["profile"], "method_library": preferences}
    assert store.saved == [("ath-test", expected, 4, ACTOR)]
    assert store.current == before


@pytest.mark.parametrize("override", [
    {"speed_multiplier": [1.1, 1.]}, {"speed_multiplier": [0., 1.]},
    {"speed_multiplier": [True, 1.]}, {"speed_multiplier": ["1", 1.1]},
    {"speed_multiplier": [1., 3.1]}, {"work_range": [0, 10]}, {"work_range": [60, 30]},
    {"work_range": [1, 100001]}, {"repetitions": [1, 101]}, {"repetitions": [4, 2]},
    {"repetitions": [1.5, 3]}, {"enabled": "false"}, {"anchor": {"type": "V5"}},
    {"id": "TEST-REFERENCE"}, {"source": "replacement"},
])
def test_invalid_or_immutable_overrides_are_rejected_without_writes(api, override):
    client, store, _, _ = api
    body = {"expected_revision": 4, "preferences": {"overrides": {"TEST-CV": override}}}
    assert client.put("/api/v2/athlete/management/methods/preferences", json=body, headers=HEADERS).status_code == 422
    assert not store.saved


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_ranges_rejected_at_schema(value):
    with pytest.raises(ValidationError):
        MethodLibraryPreferences.model_validate({"overrides": {"TEST-CV": {"speed_multiplier": [value, 2.]}}})


@pytest.mark.parametrize("key,override", [
    ("UNKNOWN", {"enabled": True}), ("TEST-REFERENCE", {"speed_multiplier": [1., 1.1]}),
    ("TEST-REFERENCE", {"work_range": [30, 60]}), ("TEST-REFERENCE", {"repetitions": [4, 8]}),
])
def test_unknown_method_and_reference_numeric_override_rejected(api, key, override):
    client, store, _, _ = api
    body = {"expected_revision": 4, "preferences": {"overrides": {key: override}}}
    assert client.put("/api/v2/athlete/management/methods/preferences", json=body, headers=HEADERS).status_code == 422
    assert not store.saved


def test_reference_toggle_does_not_create_executable_recipe(api):
    client, store, _, _ = api
    body = {"expected_revision": 4, "preferences": {"overrides": {"TEST-REFERENCE": {"enabled": True}}}}
    response = client.put("/api/v2/athlete/management/methods/preferences", json=body, headers=HEADERS)
    assert response.status_code == 200
    assert store.saved[0][1]["method_library"]["overrides"] == {"TEST-REFERENCE": {"enabled": True}}
    assert CATALOG[1]["prescription"] is None


@pytest.mark.parametrize("override", [{"work_range": [30, 60]}, {"speed_multiplier": [1., 1.1]}])
def test_compound_ranges_require_segment_prescription(api, monkeypatch, override):
    client, store, _, _ = api
    entries = deepcopy(CATALOG)
    entries[0]["prescription"]["pattern"] = [{"speed_multiplier": [.95, 1.],
                                              "work": {"unit": "seconds", "range": [30, 30]}}]
    monkeypatch.setattr(service.method_library, "entries", lambda: entries)
    response = client.put("/api/v2/athlete/management/methods/preferences", headers=HEADERS,
                          json={"expected_revision": 4, "preferences": {"overrides": {"TEST-CV": override}}})
    assert response.status_code == 422 and not store.saved


def test_stale_revision_and_concurrent_store_conflict_never_overwrite(api):
    client, store, _, _ = api
    body = {"expected_revision": 3, "preferences": {}}
    assert client.put("/api/v2/athlete/management/methods/preferences", json=body, headers=HEADERS).status_code == 409
    body["expected_revision"] = 4
    store.conflict = True
    assert client.put("/api/v2/athlete/management/methods/preferences", json=body, headers=HEADERS).status_code == 409
    assert not store.saved


def test_preferences_require_existing_profile(api):
    client, store, _, _ = api
    store.current = {"configured": False, "profile": None, "revision": 0}
    body = {"expected_revision": 1, "preferences": {}}
    assert client.put("/api/v2/athlete/management/methods/preferences", json=body, headers=HEADERS).status_code == 409
    assert not store.saved


def test_existing_profile_write_preserves_preferences_when_legacy_editor_omits_them(api):
    client, store, _, _ = api
    preferences = {"enabled": False, "overrides": {"TEST-CV": {"enabled": False}}}
    store.current["profile"]["method_library"] = deepcopy(preferences)
    response = client.put("/api/v2/athlete/management/profile",
                          json={"expected_revision": 4, "profile": PROFILE}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["profile"]["method_library"] == preferences
    assert store.saved[0][1]["method_library"] == preferences


def test_full_profile_write_cannot_bypass_catalog_validation(api):
    client, store, _, _ = api
    profile = {**PROFILE, "method_library": {"overrides": {"UNKNOWN": {"enabled": True}}}}
    response = client.put("/api/v2/athlete/management/profile",
                          json={"expected_revision": 4, "profile": profile}, headers=HEADERS)
    assert response.status_code == 422 and not store.saved


def test_profile_schema_keeps_method_library_roundtrip():
    profile = {**PROFILE, "method_library": {"enabled": True, "overrides": {
        "TEST-CV": {"work_range": [30, 60], "speed_multiplier": [1., 1.1]},
    }}}
    parsed = ManagementProfile.model_validate(profile)
    assert parsed.model_dump(mode="json")["method_library"]["overrides"]["TEST-CV"] == {
        "work_range": [30., 60.], "speed_multiplier": [1., 1.1],
    }
    restored = ManagementProfile.model_validate(parsed.model_dump(mode="json"))
    assert restored.method_library == parsed.method_library


def test_storage_failure_is_safe_503(api, monkeypatch):
    client, store, _, _ = api
    def failure(alias):
        raise PersistentStoreFailure("private storage context")
    monkeypatch.setattr(store, "profile", failure)
    response = client.get("/api/v2/athlete/management/methods", headers=HEADERS)
    assert response.status_code == 503 and "private" not in response.text
