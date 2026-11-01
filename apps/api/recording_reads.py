"""Consume pinned recordings without retaining decoded histories in a read phase."""
from threading import RLock

from .request_metrics import timed_stage

_RECORDINGS = RLock()


def iter_samples(repository, alias, run_keys, *, include_raw=True):
    # Distinct athletes must not expand large recordings concurrently in the
    # same 512 MiB API process. Ordinary summary/page reads use no such lane.
    with timed_stage("recording_wait"):
        _RECORDINGS.acquire()
    try:
        yield from _iter_samples(repository, alias, run_keys, include_raw=include_raw)
    finally:
        _RECORDINGS.release()


def _iter_samples(repository, alias, run_keys, *, include_raw):
    name = "activity_speed_history_samples" if include_raw else "activity_speed_exposure_samples"
    streaming = getattr(repository, "iter_" + name, None)
    if callable(streaming):
        yield from streaming(alias, run_keys)
        return
    reader = getattr(repository, name, None)
    for offset in range(0, len(run_keys), 10):
        batch = run_keys[offset:offset + 10]
        rows = reader(alias, batch) if callable(reader) else {}
        for key in batch:
            yield key, rows.get(key) or {}
        del rows
