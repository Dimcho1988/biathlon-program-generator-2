"""Prepared dashboard reads and training-triggered planning on one model lane.

The caches contain derived data, never access decisions. SQL invalidation,
local-day checks, leases and publication fences own freshness across processes.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
import os
from threading import Event, Thread
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .oauth_store import PersistentStoreFailure

logger = logging.getLogger(__name__)
VERSION = "prepared-dashboard-v1-training-events-v1"


def model_key():
    from . import model_service
    return sha256(json.dumps({"version": VERSION, "build": os.getenv("RENDER_GIT_COMMIT", VERSION),
        "recovery_v2": model_service.enabled()}, sort_keys=True).encode()).hexdigest()


def _validate_envelope(value, alias):
    from .planning_input_bundle import parse_analysis
    from .schemas import AthleteSnapshot
    envelope = parse_analysis(value)
    if envelope is None:
        raise ValueError("No active analysis is available")
    snapshot = AthleteSnapshot.model_validate(envelope["snapshot_payload"])
    if snapshot.training_status.athlete_id != alias or snapshot.load_history.athlete_id != alias:
        raise ValueError("Prepared dashboard belongs to another athlete")
    return envelope


def compute_dashboard(repository, alias):
    from . import model_service
    from .read_session import ReadSession
    phase = ReadSession(repository)
    envelope = phase.active_analysis(alias)
    if not isinstance(envelope, dict) or not isinstance(envelope.get("snapshot_payload"), dict):
        raise ValueError("No active analysis is available")
    payload = model_service.project_recovery(phase, alias, envelope["snapshot_payload"])
    return _validate_envelope({**envelope, "snapshot_payload": payload}, alias)


def dashboard_envelope(repository, alias):
    """Only read the worker-prepared result; a cold HTTP read never simulates."""
    operation = getattr(repository, "dashboard_preparation", None)
    if not callable(operation):
        # Offline adapters have no worker. Persistent repositories always expose
        # this operation, including when its database transport is unavailable.
        return compute_dashboard(repository, alias)
    from .prepared_views import pending
    try:
        state = operation("READ", alias=alias, model_key=model_key())
        if isinstance(state, dict) and state.get("ready") is True:
            return deepcopy(_validate_envelope(state.get("envelope"), alias))
    except (PersistentStoreFailure, TypeError, ValueError) as exc:
        logger.warning("prepared_dashboard state=read_deferred error_type=%s", type(exc).__name__)
    raise pending()


@contextmanager
def _lease(operation, alias, token):
    stopped, lost = Event(), Event()

    def heartbeat():
        while not stopped.wait(20.):
            try:
                response = operation("RENEW", alias=alias, token=token)
                if not isinstance(response, dict) or response.get("state") != "RUNNING":
                    lost.set()
                    return
            except Exception:
                lost.set()
                return

    thread = Thread(target=heartbeat, name="prepared-model-lease", daemon=True)
    thread.start()
    try:
        yield lost
    finally:
        stopped.set()
        thread.join(timeout=1.)


def process_dashboard(repository, alias=None):
    from .memory_budget import MODEL_WORK
    operation = repository.dashboard_preparation
    key, token = model_key(), str(uuid4())
    with MODEL_WORK:
        claim = operation("CLAIM", model_key=key, token=token, **({"alias": alias} if alias else {}))
        if claim is None:
            return False
        if (not isinstance(claim, dict) or not isinstance(claim.get("athlete_alias"), str)
                or type(claim.get("revision")) is not int or not isinstance(claim.get("day"), str)):
            raise PersistentStoreFailure("Invalid dashboard preparation claim")
        alias = claim["athlete_alias"]
        try:
            with _lease(operation, alias, token) as lost:
                envelope = compute_dashboard(repository, alias)
                if lost.is_set():
                    raise HTTPException(409, "Dashboard preparation lease changed")
                saved = operation("STORE", alias=alias, token=token, revision=claim["revision"],
                    day=claim["day"], model_key=key, envelope=envelope)
                logger.info("prepared_dashboard state=%s", "prepared" if isinstance(saved, dict)
                    and saved.get("stored") is True else "superseded")
                cleanup = getattr(repository, "cleanup_prepared_data", None)
                if isinstance(saved, dict) and saved.get("stored") is True and callable(cleanup):
                    try:
                        cleanup(alias)
                    except Exception as exc:
                        logger.warning("prepared_cleanup state=deferred error_type=%s", type(exc).__name__)
        except Exception as exc:
            try:
                operation("FAIL", alias=alias, token=token)
            except Exception:
                pass
            logger.warning("prepared_dashboard state=deferred error_type=%s", type(exc).__name__)
    return True


def process_training(repository):
    """Coalesced new-training, local-day and explicit manual preparation."""
    from . import management_lifecycle, planning_jobs
    from .management_plan_store import PlanStore
    from .management_store import ManagementStore
    from .management_schemas import ManagementGenerateRequest
    from .memory_budget import MODEL_WORK
    operation = repository.training_plan_update
    token = str(uuid4())
    with MODEL_WORK:
        claim = operation("CLAIM", token=token)
        if claim is None:
            return False
        if (not isinstance(claim, dict) or not isinstance(claim.get("athlete_alias"), str)
                or not isinstance(claim.get("actor_id"), str) or type(claim.get("revision")) is not int):
            raise PersistentStoreFailure("Invalid training update claim")
        alias = claim["athlete_alias"]
        try:
            with _lease(operation, alias, token) as lost:
                record = PlanStore(repository).current(alias, metadata_only=True)
                if record is not None:
                    manual = claim.get("manual") is True
                    if manual and claim.get("expected_plan_revision") != record["revision"]:
                        raise HTTPException(409, "The queued plan revision changed")
                    management_lifecycle.refresh(repository, alias, claim["actor_id"],
                        expected_revision=record["revision"], automatic=not manual, force=manual,
                        include_saved_payload=False, record_unchanged_check=claim.get("record_unchanged_check") is True,
                        _background_claim={"token": token, "revision": claim["revision"], **({"manual":True} if manual else {})})
                else:
                    stored = ManagementStore(repository).profile(alias)
                    if stored["configured"]:
                        profile = stored["profile"]
                        settings = repository.athlete_settings(alias)
                        today = datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone)).date()
                        start = max(today, date.fromisoformat(profile["program_start"]))
                        if start <= min(today+timedelta(days=7), date.fromisoformat(profile["program_end"])):
                            draft = ManagementStore(repository).draft_metadata(alias, start)
                            body = ManagementGenerateRequest(start_date=start,
                                expected_profile_revision=stored["revision"], expected_draft_revision=draft["revision"])
                            planning_jobs.enqueue(repository, alias, body, claim["actor_id"])
                if lost.is_set():
                    raise HTTPException(409, "Training update lease changed")
                operation("COMPLETE", alias=alias, token=token)
                logger.info("training_plan_update state=prepared")
        except Exception as exc:
            try:
                operation("REJECT" if claim.get("manual") and isinstance(exc, HTTPException) and exc.status_code == 409 else "FAIL", alias=alias, token=token)
            except Exception:
                pass
            logger.warning("training_plan_update state=deferred error_type=%s", type(exc).__name__)
    return True


def queue_due_imports(repository, *, now=None):
    """Keep existing opt-in provider polling separate from plan calculation.

    The SQL import RPC retains its connection, grant and six-hour cooldown
    checks. Enqueueing a sync never directly regenerates a program.
    """
    from .management_plan_store import PlanStore
    store = PlanStore(repository)
    rows = store.due(now or datetime.now(timezone.utc))[:1]
    for row in rows:
        try:
            store.queue_import(row["athlete_alias"])
        except Exception as exc:
            logger.warning("training_import state=deferred error_type=%s", type(exc).__name__)
        finally:
            store.defer_check(row["athlete_alias"], row["revision"])
    return len(rows)
