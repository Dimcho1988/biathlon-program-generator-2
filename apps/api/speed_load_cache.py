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

    def get_or_compute(self, key, compute):
        with self._lock:
            now = self._clock()
            for expired in [k for k, (until, _) in self._entries.items() if until <= now]:
                del self._entries[expired]
            if key in self._entries:
                self._entries.move_to_end(key)
                return deepcopy(self._entries[key][1])
            future = self._pending.get(key)
            owner = future is None
            if owner:
                future = self._pending[key] = Future()
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


speed_load_cache = SpeedLoadCache()
