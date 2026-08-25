from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from apps.api import main as api_main
from apps.api.activity_catalog import (
    activity_calendar_payload,
    extract_activity_metadata,
    provider_activity_key,
)
from apps.api.cloud import InMemorySnapshotRepository
from apps.api.main import app
from apps.api.oauth_store import PersistentStoreFailure
from apps.api.real_service import refresh
from intervals_inspector.intervals_client import IntervalsResponse


ENV = {
    "ONFLOWS_ATHLETE_ALIAS": "pilot",
    "INTERVALS_ATHLETE_ID": "private-athlete",
    "INTERVALS_ACCESS_TOKEN": "private-token",
    "ONFLOWS_SNAPSHOT_SALT": "stable-secret",
    "ONFLOWS_HR_ZONE_BOUNDS": "100,126,146,163,178,196",
    "ONFLOWS_ATHLETE_TIMEZONE": "Europe/Sofia",
    "ONFLOWS_INTRAZONE_VERSION": "intra_zone_linear_v1",
    "ONFLOWS_TREF_VERSION": "tref-fixed-expert-v1",
    "ONFLOWS_RECOVERY_VERSION": "main-load-recovery-v1",
    "ONFLOWS_HISTORY_DAYS": "41",
}


class CatalogClient:
    def __init__(self, *, name: str = "Morning threshold", hr: float = 150.0):
        self.name = name
        self.hr = hr

    def get_athlete_result(self):
        return IntervalsResponse(200, {})

    def get_sport_settings_result(self):
        return IntervalsResponse(200, [])

    def get_wellness_result(self, oldest, newest):
        return IntervalsResponse(200, [])

    def get_activities_result(self, oldest, newest):
        return IntervalsResponse(
            200,
            [{"id": "provider-activity", "start_date_local": "2026-08-15T08:00:00"}],
        )

    def get_activity_result(self, activity_id, *, include_intervals=False):
        assert include_intervals is True
        return IntervalsResponse(
            200,
            {
                "id": activity_id,
                "start_date": "2026-08-15T05:00:00Z",
                "start_date_local": "2026-08-15T08:00:00",
                "timezone": "Europe/Sofia",
                "type": "Run",
                "name": self.name,
                "description": "Private athlete note",
                "moving_time": 60,
                "elapsed_time": 60,
                "icu_recording_time": 60,
                "distance": 240.0,
                "average_heartrate": self.hr,
                "max_heartrate": self.hr + 10,
                "average_speed": 4.0,
                "total_elevation_gain": 8.0,
                "recording_stops": [],
                "intervals": [{"name": "Work", "elapsed_time": 30}],
            },
        )

    def get_streams_result(self, activity_id):
        return IntervalsResponse(
            200,
            [
                {"type": "time", "data": list(range(61))},
                {"type": "heartrate", "data": [self.hr] * 61},
                {"type": "velocity_smooth", "data": [4.0] * 61},
                {"type": "altitude", "data": [600.0 + index * 0.1 for index in range(61)]},
                {"type": "distance", "data": [index * 4.0 for index in range(61)]},
            ],
        )


class CatalogFailsOnceRepository(InMemorySnapshotRepository):
    def __init__(self):
        super().__init__()
        self.catalog_failures_remaining = 1

    def upsert_activity_catalog(self, athlete_alias, activities):
        if self.catalog_failures_remaining:
            self.catalog_failures_remaining -= 1
            raise PersistentStoreFailure("simulated catalog failure")
        return super().upsert_activity_catalog(athlete_alias, activities)


class WellnessCatalogClient(CatalogClient):
    def get_wellness_result(self, oldest, newest):
        return IntervalsResponse(200, [{
            "id": "2026-08-15",
            "sleepSecs": 28200,
            "sleepScore": 84,
            "restingHR": 42,
            "hrv": 96,
            "weight": 68.6,
            "steps": 13240,
        }])


def test_provider_identity_is_stable_opaque_and_athlete_scoped():
    first = provider_activity_key(
        provider_athlete_id="athlete-a",
        provider_activity_id="activity-1",
        secret="secret",
    )
    assert first == provider_activity_key(
        provider_athlete_id="athlete-a",
        provider_activity_id="activity-1",
        secret="secret",
    )
    assert first != provider_activity_key(
        provider_athlete_id="athlete-b",
        provider_activity_id="activity-1",
        secret="secret",
    )
    assert "athlete" not in first and "activity" not in first


def test_refresh_persists_provider_key_but_calendar_contract_keeps_it_private():
    repository = InMemorySnapshotRepository()
    refresh(
        repository,
        environ=ENV,
        client=CatalogClient(),
        period_end=date(2026, 8, 15),
        now=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    rows = repository.activity_calendar(
        "pilot", date(2026, 8, 15), date(2026, 8, 15)
    )
    assert rows[0]["provider_activity_key"] == provider_activity_key(
        provider_athlete_id="private-athlete",
        provider_activity_id="provider-activity",
        secret="stable-secret",
    )
    public_item = activity_calendar_payload(
        athlete_alias="pilot",
        period_start=date(2026, 8, 15),
        period_end=date(2026, 8, 15),
        rows=rows,
    )["activities"][0]
    assert "provider_activity_key" not in public_item


def test_catalog_clamps_harmless_hr_coverage_drift(monkeypatch):
    from intervals_inspector import real_data_source

    real_loader = real_data_source.load_real_history

    def load_with_float_drift(*args, **kwargs):
        dataset = real_loader(*args, **kwargs)
        dataset.activities.loc[:, "hr_coverage_percent"] = 100.00000000000001
        return dataset

    monkeypatch.setattr(real_data_source, "load_real_history", load_with_float_drift)
    repository = InMemorySnapshotRepository()
    refresh(
        repository,
        environ=ENV,
        client=CatalogClient(),
        period_end=date(2026, 8, 15),
        now=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )

    rows = repository.activity_calendar(
        "pilot", date(2026, 8, 15), date(2026, 8, 15)
    )
    assert rows[0]["hr_coverage_percent"] == 100.0


def test_timezone_and_dst_offset_are_preserved_without_provider_payload():
    summer = extract_activity_metadata(
        "act_" + "1" * 32,
        {
            "start_date": "2026-08-15T05:00:00Z",
            "start_date_local": "2026-08-15T08:00:00",
            "timezone": "Europe/Sofia",
            "type": "Run",
            "name": "Private",
            "latlng": [[42.0, 23.0]],
        },
    )
    winter = extract_activity_metadata(
        "act_" + "2" * 32,
        {
            "start_date": "2026-12-15T06:00:00Z",
            "start_date_local": "2026-12-15T08:00:00",
            "timezone": "Europe/Sofia",
            "type": "Run",
        },
    )
    assert summer["utc_offset_minutes"] == 180
    assert winter["utc_offset_minutes"] == 120
    assert "latlng" not in summer


def test_edited_name_does_not_create_a_new_scientific_run():
    repository = InMemorySnapshotRepository()
    refresh(
        repository,
        environ=ENV,
        client=CatalogClient(name="Original name"),
        period_end=date(2026, 8, 15),
        now=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    activity_ref = repository.activity_calendar(
        "pilot", date(2026, 8, 15), date(2026, 8, 15)
    )[0]["activity_ref"]
    first_run = repository.activity_shadow("pilot", activity_ref)["result_hash"]
    refresh(
        repository,
        environ=ENV,
        client=CatalogClient(name="Edited private name"),
        period_end=date(2026, 8, 15),
        now=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    rows = repository.activity_calendar(
        "pilot", date(2026, 8, 15), date(2026, 8, 15)
    )
    assert rows[0]["activity_ref"] == activity_ref
    assert rows[0]["name"] == "Edited private name"
    assert repository.activity_shadow("pilot", activity_ref)["result_hash"] == first_run
    assert len(repository._activity_runs[("pilot", activity_ref)]) == 1
    assert len(repository._canonical_activity_runs[("pilot", activity_ref)]) == 1
    assert "Edited private name" not in repr(repository._activity_inputs)


def test_retry_relinks_shadow_run_after_catalog_write_failed():
    repository = CatalogFailsOnceRepository()

    with pytest.raises(PersistentStoreFailure):
        refresh(
            repository,
            environ=ENV,
            client=CatalogClient(),
            period_end=date(2026, 8, 15),
            now=datetime(2026, 8, 15, tzinfo=timezone.utc),
        )

    assert len(repository._activity_runs) == 1
    refresh(
        repository,
        environ=ENV,
        client=CatalogClient(),
        period_end=date(2026, 8, 15),
        now=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )

    row = repository.activity_calendar(
        "pilot", date(2026, 8, 15), date(2026, 8, 15)
    )[0]
    assert row["latest_shadow_run_key"] == repository.latest_activity_shadow_run_key(
        "pilot", row["activity_ref"]
    )
    assert len(repository._activity_runs[("pilot", row["activity_ref"])]) == 1


def test_changed_scientific_input_creates_a_new_derived_run():
    repository = InMemorySnapshotRepository()
    refresh(repository, environ=ENV, client=CatalogClient(hr=145), period_end=date(2026, 8, 15))
    activity_ref = repository.activity_calendar(
        "pilot", date(2026, 8, 15), date(2026, 8, 15)
    )[0]["activity_ref"]
    refresh(repository, environ=ENV, client=CatalogClient(hr=155), period_end=date(2026, 8, 15))
    assert len(repository._activity_runs[("pilot", activity_ref)]) == 2
    assert len(repository._canonical_activity_runs[("pilot", activity_ref)]) == 2


def test_changed_hrmax_recomputes_shadow_with_same_activity_input():
    repository = InMemorySnapshotRepository()
    refresh(repository, environ=ENV, client=CatalogClient(), period_end=date(2026, 8, 15))
    activity_ref = repository.activity_calendar(
        "pilot", date(2026, 8, 15), date(2026, 8, 15)
    )[0]["activity_ref"]
    first_input_hash = repository.latest_activity_input_hash("pilot", activity_ref)
    assert repository.activity_shadow("pilot", activity_ref)["zone_summary"] == []

    configured = {**ENV, "ONFLOWS_HRMAX_BPM": "200"}
    refresh(repository, environ=configured, client=CatalogClient(), period_end=date(2026, 8, 15))

    assert repository.latest_activity_input_hash("pilot", activity_ref) == first_input_hash
    assert len(repository._activity_runs[("pilot", activity_ref)]) == 2
    assert len(repository.activity_shadow("pilot", activity_ref)["zone_summary"]) == 5


def test_calendar_contract_is_summary_only_and_athlete_isolated(monkeypatch):
    repository = InMemorySnapshotRepository()
    refresh(repository, environ=ENV, client=CatalogClient(), period_end=date(2026, 8, 15))
    repository.upsert_activity_catalog(
        "other-athlete",
        [{
            **dict(repository.activity_calendar("pilot", date(2026, 8, 15), date(2026, 8, 15))[0]),
            "activity_ref": "act_" + "9" * 32,
        }],
    )
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)
    response = TestClient(app).get(
        "/api/v2/real/activities?period_start=2026-08-01&period_end=2026-08-15",
        headers={
            "Authorization": "Bearer service-secret",
            "X-OnFlows-Athlete-Alias": "pilot",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["includes_timeseries"] is False
    assert len(payload["activities"]) == 1
    assert all("series" not in activity for activity in payload["activities"])
    assert "description" not in payload["activities"][0]
    assert payload["activities"][0]["name"] == "Morning threshold"
    assert activity_calendar_payload(
        athlete_alias="pilot",
        period_start=date(2026, 8, 15),
        period_end=date(2026, 8, 15),
        rows=repository.activity_calendar("pilot", date(2026, 8, 15), date(2026, 8, 15)),
    )["includes_timeseries"] is False


def test_calendar_adds_daily_wellness_and_hrmod_final_zone_visualization(monkeypatch):
    repository = InMemorySnapshotRepository()
    refresh(
        repository,
        environ={**ENV, "ONFLOWS_HRMAX_BPM": "200"},
        client=WellnessCatalogClient(),
        period_end=date(2026, 8, 15),
        now=datetime(2026, 8, 15, 12, tzinfo=timezone.utc),
    )
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)

    response = TestClient(app).get(
        "/api/v2/real/activities?period_start=2026-08-15&period_end=2026-08-15",
        headers={
            "Authorization": "Bearer service-secret",
            "X-OnFlows-Athlete-Alias": "pilot",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["wellness_integration"] == "DIAGNOSTIC_ONLY"
    assert payload["wellness_status"] == {
        "state": "available",
        "records_received": 1,
        "stored_days": 1,
        "displayed_days": 1,
        "latest_observed_date": "2026-08-15",
    }
    assert payload["wellness_days"][0]["metrics"]["sleep_duration"]["value"] == 28200
    assert payload["wellness_days"][0]["metrics"]["hrv"]["value"] == 96
    activity = payload["activities"][0]
    assert activity["zone_visualization_source"] == "hrmod_final"
    assert len(activity["hrmod_zones"]) == 5
    assert sum(zone["final_time_s"] for zone in activity["hrmod_zones"]) > 0


def test_calendar_marks_legacy_snapshot_as_wellness_refresh_required(monkeypatch):
    repository = InMemorySnapshotRepository()
    repository.replace("pilot", {
        "schema_version": "athlete-snapshot-v1",
        "training_status": {},
        "load_history": {},
        "recovery_history": None,
    })
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(api_main, "_repository", lambda: repository)

    response = TestClient(app).get(
        "/api/v2/real/activities?period_start=2026-08-15&period_end=2026-08-15",
        headers={
            "Authorization": "Bearer service-secret",
            "X-OnFlows-Athlete-Alias": "pilot",
        },
    )

    assert response.status_code == 200
    assert response.json()["wellness_status"]["state"] == "refresh_required"
