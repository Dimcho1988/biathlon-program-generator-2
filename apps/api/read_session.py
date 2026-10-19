"""Deduplicate reads while assembling one management page.

This object lives for one read phase only. It must never be used for writes or
reused across calculation/publication boundaries. Final concurrency checks start
a new read phase through the live repository after computing a draft. Keys
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
        self._planning_inputs = {}

    def capture_planning_inputs(self, alias, *, analysis_hints=None):
        """Capture one coherent phase; this never crosses a computation boundary."""
        if alias not in self._planning_inputs:
            reader = getattr(self._repository, "planning_inputs_bundle", None)
            self._planning_inputs[alias] = (reader(alias,
                analysis_mode="FULL" if analysis_hints is None else "HINTED", analysis_hints=analysis_hints)
                if callable(reader) else None)
        return self._planning_inputs[alias]

    def planning_inputs_bundle(self, alias, *, analysis_mode="FULL", analysis_hints=None):
        # Nested page/context scopes share this phase, while callers using the
        # raw live repository after computing create a genuinely fresh capture.
        return self.capture_planning_inputs(alias,
            analysis_hints=None if analysis_mode == "FULL" else analysis_hints or [])

    def planning_profile_rows(self, alias):
        bundle = self._planning_inputs.get(alias)
        return bundle.profile_rows() if bundle is not None else None

    def planning_model_entries(self, alias):
        bundle = self._planning_inputs.get(alias)
        return bundle.model_entries() if bundle is not None else None

    def planning_response_entries(self, alias):
        bundle = self._planning_inputs.get(alias)
        return bundle.response_entries() if bundle is not None else None

    @classmethod
    def planning(cls, repository, initial=None):
        """Select one initial phase without changing the live repository.

        Only the caller's freshly captured phase may be reused before draft
        calculation. Publication checks must call this without ``initial``.
        Athlete isolation remains in the existing per-argument cache keys.
        """
        if initial is None:
            return cls(repository, planning_settings_bundle=True)
        if (not isinstance(initial, cls) or initial._repository is not repository
                or not initial._planning_settings_bundle):
            raise ValueError("Initial planning phase belongs to another repository or configuration")
        return initial

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
                bundle = self._planning_inputs.get(args[0]) if len(args) == 1 else None
                if bundle is not None:
                    if name in self._SETTINGS_READS:
                        return getattr(bundle.settings_bundle(), name)()
                    if name in {"active_analysis", "active_analysis_metadata"}:
                        return getattr(bundle, name)()
                    if name == "athlete_planning_settings_bundle":
                        return bundle.settings_bundle()
                if name == "active_trainability_calendar" and len(args) == 3:
                    # A bound repository method would otherwise reread analysis
                    # internally, bypassing this phase's existing capture.
                    analysis = self._values.get(("active_analysis", args[0]))
                    if (isinstance(analysis, dict)
                            and isinstance(analysis.get("generation_id"), str) and analysis["generation_id"]
                            and type(analysis.get("revision")) is int and analysis["revision"] > 0
                            and isinstance(analysis.get("snapshot_payload"), dict)):
                        calendar_reader = getattr(self._repository, "trainability_calendar_from_analysis", None)
                        if callable(calendar_reader):
                            return calendar_reader(*args, deepcopy(analysis))
                if self._planning_settings_bundle and name in self._SETTINGS_READS:
                    bundle_reader = getattr(self._repository, "athlete_planning_settings_bundle", None)
                    if callable(bundle_reader):
                        bundle = self._read(("athlete_planning_settings_bundle", *args), lambda: bundle_reader(*args))
                        return getattr(bundle, name)()
                return reader(*args)

            return self._read((name, *args), calculate)

        return read
