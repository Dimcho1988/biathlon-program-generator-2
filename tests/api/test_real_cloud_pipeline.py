from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from apps.api import main as api_main
from apps.api.cloud import AthleteModelSettings, InMemorySnapshotRepository
from apps.api.main import app
from apps.api.real_service import (
    ConfigurationError,
    ProviderFailure,
    _bounded_percentage,
    completed_work_from_load_history,
    completed_work_from_persisted,
    load_history_from_persisted,
    recovery_history_from_persisted,
    refresh,
    training_status_from_persisted,
    volume_history_from_load_history,
    volume_history_from_persisted,
)
from intervals_inspector.intervals_client import IntervalsAPIError, IntervalsResponse


ENV = {
    "ONFLOWS_ATHLETE_ALIAS": "pilot",
    "INTERVALS_ATHLETE_ID": "private-athlete",
    "INTERVALS_ACCESS_TOKEN": "private-token",
    "ONFLOWS_SNAPSHOT_SALT": "private-salt",
    "ONFLOWS_HR_ZONE_BOUNDS": "100,126,146,163,178,196",
    "ONFLOWS_ATHLETE_TIMEZONE": "Europe/Sofia",
    "ONFLOWS_INTRAZONE_VERSION": "intra_zone_linear_v1",
    "ONFLOWS_TREF_VERSION": "tref-fixed-expert-v1",
    "ONFLOWS_RECOVERY_VERSION": "main-load-recovery-v1",
    "ONFLOWS_HISTORY_DAYS": "41",
}


class Client:
    def __init__(self, *, fail=False, wellness=None):
        self.fail = fail
        self.wellness = wellness if wellness is not None else [{"id": "2026-08-15", "sleepSecs": 28800}]

    def get_athlete_result(self): return IntervalsResponse(200, {"id": "must-not-survive"})
    def get_sport_settings_result(self): return IntervalsResponse(200, [{"private": True}])
    def get_wellness_result(self, oldest, newest): return IntervalsResponse(200, self.wellness)
    def get_activities_result(self, oldest, newest):
        if self.fail: raise IntervalsAPIError("provider leaked detail")
        return IntervalsResponse(200, [{"id": "provider-activity", "start_date_local": "2026-08-15T08:00:00"}])
    def get_activity_result(self, activity_id, *, include_intervals=False):
        return IntervalsResponse(200, {"id": activity_id, "start_date_local": "2026-08-15T08:00:00", "type": "Run", "moving_time": 60, "elapsed_time": 60, "icu_recording_time": 60, "recording_stops": []})
    def get_streams_result(self, activity_id):
        return IntervalsResponse(200, [{"type": "time", "data": list(range(61))}, {"type": "heartrate", "data": [145.0] * 61}])


class StrengthClient(Client):
    def get_activity_result(self, activity_id, *, include_intervals=False):
        return IntervalsResponse(
            200,
            {
                "id": activity_id,
                "start_date_local": "2026-08-15T08:00:00",
                "type": "WeightTraining",
                "moving_time": 600,
                "elapsed_time": 2100,
                "icu_recording_time": 1800,
                "recording_stops": [],
            },
        )

    def get_streams_result(self, activity_id):
        raise AssertionError("strength activities must not request or use HR streams")


def test_ingests_activity_and_wellness_then_atomically_publishes_aggregate_snapshot():
    repo = InMemorySnapshotRepository()
    result = refresh(repo, environ=ENV, client=Client(), period_end=date(2026, 8, 15), now=datetime(2026, 8, 15, 12, tzinfo=timezone.utc))
    payload = repo.latest("pilot")
    assert result.processed_activities == 1
    assert payload["schema_version"] == "athlete-snapshot-v1"
    assert payload["training_status"]["athlete_id"] == "pilot"
    assert payload["load_history"]["schema_version"] == "load-history-v1"
    assert payload["recovery_history"]["schema_version"] == "recovery-history-v1"
    diagnostics = payload["recovery_history"]["wellness_diagnostics"]
    assert diagnostics["schema_version"] == "wellness-coverage-v1"
    assert diagnostics["records_received"] == 1
    assert diagnostics["days_with_any_recognized_data"] == 1
    assert diagnostics["fields"][0]["field"] == "sleep_duration"
    assert diagnostics["affects_recovery"] is False
    assert len(payload["load_history"]["daily"]) == 41 * 5
    assert len(payload["load_history"]["activities"]) == 1
    assert len(payload["recovery_history"]["daily"]) == 41 * 5
    rendered = repr(payload)
    assert all(secret not in rendered for secret in ("private-athlete", "private-token", "provider-activity", "must-not-survive"))
    assert "28800" not in rendered


def test_strength_activity_is_persisted_as_one_str_component_without_hr_double_counting():
    repo = InMemorySnapshotRepository()
    refresh(
        repo,
        environ=ENV,
        client=StrengthClient(),
        period_end=date(2026, 8, 15),
    )
    payload = repo.latest("pilot")
    history = payload["load_history"]
    strength = history["strength"]
    assert strength["model"] == {
        "classification_version": "intervals-strength-activity-type-v1",
        "source": "intervals-activity-type-duration",
        "duration_basis": "recording-time-first",
        "equivalent_time_coefficient": 1.0,
        "aerobic_hr_counted": False,
    }
    assert strength["summary"]["recorded_activities"] == 1
    assert strength["summary"]["real_time_7d_min"] == pytest.approx(30.0)
    activity = history["activities"][0]
    assert activity["sport"] == "WeightTraining"
    assert activity["duration_min"] == pytest.approx(30.0)
    assert activity["strength_time_min"] == pytest.approx(30.0)
    assert activity["hr_coverage_percent"] == 0.0
    assert sum(row["raw_time_min"] for row in activity["zones"]) == 0.0
    recovery = payload["recovery_history"]["strength"]
    assert recovery["settings"]["tref_min"] == pytest.approx(56.0)
    assert recovery["current"]["readiness_percent"] < 100.0
    assert payload["training_status"]["data_quality"]["latest_activity_quality_score"] is None


def test_percentage_boundary_clamps_only_floating_point_drift():
    assert _bounded_percentage(100.00000000000001, "coverage") == 100.0
    assert _bounded_percentage(-0.00000000000001, "coverage") == 0.0
    with pytest.raises(ValueError, match="outside 0–100"):
        _bounded_percentage(100.01, "coverage")


def test_persisted_snapshot_exposes_v1_status_and_load_history_contracts():
    repo = InMemorySnapshotRepository()
    refresh(repo, environ=ENV, client=Client(), period_end=date(2026, 8, 15))
    payload = repo.latest("pilot")
    status = training_status_from_persisted(payload)
    history = load_history_from_persisted(payload)
    recovery = recovery_history_from_persisted(payload)
    assert status.schema_version == "training-status-v1"
    assert history.schema_version == "load-history-v1"
    assert [row.zone for row in history.zones] == ["Z1", "Z2", "Z3", "Z4", "Z5"]
    assert history.activities[0].activity_ref == "activity-001"
    assert recovery.basis == "load-only"
    assert recovery.model.parameter_version == "main-load-recovery-v1"
    assert [row.zone for row in recovery.settings] == ["Z1", "Z2", "Z3", "Z4", "Z5"]
    assert recovery.settings[3].tau_days == pytest.approx(1.65)
    report = completed_work_from_persisted(payload)
    assert report.schema_version == "completed-work-v1"
    assert report.model.source_schema_version == "load-history-v1"
    volume = volume_history_from_persisted(payload)
    assert volume.schema_version == "volume-history-v1"
    assert volume.model.source_schema_version == "load-history-v1"
    assert volume.model.calendar_week_start == "monday"


def test_volume_history_keeps_duration_and_hr_zoned_time_separate():
    repo = InMemorySnapshotRepository()
    refresh(repo, environ=ENV, client=Client(), period_end=date(2026, 8, 15))
    history = load_history_from_persisted(repo.latest("pilot"))
    payload = history.model_dump(mode="json")
    second = dict(payload["activities"][0])
    second.update(
        activity_ref="activity-002",
        date="2026-08-08",
        duration_min=None,
        quality_status="limited",
    )
    second["zones"] = [dict(row) for row in second["zones"]]
    payload["activities"].append(second)

    volume = volume_history_from_load_history(type(history).model_validate(payload))
    original_zoned = sum(row.raw_time_min for row in history.activities[0].zones)
    assert volume.quality.modeled_activities == 2
    assert volume.quality.limited_activities == 1
    assert volume.quality.missing_duration_activities == 1
    assert volume.weekly[0].week_start == "2026-07-06"
    assert volume.weekly[0].observed_days == 7
    assert volume.weekly[-1].week_end == "2026-08-16"
    assert volume.weekly[-1].observed_days == 6
    assert volume.weekly[-1].activity_duration_min == pytest.approx(
        history.activities[0].duration_min
    )
    assert volume.weekly[-1].zoned_hr_time_min == pytest.approx(original_zoned)
    assert volume.weekly[-2].activity_duration_min == 0
    assert volume.weekly[-2].zoned_hr_time_min == pytest.approx(original_zoned)
    assert volume.weekly[-2].missing_duration_activities == 1


def test_completed_work_aggregates_persisted_values_without_recalculating_physiology():
    repo = InMemorySnapshotRepository()
    refresh(repo, environ=ENV, client=Client(), period_end=date(2026, 8, 15))
    history = load_history_from_persisted(repo.latest("pilot"))
    payload = history.model_dump(mode="json")
    second = dict(payload["activities"][0])
    second.update(
        activity_ref="activity-002",
        date="2026-08-14",
        sport="TrailRun",
        duration_min=None,
        quality_status="limited",
    )
    second["zones"] = [dict(row) for row in second["zones"]]
    payload["activities"].append(second)
    enriched = type(history).model_validate(payload)

    report = completed_work_from_load_history(enriched)
    original_zoned = sum(row.raw_time_min for row in history.activities[0].zones)
    assert report.quality.modeled_activities == 2
    assert report.quality.limited_activities == 1
    assert report.quality.missing_duration_activities == 1
    assert report.totals.activity_duration_min == pytest.approx(
        history.activities[0].duration_min
    )
    assert report.totals.zoned_hr_time_min == pytest.approx(2 * original_zoned)
    assert [row.sport for row in report.sports] == ["Run", "TrailRun"]
    assert report.sports[1].activity_duration_min == 0
    for expected, actual in zip(history.activities[0].zones, report.zones):
        assert actual.zone == expected.zone
        assert actual.raw_time_min == pytest.approx(2 * expected.raw_time_min)
        assert actual.equivalent_time_min == pytest.approx(
            2 * expected.equivalent_time_min
        )
        assert actual.effective_load == pytest.approx(2 * expected.effective_load)

    selected = completed_work_from_load_history(
        enriched, date(2026, 8, 15), date(2026, 8, 15)
    )
    assert selected.quality.modeled_activities == 1
    assert [row.sport for row in selected.sports] == ["Run"]
    with pytest.raises(ValueError, match="outside stored history"):
        completed_work_from_load_history(
            enriched, date(2026, 7, 1), date(2026, 8, 15)
        )


def test_completed_work_endpoint_is_profile_scoped_and_validates_the_period(monkeypatch):
    repo = InMemorySnapshotRepository()
    refresh(repo, environ=ENV, client=Client(), period_end=date(2026, 8, 15))
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(api_main, "_repository", lambda: repo)
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer service-secret",
        "X-OnFlows-Athlete-Alias": "pilot",
    }

    response = client.get(
        "/api/v2/real/completed-work"
        "?period_start=2026-08-15&period_end=2026-08-15",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["quality"]["modeled_activities"] == 1
    assert response.json()["athlete_id"] == "pilot"
    assert client.get(
        "/api/v2/real/completed-work"
        "?period_start=2026-01-01&period_end=2026-08-15",
        headers=headers,
    ).status_code == 422

    volume = client.get("/api/v2/real/volume-history", headers=headers)
    assert volume.status_code == 200
    assert volume.json()["schema_version"] == "volume-history-v1"
    assert volume.json()["athlete_id"] == "pilot"


def test_deployed_v1_snapshot_remains_readable_during_rollout():
    legacy = {
        "schema_version": "training-status-v1",
        "as_of": "2026-08-15",
        "athlete_id": "pilot",
        "model": {
            "algorithm_version": "v1",
            "effective_hr_version": "v1",
            "effective_hr_source": "raw_hr",
            "parameter_version": 1,
        },
        "data_quality": {
            "history_reliability": 1.0,
            "latest_activity_quality_score": 1.0,
            "warnings": [],
        },
        "zones": [
            {
                "zone": zone,
                "raw_time_min": 0.0,
                "equivalent_time_min": 0.0,
                "tref_min": 1.0,
                "status_7_40": 1.0,
                "recovery_readiness_percent": 100.0,
                "recovery_days_to_full": 0.0,
            }
            for zone in ("Z1", "Z2", "Z3", "Z4", "Z5")
        ],
    }
    assert training_status_from_persisted(legacy).athlete_id == "pilot"


def test_snapshot_without_recovery_history_requires_one_refresh():
    repo = InMemorySnapshotRepository()
    refresh(repo, environ=ENV, client=Client(), period_end=date(2026, 8, 15))
    payload = repo.latest("pilot")
    payload.pop("recovery_history")
    with pytest.raises(ValueError, match="requires a new real-data refresh"):
        recovery_history_from_persisted(payload)


@pytest.mark.parametrize("wellness, warning", [([], "unknown"), ([{"id": "2026-01-01", "fatigue": "bad"}], "stale")])
def test_missing_stale_invalid_wellness_is_explicit(wellness, warning):
    result = refresh(InMemorySnapshotRepository(), environ=ENV, client=Client(wellness=wellness), period_end=date(2026, 8, 15), now=datetime(2026, 8, 15, tzinfo=timezone.utc))
    assert any(warning in item.lower() for item in result.snapshot.data_quality.warnings)
    assert any("load-only" in item for item in result.snapshot.data_quality.warnings)


def test_provider_failure_retains_last_valid_snapshot_and_is_sanitized():
    repo = InMemorySnapshotRepository(); repo.replace("pilot", {"schema_version": "training-status-v1", "sentinel": True})
    with pytest.raises(ProviderFailure, match="provider request failed") as caught:
        refresh(repo, environ=ENV, client=Client(fail=True), period_end=date(2026, 8, 15))
    assert "leaked" not in str(caught.value)
    assert repo.latest("pilot")["sentinel"] is True


def test_another_athlete_cannot_inherit_pilot_physiological_inputs():
    with pytest.raises(
        ConfigurationError, match="Athlete-specific physiological configuration"
    ):
        refresh(
            InMemorySnapshotRepository(),
            environ=ENV,
            client=Client(),
            athlete_alias="ath-another-profile",
            provider_athlete_id="another-private-id",
            period_end=date(2026, 8, 15),
        )


def test_another_athlete_can_refresh_with_explicit_individual_settings():
    result = refresh(
        InMemorySnapshotRepository(),
        environ=ENV,
        client=Client(),
        athlete_alias="ath-another-profile",
        provider_athlete_id="another-private-id",
        athlete_settings=AthleteModelSettings(
            (90, 115, 135, 155, 175, 195), "Europe/Sofia"
        ),
        period_end=date(2026, 8, 15),
    )
    assert result.processed_activities == 1
    assert result.snapshot.athlete_id == "ath-another-profile"
