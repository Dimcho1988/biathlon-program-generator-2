"""Bounded, process-local reuse of immutable speed-load inputs.

Callers must authorize and reread the current settings/calendar before lookup.
Concurrent requests for the same inputs share work; exceptions are never cached.
"""
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from threading import Lock
from time import monotonic


class SpeedLoadCache:
    def __init__(self, max_entries=16, ttl_seconds=300, clock=monotonic):
        self._entries = OrderedDict()
        self._pending = {}
        self._lock = Lock()
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._clock = clock

    def get_or_compute(self, key, compute, *, stats=None):
        with self._lock:
            now = self._clock()
            for expired in [k for k, (until, _) in self._entries.items() if until <= now]:
                del self._entries[expired]
            if key in self._entries:
                if stats is not None:
                    stats["report_cache"] = "hit"
                self._entries.move_to_end(key)
                return deepcopy(self._entries[key][1])
            future = self._pending.get(key)
            owner = future is None
            if owner:
                future = self._pending[key] = Future()
            if stats is not None:
                stats["report_cache"] = "miss" if owner else "shared"
        if not owner:
            return deepcopy(future.result())
        try:
            result = compute()
            # Missing old/full-resolution runs may be repaired without a new
            # generation. Never retain a result that needs such a repair.
            cacheable = ("SPEED_RECOMPUTATION_REQUIRED" not in result.get("warnings", [])
                         and not any(a.get("reason") == "SPEED_RECOMPUTATION_REQUIRED"
                                     for a in result.get("activities", [])))
            with self._lock:
                if cacheable:
                    self._entries[key] = (self._clock() + self._ttl, deepcopy(result))
                    while len(self._entries) > self._max_entries:
                        self._entries.popitem(last=False)
                del self._pending[key]
                future.set_result(result)
            return deepcopy(result)
        except BaseException as error:
            with self._lock:
                self._pending.pop(key, None)
                future.set_exception(error)
            raise

    def get_or_compute_many(self, keys, compute, *, stats=None):
        """Reuse small activity results while batching only missing source reads.

        Reserve every missing key atomically, then finish owned work before
        waiting on other requests. Overlapping report periods cannot deadlock
        or duplicate the same activity integration. The callback returns one
        result per owned key, and must never mutate already cached results.
        """
        values, futures, owned = {}, {}, []
        with self._lock:
            now = self._clock()
            for expired in [k for k, (until, _) in self._entries.items() if until <= now]:
                del self._entries[expired]
            for key in dict.fromkeys(keys):
                if key in self._entries:
                    self._entries.move_to_end(key)
                    values[key] = self._entries[key][1]
                else:
                    future = self._pending.get(key)
                    if future is None:
                        future = self._pending[key] = Future()
                        owned.append(key)
                    futures[key] = future
        if stats is not None:
            stats["activity_cache_hits"] = len(values)
            stats["activity_cache_misses"] = len(owned)
            stats["activity_cache_waits"] = len(futures)-len(owned)
        if owned:
            try:
                results = compute(owned)
                if set(results) != set(owned):
                    raise ValueError("Missing activity cache results")
                # Copy before publishing; callers only receive separate copies.
                retained = {key: deepcopy(result) for key, result in results.items()
                            if result.get("reason") != "SPEED_RECOMPUTATION_REQUIRED"}
                with self._lock:
                    for key in owned:
                        if key in retained:
                            self._entries[key] = (self._clock() + self._ttl, retained[key])
                        del self._pending[key]
                        futures[key].set_result(results[key])
                    while len(self._entries) > self._max_entries:
                        self._entries.popitem(last=False)
            except BaseException as error:
                with self._lock:
                    for key in owned:
                        self._pending.pop(key, None)
                        futures[key].set_exception(error)
                raise
        values.update({key: future.result() for key, future in futures.items()})
        return [deepcopy(values[key]) for key in keys]


speed_load_cache = SpeedLoadCache()
# Only small Q summaries and calibrated mappings are retained, never raw
# samples. Source run keys and the full causal mapping are part of every key.
speed_activity_cache = SpeedLoadCache(max_entries=2048, ttl_seconds=1800)
