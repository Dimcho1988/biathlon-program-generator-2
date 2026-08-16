from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from apps.api.cloud import InMemorySnapshotRepository
from apps.api.real_service import (
    ProviderFailure,
    _bounded_percentage,
    load_history_from_persisted,
    refresh,
    training_status_from_persisted,
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


def test_ingests_activity_and_wellness_then_atomically_publishes_aggregate_snapshot():
    repo = InMemorySnapshotRepository()
    result = refresh(repo, environ=ENV, client=Client(), period_end=date(2026, 8, 15), now=datetime(2026, 8, 15, 12, tzinfo=timezone.utc))
    payload = repo.latest("pilot")
    assert result.processed_activities == 1
    assert payload["schema_version"] == "athlete-snapshot-v1"
    assert payload["training_status"]["athlete_id"] == "pilot"
    assert payload["load_history"]["schema_version"] == "load-history-v1"
    assert len(payload["load_history"]["daily"]) == 41 * 5
    assert len(payload["load_history"]["activities"]) == 1
    rendered = repr(payload)
    assert all(secret not in rendered for secret in ("private-athlete", "private-token", "provider-activity", "must-not-survive"))


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
    assert status.schema_version == "training-status-v1"
    assert history.schema_version == "load-history-v1"
    assert [row.zone for row in history.zones] == ["Z1", "Z2", "Z3", "Z4", "Z5"]
    assert history.activities[0].activity_ref == "activity-001"


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
