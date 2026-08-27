from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api import main as api_main
from apps.api.main import app


ACTIVITY_REF = "act_" + "1" * 32
AUTH = {
    "Authorization": "Bearer service-secret",
    "X-OnFlows-Athlete-Alias": "ath-activity-view",
}


def _catalog() -> dict:
    return {
        "activity_ref": ACTIVITY_REF,
        "start_at_utc": "2026-08-26T05:00:00+00:00",
        "start_local": "2026-08-26T08:00:00",
        "local_date": "2026-08-26",
        "timezone": "Europe/Sofia",
        "utc_offset_minutes": 180,
        "sport": "Run",
        "activity_type": "Run",
        "activity_sub_type": None,
        "name": "Pinned threshold",
        "description": "Private note",
        "moving_time_s": 600,
        "elapsed_time_s": 620,
        "recording_time_s": 610,
        "distance_m": 2400.0,
        "elevation_gain_m": 12.0,
        "average_hr_bpm": 151.0,
        "max_hr_bpm": 171.0,
        "average_speed_mps": 4.0,
        "max_speed_mps": 5.0,
        "canonical_training_load": 14.0,
        "quality_status": "valid",
        "quality_reason": None,
        "hr_coverage_percent": 100.0,
        "canonical_summary": {
            "duration_min": 10.0,
            "zones": [
                {
                    "zone": "Z3",
                    "raw_time_s": 600.0,
                    "equivalent_time_s": 480.0,
                    "effective_load": 14.0,
                }
            ],
        },
        "intervals": [],
    }


def _active_row() -> dict:
    return {
        "generation_id": "11111111-1111-4111-8111-111111111111",
        "revision": 7,
        "analysis_as_of": "2026-08-26",
        "activated_at": "2026-08-26T18:00:00+00:00",
        "catalog_payload": _catalog(),
        "input_key": "a" * 64,
        "canonical_run_key": "b" * 64,
        "shadow_run_key": "c" * 64,
        "previous_activity_ref": "act_" + "2" * 32,
        "next_activity_ref": "act_" + "3" * 32,
        "series_payload": {
            "samples": [
                {
                    "timestamp": "2026-08-26T05:00:00+00:00",
                    "elapsed_s": 0.0,
                    "hr_raw_bpm": 151.0,
                    "speed_raw_kmh": 14.4,
                    "altitude_m": 600.0,
                    "grade_raw_pct": 1.0,
                    "quality_flags": [],
                }
            ]
        },
        "shadow_payload": {
            "schema_version": "activity-shadow-derived-v2",
            "experimental": True,
            "zone_summary": [
                {"zone_name": "Z3", "hrmod_final_seconds": 590.0}
            ],
        },
    }


def test_activity_view_is_one_generation_pinned_repository_read(monkeypatch):
    class Repository:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        def active_activity_view(self, athlete_alias, activity_ref):
            self.calls.append((athlete_alias, activity_ref))
            return _active_row()

    repository = Repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)

    response = TestClient(app).get(
        f"/api/v2/real/activities/{ACTIVITY_REF}/view", headers=AUTH
    )

    assert response.status_code == 200
    assert repository.calls == [("ath-activity-view", ACTIVITY_REF)]
    payload = response.json()
    assert payload["schema_version"] == "activity-view-v1"
    assert payload["generation_id"] == "11111111-1111-4111-8111-111111111111"
    assert payload["revision"] == 7
    assert payload["activity"]["activity_ref"] == ACTIVITY_REF
    assert payload["activity"]["previous_activity_ref"] == "act_" + "2" * 32
    assert payload["activity"]["hrmod_zones"] == [
        {"zone": "Z3", "final_time_s": 590.0}
    ]
    assert payload["series"]["activity_ref"] == ACTIVITY_REF
    assert payload["series"]["series"][0]["hr_bpm"] == 151.0
    assert payload["shadow"]["schema_version"] == "activity-shadow-derived-v2"
    assert "input_key" not in payload
    assert "shadow_run_key" not in payload


def test_activity_view_retains_revision_zero_rollout_compatibility(monkeypatch):
    class Repository:
        def active_activity_view(self, athlete_alias, activity_ref):
            return {
                **_active_row(),
                "generation_id": None,
                "revision": 0,
                "analysis_as_of": None,
                "activated_at": None,
                "input_key": None,
                "shadow_run_key": None,
                "series_payload": None,
                "shadow_payload": None,
            }

    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(api_main, "_repository", lambda: Repository())

    response = TestClient(app).get(
        f"/api/v2/real/activities/{ACTIVITY_REF}/view", headers=AUTH
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["generation_id"] is None
    assert payload["revision"] == 0
    assert payload["series"] is None
    assert payload["shadow"] is None
    assert payload["activity"]["shadow_available"] is False


def test_activity_view_fails_closed_when_a_pointer_payload_is_missing(monkeypatch):
    class Repository:
        def active_activity_view(self, athlete_alias, activity_ref):
            return {**_active_row(), "series_payload": None}

    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(api_main, "_repository", lambda: Repository())

    response = TestClient(app).get(
        f"/api/v2/real/activities/{ACTIVITY_REF}/view", headers=AUTH
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Persistent server storage is unavailable"
