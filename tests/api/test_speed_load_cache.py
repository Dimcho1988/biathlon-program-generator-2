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
    compute = model._compute_history
    computed = []
    monkeypatch.setattr(model, "_compute_history", lambda *args: computed.append(1) or compute(*args))
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
    computed_count = len(computed)
    # The same date range with a different sport must never reuse the all-sport report.
    model.history_view(repo, "authorized-athlete", "Run", today=TODAY)
    assert len(computed) > computed_count; computed_count = len(computed)
    assert len(calls) == count  # Identical activity Q can be shared across report filters.
    model.history_view(repo, "authorized-athlete", today=TODAY,
                       period_start=TODAY-timedelta(days=1), period_end=TODAY)
    assert len(computed) > computed_count; computed_count = len(computed)
    assert len(calls) == count
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
    assert len(computed) > computed_count; computed_count = len(computed)
    assert len(calls) == count  # A revision alone cannot change immutable activity inputs.
    # Cache namespaces separate databases even when aliases/generations coincide.
    repo.speed_load_cache_namespace += "-other-database"
    model.history_view(repo, "authorized-athlete", today=TODAY)
    assert len(calls) > count


def test_activity_cache_reuses_only_unchanged_causal_inputs_and_recomputes_downstream_load(monkeypatch):
    repo = Repository()
    repo.speed_load_cache_namespace = str(uuid4())
    for offset in (3, 2, 1):
        repo.add(f"run-{offset}", "Run", TODAY-timedelta(days=offset), 150, 15)
    original = repo.activity_speed_exposure_samples
    reads = []
    monkeypatch.setattr(repo, "activity_speed_exposure_samples", lambda alias, keys:
                        reads.extend(keys) or original(alias, keys))
    first = model.history_view(repo, "authorized-athlete", today=TODAY)
    assert len(reads) == 3
    # A new workout cannot change earlier workouts' prior calibration, but
    # it must change daily effective loads and current sport indices.
    repo.add("new", "Run", TODAY, 150, 15)
    updated = model.history_view(repo, "authorized-athlete", today=TODAY)
    assert reads[3:] == ["new"]
    assert updated["classified_minutes"] > first["classified_minutes"]
    uncached = SimpleNamespace(athlete_settings=repo.athlete_settings,
        active_trainability_calendar=repo.active_trainability_calendar,
        trainability_summaries=repo.trainability_summaries,
        activity_speed_exposure_samples=repo.activity_speed_exposure_samples)
    assert updated == model.history_view(uncached, "authorized-athlete", today=TODAY)
    reads.clear()
    # Corrected old TI changes later mappings, not its own mapping. Admission
    # and daily E are recomputed for the whole causal suffix on every miss.
    repo.summaries["run-3"]["trainability_index"]["general"]["index"] *= 1.05
    corrected = model.history_view(repo, "authorized-athlete", today=TODAY)
    assert set(reads) == {"run-2", "run-1", "new"}
    assert corrected == model.history_view(uncached, "authorized-athlete", today=TODAY)
    assert corrected["activities"][0] == updated["activities"][0]
    assert corrected["activities"][1]["mapping"] != updated["activities"][1]["mapping"]
    # A changed immutable run key forces fresh reading/integration even if the
    # metadata, generation, calendar date and calibration remain identical.
    reads.clear()
    activity = repo.activities[-1]
    activity["latest_shadow_run_key"] = "replacement"
    repo.shadows["replacement"] = deepcopy(repo.shadows["new"])
    repo.summaries["replacement"] = deepcopy(repo.summaries["new"])
    replacement = model.history_view(repo, "authorized-athlete", today=TODAY)
    assert reads == ["replacement"]
    assert replacement == corrected


def test_batched_activity_cache_bounds_entries_isolates_mutation_and_retries_missing_results():
    cache = SpeedLoadCache(max_entries=2)
    requested = []
    def compute(keys):
        requested.append(keys)
        return {key: {"reason": None, "value": [key]} for key in keys}
    first = cache.get_or_compute_many(["a", "b"], compute)
    first[0]["value"].append("mutated")
    assert cache.get_or_compute_many(["a", "b"], compute)[0]["value"] == ["a"]
    cache.get_or_compute_many(["a", "c"], compute)
    assert requested == [["a", "b"], ["c"]]
    cache.get_or_compute_many(["b"], compute)
    assert requested[-1] == ["b"]
    cache.get_or_compute_many(["missing"], lambda keys:
                              {key: {"reason": "SPEED_RECOMPUTATION_REQUIRED"} for key in keys})
    assert cache.get_or_compute_many(["missing"], compute)[0]["reason"] is None
    with pytest.raises(RuntimeError):
        cache.get_or_compute_many(["error"], lambda _: (_ for _ in ()).throw(RuntimeError("temporary")))
    assert cache.get_or_compute_many(["error"], compute)[0]["value"] == ["error"]


def test_missing_activity_does_not_disable_reuse_and_repair_invalidates_only_that_activity(monkeypatch):
    repo = Repository()
    repo.speed_load_cache_namespace = str(uuid4())
    repo.add("prior", "Run", TODAY-timedelta(days=1), 150, 15)
    repo.add("missing", "Run", TODAY, 150, 15)
    repo.activities[-1]["latest_shadow_run_key"] = None
    original = repo.activity_speed_exposure_samples
    reads = []
    monkeypatch.setattr(repo, "activity_speed_exposure_samples", lambda alias, keys:
                        reads.extend(keys) or original(alias, keys))
    first = model.history_view(repo, "authorized-athlete", today=TODAY)
    assert "SPEED_RECOMPUTATION_REQUIRED" in first["warnings"]
    assert reads == ["prior"]
    model.history_view(repo, "authorized-athlete", today=TODAY)
    assert reads == ["prior"]
    repo.activities[-1]["latest_shadow_run_key"] = "missing"
    repaired = model.history_view(repo, "authorized-athlete", today=TODAY)
    assert reads == ["prior", "missing"]
    assert "SPEED_RECOMPUTATION_REQUIRED" not in repaired["warnings"]


def test_unrelated_calendar_metadata_does_not_invalidate_activity_q(monkeypatch):
    repo = Repository()
    repo.speed_load_cache_namespace = str(uuid4())
    repo.add("prior", "Run", TODAY-timedelta(days=1), 150, 15)
    repo.add("current", "Run", TODAY, 150, 15)
    original = repo.activity_speed_exposure_samples
    reads = []
    monkeypatch.setattr(repo, "activity_speed_exposure_samples", lambda alias, keys:
                        reads.extend(keys) or original(alias, keys))
    first = model.history_view(repo, "authorized-athlete", today=TODAY)
    reads.clear()
    for activity in repo.activities:
        activity.update(name="Renamed workout", canonical_summary={"tref": "changed"},
                        hrmod_zone_summary={"minutes": 99}, provider_updated_at="later")
    assert model.history_view(repo, "authorized-athlete", today=TODAY) == first
    assert not reads


def test_overlapping_periods_share_activity_work_without_deadlock(monkeypatch):
    started, waiting, release = Event(), Event(), Event()
    class ObservedFuture(Future):
        def result(self, timeout=None):
            if not self.done():
                waiting.set()
            return super().result(timeout)
    monkeypatch.setattr(cache_module, "Future", ObservedFuture)
    cache = SpeedLoadCache()
    computed = []
    def compute_first(keys):
        computed.extend(keys); started.set()
        assert release.wait(5)
        return {key: {"value": key} for key in keys}
    def compute_second(keys):
        computed.extend(keys)
        return {key: {"value": key} for key in keys}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get_or_compute_many, ["a", "b"], compute_first)
        assert started.wait(5)
        second = pool.submit(cache.get_or_compute_many, ["b", "c"], compute_second)
        try:
            assert waiting.wait(5)
            assert cache.get_or_compute_many(["other-athlete"], compute_second) == [{"value": "other-athlete"}]
        finally:
            release.set()
        assert first.result(5) == [{"value": "a"}, {"value": "b"}]
        assert second.result(5) == [{"value": "b"}, {"value": "c"}]
    assert computed.count("b") == 1


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
