from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from apps.api.cloud import InMemorySnapshotRepository
from apps.api.real_service import ProviderFailure, refresh
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


def test_ingests_activity_and_wellness_then_atomically_publishes_v1():
    repo = InMemorySnapshotRepository()
    result = refresh(repo, environ=ENV, client=Client(), period_end=date(2026, 8, 15), now=datetime(2026, 8, 15, 12, tzinfo=timezone.utc))
    payload = repo.latest("pilot")
    assert result.processed_activities == 1 and payload["schema_version"] == "training-status-v1"
    assert payload["athlete_id"] == "pilot"
    rendered = repr(payload)
    assert all(secret not in rendered for secret in ("private-athlete", "private-token", "provider-activity", "must-not-survive"))


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
