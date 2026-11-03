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
        next_automatic, next_dashboard, next_learning = 0., 0., 0.
        while not stopping.is_set():
            try:
                worked = processor()
            except Exception as exc:
                worked = False
                logger.warning('planning_queue_deferred error_type=%s', type(exc).__name__)
            if stopping.is_set():
                break
            if not worked and monotonic() >= next_automatic:
                next_automatic = monotonic()+15.
                try:
                    repository.refresh_due_training_plans()
                except Exception as exc:
                    logger.warning('management_tick_deferred error_type=%s', type(exc).__name__)
            warmer = getattr(repository, 'process_next_prepared_dashboard', None)
            if not stopping.is_set() and not worked and callable(warmer) and monotonic() >= next_dashboard:
                next_dashboard = monotonic()+15.
                try:
                    warmer()
                except Exception as exc:
                    logger.warning('dashboard_tick_deferred error_type=%s', type(exc).__name__)
            learner = getattr(repository, 'process_next_prepared_learning', None)
            if not stopping.is_set() and callable(learner) and monotonic() >= next_learning:
                # Give read-model preparation a bounded turn even with a busy
                # planning queue; the same memory lane serializes the work.
                next_learning = monotonic()+15.
                try:
                    learner()
                except Exception as exc:
                    logger.warning('learning_tick_deferred error_type=%s', type(exc).__name__)
            stopping.wait(0.1 if worked else 3.)

    # Non-daemon: SIGTERM stops new claims while a fenced in-flight draft drains.
    thread = Thread(target=run, name='planning-queue', daemon=False)
    thread.start()
    return thread
