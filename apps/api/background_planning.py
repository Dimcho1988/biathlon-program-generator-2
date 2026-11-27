"""One drainable planning consumer alongside the existing import queue."""
import logging
from threading import Thread
from time import monotonic
from functools import partial

logger = logging.getLogger(__name__)

# Drain several athletes/pages before accepting minutes of planning work.
# Both limits keep continuous input changes from starving the planning queue.
PREPARATION_SECONDS = 120.
PREPARATION_JOBS = 32


def prepare_pending(repository, stopping, *, requested_first=False):
    """Prepare a bounded batch, round-robin across the independent queues.

    Calls stay serial and retain each processor's model-memory lock and SQL
    lease/publication fences. A time budget is a soft boundary between jobs;
    an in-flight calculation always finishes rather than losing its work.
    """
    priority = requested_first and callable(getattr(repository, 'prepared_view', None))
    processors = [(kind, getattr(repository, name, None)) for kind, name in (
        ('dashboard', 'process_next_prepared_dashboard'),
        ('learning', 'process_next_prepared_learning'),
        ('prepared_view', 'process_next_prepared_view'),
    )]
    if priority:
        from .prepared_views import process_next
        processors[-1] = ('prepared_view', partial(process_next, repository, demand=True))
    processors = [(kind, call) for kind, call in processors if callable(call)]
    started = monotonic()
    completed = 0
    failed = set()
    # Reserve one place for prewarming, so a busy planning queue still makes
    # progress on unopened pages. Old repository adapters retain their policy.
    limit = max(0, PREPARATION_JOBS - 1) if priority else PREPARATION_JOBS
    while not stopping.is_set():
        worked = False
        for kind, call in processors:
            if stopping.is_set():
                return completed
            if completed >= limit:
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
        if (not worked or completed >= limit
                or monotonic() - started >= PREPARATION_SECONDS):
            break
    if priority and not stopping.is_set() and monotonic()-started < PREPARATION_SECONDS:
        try:
            background_worked = bool(process_next(repository, demand=False))
            completed += background_worked
            # Do not leave an opened page behind solely because the reserved
            # background lane is empty. The combined cap remains 32 jobs.
            if not background_worked and completed == limit:
                for kind, call in processors:
                    if stopping.is_set() or monotonic()-started >= PREPARATION_SECONDS:
                        break
                    if kind not in failed and call():
                        completed += 1
                        break
        except Exception as exc:
            logger.warning('background_view_tick_deferred error_type=%s', type(exc).__name__)
    if completed:
        logger.info('prepared_batch jobs=%s seconds=%.3f lane=%s',
                    completed, monotonic()-started, 'requested' if priority else 'all')
    return completed


def start(repository, stopping):
    processor = getattr(repository, 'process_next_planning_job', None)
    if not callable(processor):
        return None

    def run():
        next_automatic, next_preparation = 0., 0.
        planning_worked = False
        preparation_drained = False
        while not stopping.is_set():
            prepared = 0
            if not preparation_drained and (not planning_worked or monotonic() >= next_preparation):
                prepared = prepare_pending(repository, stopping, requested_first=True)
                next_preparation = monotonic()+15.
            preparation_drained = False
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
            if (not planning_worked and not stopping.is_set()
                    and callable(getattr(repository, 'prepared_view', None))):
                # No claimed programme: use the spare lane to drain prewarming
                # normally, still bounded so a newly arriving plan gets a turn.
                prepared += prepare_pending(repository, stopping)
                # Check planning immediately after this batch. A plan arriving
                # during idle prewarming must not wait through a second batch.
                preparation_drained = True
            stopping.wait(0.1 if planning_worked or prepared else 3.)

    # Non-daemon: SIGTERM stops new claims while a fenced in-flight draft drains.
    thread = Thread(target=run, name='planning-queue', daemon=False)
    thread.start()
    return thread
