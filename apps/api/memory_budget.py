"""One reentrant model-work lane per process, shared by imports and planning.

The worker intentionally runs planning alongside its queue loop. They must not
expand independent histories simultaneously inside a 512 MiB process. Nested
history reads from a plan stay in the same lane; database lease heartbeats and
ordinary summary requests do not take it.
"""
from contextlib import contextmanager
from functools import wraps
from threading import RLock, local


class ModelWorkLane:
    def __init__(self):
        self._lock = RLock()
        self._thread = local()

    def acquire(self, *args, **kwargs):
        acquired = self._lock.acquire(*args, **kwargs)
        if acquired:
            self._thread.depth = getattr(self._thread, "depth", 0) + 1
        return acquired

    def release(self):
        self._lock.release()
        self._thread.depth -= 1

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()

    @contextmanager
    def while_waiting(self):
        """Let a cache owner finish when a plan is waiting for its result.

        Holding this lane while awaiting an owner that needs the same lane
        would deadlock. No calculations run in the waiting thread. Restore
        every nested acquisition before it can resume model work.
        """
        depth = getattr(self._thread, "depth", 0)
        for _ in range(depth):
            self.release()
        try:
            yield
        finally:
            for _ in range(depth):
                self.acquire()


MODEL_WORK = ModelWorkLane()


def wait_for_model_result(future):
    if future.done():
        return future.result()
    with MODEL_WORK.while_waiting():
        return future.result()


def serialized_model_work(calculate):
    @wraps(calculate)
    def run(*args, **kwargs):
        with MODEL_WORK:
            return calculate(*args, **kwargs)
    return run
