"""One drainable planning consumer alongside the existing import queue."""
import logging
from threading import Thread
from time import monotonic

logger = logging.getLogger(__name__)


def start(repository, stopping):
    processor = getattr(repository, 'process_next_planning_job', None)
    if not callable(processor):
        return None

    def run():
        next_automatic = 0.
        while not stopping.is_set():
            try:
                worked = processor()
            except Exception as exc:
                worked = False
                logger.warning('planning_queue_deferred error_type=%s', type(exc).__name__)
            if stopping.is_set():
                break
            if monotonic() >= next_automatic:
                next_automatic = monotonic()+60.
                try:
                    repository.refresh_due_training_plans()
                except Exception as exc:
                    logger.warning('management_tick_deferred error_type=%s', type(exc).__name__)
            stopping.wait(0.1 if worked else 3.)

    # Non-daemon: SIGTERM stops new claims while a fenced in-flight draft drains.
    thread = Thread(target=run, name='planning-queue', daemon=False)
    thread.start()
    return thread
