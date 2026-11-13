"""Capacity fast reads must preserve dosing and observe fresh athlete evidence."""
from collections import Counter
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from apps.api import component_load_context as capacity, model_service
from apps.api.read_session import ReadSession
from apps.api.speed_load_cache import SpeedLoadCache
from tests.api.test_model_service import SpeedStore, NOW


@pytest.mark.parametrize("sport", ["Run", "NordicSki", "Ride"])
@pytest.mark.parametrize("calibrated", [False, True])
def test_capacity_fast_path_matches_full_model(sport, calibrated, monkeypatch):
    class Clock(model_service.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(model_service, "datetime", Clock)
    repo = ReadSession(SpeedStore())
    if calibrated:
        repo._repository.rows = [{"kind": "SPEED_TEST", "entry_key": "manual", "revision": 1,
            "payload": {"sport": sport, "day": NOW.date().isoformat(), "enabled": True,
                "duration_s": 600, "speed_kmh": 18, "maximal": True, "comparable": True, "source": "MANUAL"}}]
    full = model_service.speed_view(repo, "ath-test", sport)
    fast = model_service.speed_view(repo, "ath-test", sport, _capacity_only=True)
    assert capacity.context_from_speed_view(fast) == capacity.context_from_speed_view(full)
    assert "functional_profile" not in fast and "points" not in fast


def test_context_cache_rereads_inputs_and_invalidates_all_mutable_evidence(monkeypatch):
    monkeypatch.setattr(capacity, "_context_cache", SpeedLoadCache())
    inputs = {"settings": SimpleNamespace(zone_bounds_bpm=(100, 125, 145, 160, 175, 190),
        hrmax_bpm=190, timezone="Europe/Sofia"), "today": date(2026, 10, 15),
        "calendar": {"revision": 1}, "entries": [], "history": [{"old_index": 1}]}
    calls = Counter()
    class Repository:
        speed_load_cache_namespace = "private-test-namespace"
        def _request(self, *args):
            raise AssertionError("Inputs are supplied by the fixture")
    repo = Repository()
    def read(repository, alias):
        calls["inputs"] += 1
        return {**deepcopy(inputs), "alias": alias}
    def view(repository, alias, sport, *, _inputs, _capacity_only):
        assert _inputs["alias"] == alias and _capacity_only
        calls["models"] += 1
        return {"sport": sport, "status": "UNAVAILABLE"}
    monkeypatch.setattr(model_service, "speed_view_inputs", read)
    monkeypatch.setattr(model_service, "speed_view", view)
    def get(alias="one"):
        return capacity.read_contexts(repo, alias, ["Run", "Ride"])
    first = get()
    first["Run"]["minutes"]["Z1"] = -1
    assert get()["Run"]["minutes"]["Z1"] > 0
    assert calls == {"inputs": 2, "models": 2}
    # An older summary can change without changing the latest date/revision.
    inputs["history"][0]["old_index"] = 2
    get()
    inputs["entries"].append({"revision": 2})
    get()
    inputs["settings"].hrmax_bpm = 191
    get()
    inputs["today"] = date(2026, 10, 16)
    get()
    get("two")
    repo.speed_load_cache_namespace = "another-database"
    get()
    assert calls == {"inputs": 8, "models": 14}


def test_sports_share_one_request_snapshot_without_caching_raw_streams():
    class Repository(SpeedStore):
        def __init__(self):
            super().__init__()
            self.calls = Counter()
        def athlete_settings(self, alias):
            self.calls["settings"] += 1
            return super().athlete_settings(alias)
        def activity_speed_exposure_samples(self, alias, keys):
            self.calls["streams"] += 1
            return {}
    source = Repository()
    repo = ReadSession(source)
    first = model_service.speed_view_inputs(repo, "ath-test")
    first["entries"].append({"invalid": True})
    assert model_service.speed_view_inputs(repo, "ath-test")["entries"] == []
    assert source.calls["settings"] == 1
    repo.activity_speed_exposure_samples("ath-test", ("one",))
    repo.activity_speed_exposure_samples("ath-test", ("one",))
    assert source.calls["streams"] == 2
