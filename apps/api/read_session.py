"""Deduplicate reads while assembling one management page.

This object lives for one read request only. It must never be used for writes:
write services intentionally reread inputs to detect concurrent changes. Keys
include all arguments (including the athlete). No user data is cached globally.
"""
from copy import deepcopy
from functools import wraps


class ReadSession:
    _READS = frozenset({
        "athlete_settings", "active_analysis", "athlete_planning_calendar",
        "athlete_planning_profile", "athlete_mesocycle_accent_preferences",
        "active_trainability_calendar", "active_planning_calendar",
        "trainability_summaries", "sync_state",
    })

    def __init__(self, repository):
        self._repository = repository
        self._values = {}

    def _read(self, key, reader):
        if key not in self._values:
            self._values[key] = reader()
        return deepcopy(self._values[key])

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
        if name not in self._READS:
            raise AttributeError(name)
        reader = getattr(self._repository, name)

        @wraps(reader)
        def read(*args):
            return self._read((name, *args), lambda: reader(*args))

        return read
