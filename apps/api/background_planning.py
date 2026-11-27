"""One drainable planning consumer alongside the existing import queue."""
import logging
from threading import Thread
from time import monotonic

logger = logging.getLogger(__name__)

# Drain several athletes/pages before accepting minutes of planning work.
# Both limits keep continuous input changes from starving the planning queue.
PREPARATION_SECONDS = 120.
PREPARATION_JOBS = 32


def prepare_pending(repository, stopping):
    """Prepare a bounded batch, round-robin across the independent queues.

    Calls stay serial and retain each processor's model-memory lock and SQL
    lease/publication fences. A time budget is a soft boundary between jobs;
    an in-flight calculation always finishes rather than losing its work.
    """
    processors = [(kind, getattr(repository, name, None)) for kind, name in (
        ('dashboard', 'process_next_prepared_dashboard'),
        ('learning', 'process_next_prepared_learning'),
        ('prepared_view', 'process_next_prepared_view'),
    )]
    processors = [(kind, call) for kind, call in processors if callable(call)]
    started = monotonic()
    completed = 0
    failed = set()
    while not stopping.is_set():
        worked = False
        for kind, call in processors:
            if stopping.is_set():
                return completed
            if completed >= PREPARATION_JOBS:
                break
            if kind in failed:
                continue
            try:
                if call():
                    completed += 1
                    worked = True
            except Exception as exc:
                # Do not repeatedly call a broken queue during this batch.
                failed.add(kind)
                logger.warning('%s_tick_deferred error_type=%s', kind, type(exc).__name__)
        if (not worked or completed >= PREPARATION_JOBS
                or monotonic() - started >= PREPARATION_SECONDS):
            break
    if completed:
        logger.info('prepared_batch jobs=%s seconds=%.3f', completed, monotonic()-started)
    return completed


def start(repository, stopping):
    processor = getattr(repository, 'process_next_planning_job', None)
    if not callable(processor):
        return None

    def run():
        next_automatic, next_preparation = 0., 0.
        planning_worked = False
        while not stopping.is_set():
            prepared = 0
            if not planning_worked or monotonic() >= next_preparation:
                prepared = prepare_pending(repository, stopping)
                next_preparation = monotonic()+15.
            if stopping.is_set():
                break
            try:
                planning_worked = bool(processor())
            except Exception as exc:
                planning_worked = False
                logger.warning('planning_queue_deferred error_type=%s', type(exc).__name__)
            if stopping.is_set():
                break
            if not planning_worked and monotonic() >= next_automatic:
                next_automatic = monotonic()+15.
                try:
                    planning_worked = bool(repository.refresh_due_training_plans())
                except Exception as exc:
                    logger.warning('management_tick_deferred error_type=%s', type(exc).__name__)
            stopping.wait(0.1 if planning_worked or prepared else 3.)

    # Non-daemon: SIGTERM stops new claims while a fenced in-flight draft drains.
    thread = Thread(target=run, name='planning-queue', daemon=False)
    thread.start()
    return thread
