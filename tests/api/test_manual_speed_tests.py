from apps.api import dependencies
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.api import main, model_service as service
from apps.api.model_schemas import ManualSpeedTestInput, SpeedTestInput

NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
ACTOR = UUID("11111111-1111-4111-8111-111111111111")
TEST_ID = UUID("22222222-2222-4222-8222-222222222222")
REF = "act_" + "a" * 32


class Repository:
    def __init__(self):
        self.rows = []
        self.writes = []

    def athlete_settings(self, alias):
        assert alias == "ath-test"
        return SimpleNamespace(timezone="Europe/Sofia", hrmax_bpm=190,
                               zone_bounds_bpm=(100, 125, 145, 160, 175, 190))

    def _request(self, method, path, **kwargs):
        if method == "GET":
            assert "athlete_alias=eq.ath-test" in path
            return sorted(self.rows, key=lambda r: -r["revision"])
        assert path == "/rpc/save_onflows_model_entry"
        body = kwargs["json"]
        assert body["p_alias"] == "ath-test" and body["p_actor"] == str(ACTOR)
        previous = [r for r in self.rows if r["entry_key"] == body["p_key"]]
        revision = max((r["revision"] for r in previous), default=0)
        if revision != body["p_expected_revision"]:
            return {"conflict": True, "revision": revision}
        self.writes.append(deepcopy(body))
        self.rows.append({"kind": body["p_kind"], "entry_key": body["p_key"],
                          "revision": revision + 1, "payload": deepcopy(body["p_payload"]),
                          "recorded_at": NOW.isoformat()})
        return {"saved": True, "revision": revision + 1}

    def _json(self, value):
        return value

    def active_activity_calendar(self, alias, start, end):
        return {"activities": [], "snapshot_payload": {}}

    def active_activity_view(self, alias, ref):
        assert alias == "ath-test" and ref == REF
        return {"catalog_payload": {"sport": "Run", "local_date": NOW.date().isoformat()},
                "shadow_run_key": "a" * 64, "shadow_payload": {
                    "vflat_model_version": "v5", "vflat_config_version": "config-v5",
                    "timeseries": [{"elapsed_s": t, "vflat_b65_kmh": 20., "grade_smoothed_pct": 0.}
                                   for t in range(1000)]}}


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(service, "datetime", FixedDatetime)


def body(**changes):
    return ManualSpeedTestInput(**{
        "test_id": TEST_ID, "name": "Track test", "day": NOW.date(), "sport": "Run",
        "duration_s": 180., "distance_m": 1200., "maximal": True, "comparable": True,
        "flat_terrain": True, "conditions": "Flat track; same shoes", **changes})


def test_manual_result_calibrates_without_imported_activity_or_shadow():
    repo = Repository()
    result = service.save_manual_test(repo, "ath-test", body(), ACTOR)
    payload = repo.writes[-1]["p_payload"]
    assert payload["speed_kmh"] == pytest.approx(24)
    assert payload["source"] == "MANUAL" and payload["coverage_percent"] is None
    assert payload["distance_basis"] == "MEASURED_FLAT"
    assert "activity_ref" not in payload and "vflat_version" not in payload
    model = service.speed_view(repo, "ath-test", "Run", duration_s=180)
    assert model["status"] == "CALIBRATED" and model["active_test_keys"] == [result["entry_key"]]
    assert model["prediction"]["speed_kmh"] == pytest.approx(24)
    assert model["test_window"]["end"] == "2026-09-19"
    assert service.speed_view(repo, "ath-test", "NordicSki")["active_test_count"] == 0


def test_fractional_sprint_and_direct_speed_preserve_measurement():
    repo = Repository()
    service.save_manual_test(repo, "ath-test", body(duration_s=10.8, distance_m=None, speed_kmh=33.6), ACTOR)
    payload = repo.writes[-1]["p_payload"]
    assert payload["duration_s"] == 10.8 and payload["measurement_input"] == "SPEED"
    assert payload["distance_m"] == pytest.approx(100.8)
    assert service.speed_view(repo, "ath-test", "Run", duration_s=10.8)["prediction"]["speed_kmh"] == pytest.approx(33.6)


def test_edit_disable_and_reenable_use_same_identity_and_revision():
    repo = Repository()
    service.save_manual_test(repo, "ath-test", body(), ACTOR)
    service.save_manual_test(repo, "ath-test", body(duration_s=200, expected_revision=1), ACTOR)
    assert service.speed_view(repo, "ath-test", "Run", duration_s=200)["prediction"]["speed_kmh"] == pytest.approx(21.6)
    service.save_manual_test(repo, "ath-test", body(enabled=False, expected_revision=2), ACTOR)
    assert repo.writes[-1]["p_payload"]["duration_s"] == 200  # disabling preserves the saved result
    assert service.speed_view(repo, "ath-test", "Run")["status"] == "REFERENCE_ONLY"
    service.save_manual_test(repo, "ath-test", body(expected_revision=3), ACTOR)
    assert service.speed_view(repo, "ath-test", "Run")["active_test_count"] == 1
    with pytest.raises(HTTPException) as error:
        service.save_manual_test(repo, "ath-test", body(expected_revision=1), ACTOR)
    assert error.value.status_code == 409 and len(repo.writes) == 4


def test_manual_and_vflat_points_share_curve_and_validate_conflicts_in_both_directions():
    repo = Repository()
    service.save_manual_test(repo, "ath-test", body(), ACTOR)
    imported = SpeedTestInput(activity_ref=REF, start_s=0, duration_s=720, maximal=True,
                              comparable=True, conditions="Same sport and equipment")
    service.save_test(repo, "ath-test", imported, ACTOR)
    model = service.speed_view(repo, "ath-test", "Run", duration_s=720)
    assert model["active_test_count"] == 2 and "INCOMPARABLE_MODEL_VERSIONS" not in model["warnings"]
    assert model["prediction"]["speed_kmh"] == pytest.approx(20)
    for update in [body(duration_s=900, distance_m=None, speed_kmh=22, expected_revision=1),
                   body(duration_s=720, expected_revision=1)]:
        with pytest.raises(HTTPException) as error:
            service.save_manual_test(repo, "ath-test", update, ACTOR)
        assert error.value.status_code == 422
    repo = Repository()
    service.save_manual_test(repo, "ath-test", body(distance_m=800), ACTOR)
    with pytest.raises(HTTPException) as error:
        service.save_test(repo, "ath-test", imported, ACTOR)
    assert error.value.status_code == 422 and len(repo.writes) == 1


def test_manual_critical_speed_and_expired_disabling():
    repo = Repository()
    service.save_manual_test(repo, "ath-test", body(duration_s=180, distance_m=1000, use_for_cs=True), ACTOR)
    other = UUID("33333333-3333-4333-8333-333333333333")
    service.save_manual_test(repo, "ath-test", body(test_id=other, duration_s=720, distance_m=3700, use_for_cs=True), ACTOR)
    model = service.speed_view(repo, "ath-test", "Run")
    assert model["critical_speed"]["count"] == 2
    assert model["critical_speed"]["speed_kmh"] == pytest.approx(18)
    repo.rows[0]["payload"]["day"] = "2025-01-01"
    service.save_manual_test(repo, "ath-test", body(day="2025-01-01", enabled=False, expected_revision=1), ACTOR)
    assert not repo.writes[-1]["p_payload"]["enabled"]


@pytest.mark.parametrize("offset", [-91, -90, 1])
def test_manual_test_date_must_be_in_recent_window(offset):
    repo = Repository()
    with pytest.raises(HTTPException) as error:
        service.save_manual_test(repo, "ath-test", body(day=NOW.date()+timedelta(days=offset)), ACTOR)
    assert error.value.status_code == 422 and not repo.writes


@pytest.mark.parametrize("changes", [
    {"distance_m": None}, {"speed_kmh": 20}, {"distance_m": 0}, {"distance_m": float("nan")},
    {"duration_s": 10.7}, {"duration_s": 43517}, {"duration_s": True},
    {"maximal": False}, {"comparable": False}, {"flat_terrain": False},
    {"name": "  "}, {"conditions": "  "}, {"duration_s": 30, "use_for_cs": True},
    {"distance_m": None, "speed_kmh": 151}, {"activity_ref": REF},
])
def test_manual_measurements_and_attestations_are_validated(changes):
    with pytest.raises(ValueError):
        body(**changes)


def test_api_requires_authenticated_profile_and_actor(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "test-service-token")
    repo = Repository()
    monkeypatch.setattr(dependencies, "repository", lambda: repo)
    client = TestClient(main.app)
    url = "/api/v2/athlete/models/speed-test-manual"
    payload = body().model_dump(mode="json")
    headers = {"Authorization": "Bearer test-service-token", "X-OnFlows-Athlete-Alias": "ath-test",
               "X-OnFlows-Actor-Id": str(ACTOR)}
    assert client.put(url, json=payload).status_code == 401
    for missing in ["X-OnFlows-Actor-Id", "X-OnFlows-Athlete-Alias"]:
        assert client.put(url, json=payload, headers={k: v for k, v in headers.items() if k != missing}).status_code == 401
    assert not repo.writes
    assert client.put(url, json={**payload, "flat_terrain": False}, headers=headers).status_code == 422
    assert client.put(url, json=payload, headers=headers).status_code == 200
    assert len(repo.writes) == 1
