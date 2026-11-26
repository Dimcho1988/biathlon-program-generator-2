from datetime import date, datetime, timedelta, timezone
from time import perf_counter

import pytest

from apps.api import activity_refresh, model_service
from apps.api.cloud import AthleteModelSettings, InMemorySnapshotRepository
from apps.api.real_service import refresh
from intervals_inspector.intervals_client import IntervalsResponse
from tests.api.test_real_cloud_pipeline import Client, ENV


END = date(2026, 8, 15)
NOW = datetime(2026, 8, 16, 17, tzinfo=timezone.utc)


class Recordings(Client):
    def __init__(self):
        super().__init__()
        self.rows = {}
        self.details = []
        self.streams = []

    def add(self, day, hour=8, sport="Run"):
        stamp = f"{day}T{hour:02}:00:00"
        key = f"provider-{len(self.rows)}"
        self.rows[key] = {"id": key, "start_date": stamp + "Z", "start_date_local": stamp,
                          "analyzed": stamp + "Z", "type": sport, "moving_time": 720, "elapsed_time": 720}
        return key

    def get_activities_result(self, oldest, newest):
        return IntervalsResponse(200, [r for r in self.rows.values() if oldest <= r["start_date_local"][:10] <= newest])

    def get_activity_result(self, key, *, include_intervals=False):
        self.details.append(key)
        return IntervalsResponse(200, {**self.rows[key], "recording_stops": [], "distance": 2400.})

    def get_streams_result(self, key):
        self.streams.append(key)
        return IntervalsResponse(200, [{"type": "time", "data": list(range(721))},
            {"type": "heartrate", "data": [152] * 721}, {"type": "velocity_smooth", "data": [10/3] * 721}])


def cloud_fixture(monkeypatch, repo):
    settings = AthleteModelSettings((90, 144, 158, 178, 188, 200), "UTC", 200)
    repo.athlete_settings = lambda _: settings
    repo._request = lambda *args: None

    def inputs(repository, alias):
        return {"alias": alias, "settings": settings, "today": END + timedelta(days=1), "history": [],
                "calendar": {"activities": list(repo.activity_calendar(alias, date.min, date.max)),
                             "snapshot_payload": repo.latest(alias)},
                "entries": [], "initial_profile": {"training_experience_years": 4., "revision": 1}}
    monkeypatch.setattr(model_service, "speed_view_inputs", inputs)
    return settings


@pytest.mark.parametrize("offset", [0, 1])
def test_append_matches_full_refresh_without_old_recordings(monkeypatch, offset):
    client = Recordings()
    for days in (40, 10, 5, 2, 1, 0):
        client.add(END - timedelta(days=days))
    client.add(END - timedelta(days=2), 16, "WeightTraining")
    repo = InMemorySnapshotRepository()
    settings = cloud_fixture(monkeypatch, repo)
    def run(end):
        client.details.clear()
        client.streams.clear()
        start = perf_counter()
        refresh(repo, environ=ENV, client=client, period_end=end, now=NOW, athlete_settings=settings)
        return perf_counter() - start
    run(END)
    day = END + timedelta(days=offset)
    new = client.add(day, 16)
    append_seconds = run(day)
    assert client.details == client.streams == [new]
    expected = repo.latest("pilot")
    kept = {row["activity_ref"]: (row["latest_canonical_run_key"], row.get("latest_shadow_run_key"))
            for row in repo.activity_calendar("pilot", day - timedelta(days=40), day)}
    monkeypatch.setattr(activity_refresh, "calculation_version", lambda _: None)
    full_seconds = run(day)
    assert len(client.details) == 8 - offset
    assert repo.latest("pilot") == expected
    assert kept == {row["activity_ref"]: (row["latest_canonical_run_key"], row.get("latest_shadow_run_key"))
                    for row in repo.activity_calendar("pilot", day - timedelta(days=40), day)}
    print(f"old={len(kept)-1} append={append_seconds:.3f}s full={full_seconds:.3f}s")


def test_retry_and_reanalysis_only_download_changed_measurements(monkeypatch):
    client = Recordings()
    first = client.add(END - timedelta(days=2))
    client.add(END)
    repo = InMemorySnapshotRepository()
    settings = cloud_fixture(monkeypatch, repo)
    def sync():
        client.details.clear()
        client.streams.clear()
        refresh(repo, environ=ENV, client=client, period_end=END, now=NOW, athlete_settings=settings)
        return repo.latest("pilot")
    baseline = sync()
    assert sync() == baseline
    assert client.details == client.streams == []
    client.rows[first]["analyzed"] = f"{END}T10:30:00Z"
    sync()
    assert client.details == client.streams == [first]
    client.add(END - timedelta(days=1))
    sync()
    assert len(client.details) == 3
    del client.rows[first]
    updated = sync()
    assert len(client.details) == len(updated["load_history"]["activities"]) == 2


def test_changed_calibration_never_reuses_old_dose(monkeypatch):
    client = Recordings()
    client.add(END)
    repo = InMemorySnapshotRepository()
    settings = cloud_fixture(monkeypatch, repo)
    def sync(settings):
        refresh(repo, environ=ENV, client=client, period_end=END, now=NOW, athlete_settings=settings)
    sync(settings)
    from dataclasses import replace
    corrected = replace(settings, hrmax_bpm=190, zone_bounds_bpm=(86, 137, 150, 169, 179, 190))
    old_inputs = model_service.speed_view_inputs
    monkeypatch.setattr(model_service, "speed_view_inputs",
                        lambda *args: {**old_inputs(*args), "settings": corrected})
    client.add(END, 16)
    client.details.clear()
    sync(corrected)
    assert len(client.details) == 2
    assert repo.latest("pilot")["load_history"]["hrmax_bpm"] == 190
