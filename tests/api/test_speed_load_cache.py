from concurrent.futures import ThreadPoolExecutor, Future
from copy import deepcopy
from datetime import timedelta
from threading import Event
from uuid import uuid4
from types import SimpleNamespace
import pytest
from apps.api import speed_load as model
from apps.api import speed_load_cache as cache_module
from apps.api.speed_load_cache import SpeedLoadCache
from tests.api.test_speed_load import Repository, TODAY, SETTINGS


def test_repeated_speed_reports_reuse_work_and_invalidate_current_sources(monkeypatch):
    repo = Repository()
    repo.speed_load_cache_namespace = str(uuid4())
    repo.add("first", "Run", TODAY-timedelta(days=2), 150, 15)
    repo.add("last", "Run", TODAY, 150, 15)
    original = repo.activity_speed_exposure_samples
    calls = []
    monkeypatch.setattr(repo, "activity_speed_exposure_samples", lambda *args: calls.append(args) or original(*args))
    first = model.history_view(repo, "authorized-athlete", today=TODAY)
    count = len(calls)
    assert count > 0
    assert model.history_view(repo, "authorized-athlete", today=TODAY) == first
    assert len(calls) == count
    first["zones"][0]["minutes"] = -999
    assert model.history_view(repo, "authorized-athlete", today=TODAY)["zones"][0]["minutes"] >= 0
    # A second athlete with identical input metadata still computes independently.
    other = SimpleNamespace(speed_load_cache_namespace=repo.speed_load_cache_namespace,
        athlete_settings=lambda _: SETTINGS,
        active_trainability_calendar=lambda _, *dates: repo.active_trainability_calendar("authorized-athlete", *dates),
        trainability_summaries=lambda _, keys: repo.trainability_summaries("authorized-athlete", keys),
        activity_speed_exposure_samples=lambda _, keys: repo.activity_speed_exposure_samples("authorized-athlete", keys))
    model.history_view(other, "other-athlete", today=TODAY)
    assert len(calls) > count; count = len(calls)
    # The same date range with a different sport must never reuse the all-sport report.
    model.history_view(repo, "authorized-athlete", "Run", today=TODAY)
    assert len(calls) > count; count = len(calls)
    model.history_view(repo, "authorized-athlete", today=TODAY,
                       period_start=TODAY-timedelta(days=1), period_end=TODAY)
    assert len(calls) > count; count = len(calls)
    # Summary correction under the same generation also invalidates reuse.
    repo.summaries["first"]["trainability_index"]["general"]["index"] *= 1.01
    model.history_view(repo, "authorized-athlete", today=TODAY)
    assert len(calls) > count; count = len(calls)
    changed = deepcopy(SETTINGS); changed.hrmax_bpm = 201
    monkeypatch.setattr(repo, "athlete_settings", lambda _: changed)
    model.history_view(repo, "authorized-athlete", today=TODAY)
    assert len(calls) > count; count = len(calls)
    calendar = repo.active_trainability_calendar
    monkeypatch.setattr(repo, "active_trainability_calendar", lambda *args: {**calendar(*args), "revision": 2})
    assert model.history_view(repo, "authorized-athlete", today=TODAY)["source_revision"] == 2
    assert len(calls) > count; count = len(calls)
    # Cache namespaces separate databases even when aliases/generations coincide.
    repo.speed_load_cache_namespace += "-other-database"
    model.history_view(repo, "authorized-athlete", today=TODAY)
    assert len(calls) > count


def test_cache_has_expiry_capacity_and_does_not_retain_failures_or_missing_runs():
    now = [0]
    cache = SpeedLoadCache(max_entries=2, ttl_seconds=10, clock=lambda: now[0])
    calls = []
    def compute():
        calls.append(1)
        return {"activities": []}
    for key in ["a", "b", "a", "c", "b"]:
        cache.get_or_compute(key, compute)
    assert len(calls) == 4  # b was evicted; accessing a made it most recent.
    now[0] = 11
    cache.get_or_compute("b", compute)
    assert len(calls) == 5
    def fail(): raise RuntimeError("temporary")
    with pytest.raises(RuntimeError): cache.get_or_compute("error", fail)
    assert cache.get_or_compute("error", compute) == {"activities": []}
    missing = {"activities": [{"reason": "SPEED_RECOMPUTATION_REQUIRED"}]}
    cache.get_or_compute("missing", lambda: missing)
    assert cache.get_or_compute("missing", compute) == {"activities": []}
    cache.get_or_compute("warmup-missing", lambda: {"activities": [], "warnings": ["SPEED_RECOMPUTATION_REQUIRED"]})
    assert cache.get_or_compute("warmup-missing", compute) == {"activities": []}


def test_concurrent_identical_requests_share_work_without_blocking_other_athletes(monkeypatch):
    started, waiting, release = Event(), Event(), Event()
    class ObservedFuture(Future):
        def result(self, timeout=None):
            waiting.set()
            return super().result(timeout)
    monkeypatch.setattr(cache_module, "Future", ObservedFuture)
    cache = SpeedLoadCache()
    calls = []
    def compute():
        calls.append(1); started.set()
        assert release.wait(5)
        return {"activities": [], "value": 42}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get_or_compute, "athlete-a", compute)
        assert started.wait(5)
        second = pool.submit(cache.get_or_compute, "athlete-a", compute)
        try:
            assert waiting.wait(5)
            assert cache.get_or_compute("athlete-b", lambda: {"activities": []}) == {"activities": []}
        finally: release.set()
        assert first.result(5) == second.result(5)
    assert len(calls) == 1
