"""Deduplicate reads while assembling one management page.

This object lives for one read phase only. It must never be used for writes or
the final concurrency check: write services reread inputs through the original
repository after computing a draft. Keys
include all arguments (including the athlete). No user data is cached globally.
"""
from copy import deepcopy
from functools import wraps


class ReadSession:
    _SETTINGS_READS = frozenset({
        "athlete_settings", "athlete_planning_calendar",
        "athlete_planning_profile", "athlete_mesocycle_accent_preferences",
    })
    _READS = frozenset({
        "athlete_settings", "active_analysis", "active_analysis_metadata", "athlete_planning_calendar",
        "athlete_planning_profile", "athlete_mesocycle_accent_preferences",
        "athlete_planning_settings_bundle",
        "active_trainability_calendar", "active_planning_calendar", "active_activity_calendar",
        "trainability_summaries", "sync_state",
    })

    def __init__(self, repository, *, planning_settings_bundle=False):
        self._repository = repository
        self._planning_settings_bundle = planning_settings_bundle
        self._values = {}

    def _read(self, key, reader):
        if key not in self._values:
            self._values[key] = reader()
        return deepcopy(self._values[key])

    def memoize(self, key, calculate):
        """Reuse derived values only inside this immutable read phase."""
        return self._read(("derived", key), calculate)

    def _request(self, method, path, **kwargs):
        if method != "GET" or kwargs:
            raise ValueError("ReadSession only accepts simple GET store reads")
        key = ("GET", path)
        if key not in self._values:
            self._values[key] = self._repository._request(method, path)
        return self._values[key]

    def _json(self, value):
        return self._read(("json", id(value)), lambda: self._repository._json(value))

    def __getattr__(self, name):
        if name in {"speed_load_cache_namespace", "activity_speed_exposure_samples"}:
            # Raw streams must never accumulate in the request snapshot.
            return getattr(self._repository, name)
        if name not in self._READS:
            raise AttributeError(name)
        reader = getattr(self._repository, name)

        @wraps(reader)
        def read(*args):
            def calculate():
                if self._planning_settings_bundle and name in self._SETTINGS_READS:
                    bundle_reader = getattr(self._repository, "athlete_planning_settings_bundle", None)
                    if callable(bundle_reader):
                        bundle = self._read(("athlete_planning_settings_bundle", *args), lambda: bundle_reader(*args))
                        return getattr(bundle, name)()
                return reader(*args)

            return self._read((name, *args), calculate)

        return read
