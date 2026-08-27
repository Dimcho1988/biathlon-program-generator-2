from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from apps.api import main as api_main
from apps.api.main import app
from apps.api.training_status import build_demo_training_status


AUTH = {
    "Authorization": "Bearer service-secret",
    "X-OnFlows-Athlete-Alias": "ath-sync-test",
}


def _active_snapshot() -> dict:
    training_status = build_demo_training_status().model_dump(mode="json")
    training_status["athlete_id"] = "ath-sync-test"
    load_history = {
        "schema_version": "load-history-v2",
        "athlete_id": "ath-sync-test",
        "period_start": "2026-08-09",
        "period_end": "2026-08-09",
        "tref_bounds_profile_version": "tref-bounded-40d-expert-v1",
        "quality": {
            "processed_activities": 0,
            "limited_activities": 0,
            "excluded_activities": 0,
            "no_activity_days": 40,
            "warnings": [],
        },
        "zones": [
            {
                "zone": zone,
                "e7_daily": 0.0,
                "e40_daily": 0.0,
                "status_7_40": 1.0,
                "tref_min": tref,
                "history_reliability": 1.0,
            }
            for zone, tref in zip(
                ("Z1", "Z2", "Z3", "Z4", "Z5"),
                (300.0, 180.0, 70.0, 20.0, 20.0),
            )
        ],
        "daily": [
            {
                "date": "2026-08-09",
                "zone": zone,
                "effective_load": 0.0,
                "e7_daily": 0.0,
                "e40_daily": 0.0,
                "status_7_40": 1.0,
                "tref_used_min": tref,
            }
            for zone, tref in zip(
                ("Z1", "Z2", "Z3", "Z4", "Z5"),
                (300.0, 180.0, 70.0, 20.0, 20.0),
            )
        ],
        "activities": [],
        "strength": {
            "model": {
                "classification_version": "strength-v1",
                "source": "intervals-activity-type-duration",
                "duration_basis": "recording-time-first",
                "equivalent_time_coefficient": 1.0,
                "aerobic_hr_counted": False,
            },
            "summary": {
                "recorded_activities": 0,
                "real_time_7d_min": 0.0,
                "real_time_40d_min": 0.0,
                "e7_daily": 0.0,
                "e40_daily": 0.0,
                "status_7_40": 1.0,
                "tref_min": 56.0,
                "history_reliability": 1.0,
            },
            "daily": [{
                "date": "2026-08-09",
                "real_time_min": 0.0,
                "equivalent_time_min": 0.0,
                "effective_load": 0.0,
                "e7_daily": 0.0,
                "e40_daily": 0.0,
                "status_7_40": 1.0,
                "tref_used_min": 56.0,
            }],
        },
    }
    recovery = {
        "schema_version": "recovery-history-v1",
        "athlete_id": "ath-sync-test",
        "period_start": "2026-08-09",
        "period_end": "2026-08-09",
        "basis": "load-only",
        "wellness_freshness": "unknown",
        "wellness_coverage_percent": 0.0,
        "wellness_diagnostics": None,
        "model": {
            "algorithm_version": "recovery-v1",
            "parameter_version": "parameters-v1",
            "parameter_fingerprint": "0" * 64,
            "practical_full_recovery_percent": 99.0,
        },
        "settings": [],
        "current": [],
        "daily": [],
        "strength": None,
    }
    return {
        "schema_version": "athlete-snapshot-v1",
        "training_status": training_status,
        "load_history": load_history,
        "recovery_history": recovery,
        "wellness_calendar": [],
    }


def _legacy_snapshot() -> dict:
    snapshot = deepcopy(_active_snapshot())
    history = snapshot["load_history"]
    history["schema_version"] = "load-history-v1"
    history.pop("tref_bounds_profile_version")
    for row in history["daily"]:
        row.pop("tref_used_min")
    for row in history["strength"]["daily"]:
        row.pop("tref_used_min")
    return snapshot


def test_sync_job_endpoint_is_strict_and_only_enqueues(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 27, 22, 30, tzinfo=timezone.utc)

    class Repository:
        def __init__(self):
            self.calls = []

        def athlete_settings(self, athlete_alias):
            return {"timezone": "Europe/Sofia"}

        def enqueue_sync_job(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "job_id": "job-123",
                "status": "QUEUED",
                "deduplicated": False,
            }

    repository = Repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)
    monkeypatch.setattr(api_main, "datetime", FixedDateTime)

    response = TestClient(app).post(
        "/api/v2/real/sync-jobs",
        headers=AUTH,
        json={"scope": "FULL"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "schema_version": "sync-enqueue-v1",
        "job_id": "job-123",
        "scope": "FULL",
        "state": "QUEUED",
        "coalesced": False,
    }
    assert repository.calls[0]["athlete_alias"] == "ath-sync-test"
    assert repository.calls[0]["job_kind"] == "FULL_SYNC"
    assert repository.calls[0]["request_payload"] == {
        "schema_version": "sync-request-v1",
        "scope": "FULL",
        "as_of": "2026-08-28",
    }
    assert repository.calls[0]["request_payload"]["as_of"] == "2026-08-28"
    assert len(repository.calls[0]["idempotency_key"]) == 64

    invalid = TestClient(app).post(
        "/api/v2/real/sync-jobs",
        headers=AUTH,
        json={"scope": "FULL", "unexpected": True},
    )
    assert invalid.status_code == 422


def test_sync_status_maps_internal_worker_state_without_mutation(monkeypatch):
    class Repository:
        def sync_state(self, athlete_alias):
            assert athlete_alias == "ath-sync-test"
            return {
                "job_id": "job-123",
                "job_kind": "WELLNESS_SYNC",
                "status": "RETRY_WAIT",
                "progress_stage": "PROVIDER_WELLNESS",
                "progress_percent": 25,
                "requested_at": "2026-08-27T08:00:00+00:00",
                "started_at": "2026-08-27T08:00:01+00:00",
                "completed_at": None,
                "available_at": "2026-08-27T08:01:00+00:00",
                "error_code": "PROVIDER_TEMPORARY",
                "active_generation_id": "generation-7",
                "active_revision": 7,
                "active_as_of": "2026-08-26",
                "activated_at": "2026-08-26T22:00:00+00:00",
            }

    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(api_main, "_repository", lambda: Repository())

    response = TestClient(app).get("/api/v2/real/sync-status", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "sync-state-v1",
        "job_id": "job-123",
        "scope": "WELLNESS",
        "state": "RETRY_WAIT",
        "stage": "PROVIDER_WELLNESS",
        "progress_percent": 25.0,
        "requested_at": "2026-08-27T08:00:00+00:00",
        "started_at": "2026-08-27T08:00:01+00:00",
        "finished_at": None,
        "retry_at": "2026-08-27T08:01:00+00:00",
        "failure_code": "PROVIDER_TEMPORARY",
        "active_generation_id": "generation-7",
        "active_revision": 7,
        "analysis_as_of": "2026-08-26",
        "activated_at": "2026-08-26T22:00:00+00:00",
    }


def test_patch_enqueue_requires_an_activated_generation_base(monkeypatch):
    class Repository:
        def __init__(self, snapshot, generation_id):
            self.snapshot = snapshot
            self.generation_id = generation_id
            self.calls = []

        def active_analysis(self, athlete_alias):
            if self.snapshot is None:
                return None
            return {
                "generation_id": self.generation_id,
                "snapshot_payload": self.snapshot,
            }

        def athlete_settings(self, athlete_alias):
            return {"timezone": "UTC"}

        def enqueue_sync_job(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "job_id": f"job-{len(self.calls)}",
                "status": "QUEUED",
                "deduplicated": False,
            }

    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    client = TestClient(app)

    current = Repository(_active_snapshot(), "generation-current")
    monkeypatch.setattr(api_main, "_repository", lambda: current)
    recovery = client.post(
        "/api/v2/real/sync-jobs", headers=AUTH, json={"scope": "RECOVERY"}
    )
    assert recovery.status_code == 202
    assert recovery.json()["scope"] == "RECOVERY"
    assert current.calls[0]["job_kind"] == "RECOVERY_RESTORE"

    wellness = client.post(
        "/api/v2/real/sync-jobs", headers=AUTH, json={"scope": "WELLNESS"}
    )
    assert wellness.status_code == 202
    assert wellness.json()["scope"] == "WELLNESS"
    assert current.calls[1]["job_kind"] == "WELLNESS_SYNC"

    unsupported_source = Repository(_legacy_snapshot(), "generation-legacy-source")
    monkeypatch.setattr(api_main, "_repository", lambda: unsupported_source)
    upgraded_source = client.post(
        "/api/v2/real/sync-jobs", headers=AUTH, json={"scope": "RECOVERY"}
    )
    supported_wellness = client.post(
        "/api/v2/real/sync-jobs", headers=AUTH, json={"scope": "WELLNESS"}
    )
    assert upgraded_source.status_code == 202
    assert upgraded_source.json()["scope"] == "FULL"
    assert supported_wellness.status_code == 202
    assert supported_wellness.json()["scope"] == "WELLNESS"
    assert unsupported_source.calls[0]["job_kind"] == "FULL_SYNC"
    assert unsupported_source.calls[1]["job_kind"] == "WELLNESS_SYNC"

    legacy = Repository(_active_snapshot(), None)
    monkeypatch.setattr(api_main, "_repository", lambda: legacy)
    upgraded_recovery = client.post(
        "/api/v2/real/sync-jobs", headers=AUTH, json={"scope": "RECOVERY"}
    )
    upgraded_wellness = client.post(
        "/api/v2/real/sync-jobs", headers=AUTH, json={"scope": "WELLNESS"}
    )
    assert upgraded_recovery.status_code == 202
    assert upgraded_recovery.json()["scope"] == "FULL"
    assert upgraded_wellness.status_code == 202
    assert upgraded_wellness.json()["scope"] == "FULL"
    assert legacy.calls[0]["job_kind"] == "FULL_SYNC"
    assert legacy.calls[1]["job_kind"] == "FULL_SYNC"

    missing = Repository(None, None)
    monkeypatch.setattr(api_main, "_repository", lambda: missing)
    upgraded_missing = client.post(
        "/api/v2/real/sync-jobs", headers=AUTH, json={"scope": "RECOVERY"}
    )
    assert upgraded_missing.status_code == 202
    assert upgraded_missing.json()["scope"] == "FULL"
    assert missing.calls[0]["job_kind"] == "FULL_SYNC"


def test_dashboard_view_uses_one_generation_pinned_repository_read(monkeypatch):
    class Repository:
        def __init__(self):
            self.reads = 0

        def active_analysis(self, athlete_alias):
            self.reads += 1
            assert athlete_alias == "ath-sync-test"
            return {
                "generation_id": "generation-8",
                "revision": 8,
                "analysis_as_of": "2026-08-09",
                "activated_at": "2026-08-10T06:00:00+00:00",
                "snapshot_payload": _active_snapshot(),
            }

        def latest(self, athlete_alias):
            raise AssertionError("dashboard must not perform a second snapshot read")

        def sync_state(self, athlete_alias):
            raise AssertionError("dashboard must not mix generation metadata")

    repository = Repository()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)

    response = TestClient(app).get(
        "/api/v2/real/dashboard-view"
        "?period_start=2026-08-09&period_end=2026-08-09",
        headers=AUTH,
    )

    assert response.status_code == 200
    assert repository.reads == 1
    payload = response.json()
    assert payload["schema_version"] == "dashboard-view-v1"
    assert payload["generation_id"] == "generation-8"
    assert payload["revision"] == 8
    assert payload["load_history"]["schema_version"] == "load-history-v2"
    assert payload["completed_work"]["period_start"] == "2026-08-09"
    assert payload["completed_work"]["period_end"] == "2026-08-09"
    assert payload["completed_work"]["model"]["source_schema_version"] == "load-history-v2"
    assert payload["volume_history"]["model"]["source_schema_version"] == "load-history-v2"

    outside = TestClient(app).get(
        "/api/v2/real/dashboard-view?period_start=2026-08-08",
        headers=AUTH,
    )
    assert outside.status_code == 422


def test_dashboard_view_keeps_legacy_analysis_visible_without_recovery(monkeypatch):
    snapshot = _legacy_snapshot()
    snapshot["recovery_history"] = None

    class Repository:
        def active_analysis(self, athlete_alias):
            assert athlete_alias == "ath-sync-test"
            return {
                "generation_id": None,
                "revision": 0,
                "analysis_as_of": None,
                "activated_at": "2026-08-10T06:00:00+00:00",
                "snapshot_payload": snapshot,
            }

    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(api_main, "_repository", lambda: Repository())

    response = TestClient(app).get(
        "/api/v2/real/dashboard-view"
        "?period_start=2026-08-09&period_end=2026-08-09",
        headers=AUTH,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["generation_id"] is None
    assert payload["revision"] == 0
    assert payload["training_status"]["athlete_id"] == "ath-sync-test"
    assert payload["load_history"]["schema_version"] == "load-history-v1"
    assert payload["recovery_history"] is None
