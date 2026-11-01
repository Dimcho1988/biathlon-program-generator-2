"""Durable manual drafts: short HTTP acceptance, fenced worker publication.

The existing generator and authoritative draft writer remain responsible for
training rules and input freshness. A job never activates a program.
"""
import logging
from threading import Event, Thread
from uuid import uuid4

from fastapi import HTTPException

from . import management_service
from .management_schemas import ManagementGenerateRequest
from .oauth_store import PersistentStoreFailure, HistoricalGenerationReferenceConflict

logger = logging.getLogger(__name__)
STATUSES = frozenset({'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED'})
STAGES = frozenset({'QUEUED', 'PREPARING', 'BASELINE', 'SEARCH', 'SAVING', 'READY', 'FAILED'})


def public_job(value):
    if (not isinstance(value, dict) or value.get('status') not in STATUSES
            or value.get('stage') not in STAGES or not isinstance(value.get('job_id'), str)):
        raise PersistentStoreFailure('Invalid planning job state')
    fields = ('job_id', 'status', 'stage', 'start_date', 'requested_at', 'updated_at',
              'attempts', 'result_revision', 'error_code')
    return {key: value.get(key) for key in fields}


def enqueue(repository, alias, body, actor):
    result = repository.planning_job('ENQUEUE', alias=alias, actor=str(actor), request=body.model_dump(mode='json'))
    if isinstance(result, dict) and result.get('conflict'):
        raise HTTPException(409, 'Planning inputs changed or another draft is already being prepared')
    return {'job': public_job(result)}


def status(repository, alias, actor, job_id=None):
    value = repository.planning_job('STATUS', alias=alias, actor=str(actor),
                                    **({'job': str(job_id)} if job_id is not None else {}))
    if not isinstance(value, dict) or 'job' not in value:
        raise PersistentStoreFailure('Invalid planning job response')
    if job_id is not None and value['job'] is None:
        raise HTTPException(404, 'This planning request is no longer current')
    return {'job': public_job(value['job']) if value['job'] is not None else None}


def process_next(repository):
    """Process at most one job. SQL owns retries, leases and final publication."""
    token = str(uuid4())
    job = repository.planning_job('CLAIM', token=token)
    if job is None:
        return False
    if not isinstance(job, dict) or not all(job.get(k) for k in ('job_id', 'athlete_alias', 'actor_id', 'request')):
        raise PersistentStoreFailure('Invalid planning claim')
    job_id = job['job_id']
    stopped, lost = Event(), Event()
    last_stage = 'PREPARING'

    def renew(stage=None):
        state = repository.planning_job('RENEW', job=job_id, token=token,
                                        **({'stage': stage} if stage is not None else {}))
        if not isinstance(state, dict) or state.get('status') != 'RUNNING':
            lost.set()
            raise HTTPException(409, 'Planning job ownership changed')

    def heartbeat():
        while not stopped.wait(20.):
            try:
                renew()
            except Exception:
                lost.set()
                return

    def progress(stage):
        nonlocal last_stage
        if lost.is_set():
            raise HTTPException(409, 'Planning job ownership changed')
        if stage != last_stage:
            renew(stage)
            last_stage = stage

    thread = Thread(target=heartbeat, name='draft-job-lease', daemon=True)
    thread.start()
    try:
        body = ManagementGenerateRequest.model_validate(job['request'])
        payload = management_service.build_draft(repository, job['athlete_alias'], body.start_date,
            body.expected_profile_revision, on_progress=progress)
        if lost.is_set():
            raise HTTPException(409, 'Planning job ownership changed')
        renew('SAVING')
        state = repository.planning_job('COMPLETE', job=job_id, token=token, payload=payload)
        if not isinstance(state, dict) or state.get('status') not in {'SUCCEEDED', 'FAILED'}:
            raise PersistentStoreFailure('Planning publication could not be confirmed')
        logger.info('planning_job_finished job_id=%s status=%s', job_id, state['status'])
    except Exception as exc:
        code = ('INPUT_CHANGED' if isinstance(exc, HistoricalGenerationReferenceConflict)
                else 'TEMPORARILY_UNAVAILABLE' if isinstance(exc, PersistentStoreFailure)
                else 'INPUT_CHANGED' if isinstance(exc, HTTPException) and exc.status_code == 409
                else 'ACCESS_CHANGED' if isinstance(exc, HTTPException) and exc.status_code in {401, 403}
                else 'INVALID_INPUT' if isinstance(exc, (HTTPException, ValueError)) else 'COMPUTATION_FAILED')
        try:
            repository.planning_job('FAIL', job=job_id, token=token, stage=code)
        except Exception:
            # A vanished worker is retried after lease expiry; never publish unfenced.
            logger.warning('planning_job_failure_deferred job_id=%s', job_id)
        logger.warning('planning_job_deferred job_id=%s code=%s error_type=%s', job_id, code, type(exc).__name__)
    finally:
        stopped.set()
        thread.join(timeout=1.)
    return True
