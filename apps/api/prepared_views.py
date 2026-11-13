"""Durable, worker-owned page projections. HTTP reads never run a model.

The original view functions remain the only calculation implementations. A
private worker context calls them against one read phase; the HTTP boundary
reads a fenced database result instead. Authentication is never cached.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import date, datetime
from functools import wraps
from inspect import signature
import logging
from uuid import uuid4

from fastapi import HTTPException, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from . import prepared_view_storage
from .oauth_store import PersistentStoreFailure

VERSION = "prepared-pages-v1"
PREPARING = "Данните се подготвят във фонов режим. Показването се обновява автоматично."
SAVED_MANAGEMENT_VIEWS = frozenset({"management-view", "management-active"})
_worker_repository = ContextVar("prepared_view_worker_repository", default=None)
_registry = {}
logger = logging.getLogger(__name__)


def worker_repository():
    return _worker_repository.get()


def model_key():
    from . import model_service, preparation_identity
    return preparation_identity.model_key("pages", VERSION, recovery_v2=model_service.enabled())


def pending():
    return HTTPException(503, PREPARING, headers={"Retry-After": "3",
        "Cache-Control": "no-store", "X-OnFlows-Preparation": "pending"})


def _preparation(state, *, saved):
    """Validate persisted provenance without inventing a new preparation time."""
    value = state.get("preparation")
    if value is None and not saved:
        # Older database versions have no READY metadata during rolling deploys.
        return None
    if not isinstance(value, dict) or value.get("status") != ("UPDATING" if saved else "READY"):
        raise ValueError("Invalid preparation state")
    prepared_at, evaluated_on = value.get("prepared_at"), value.get("evaluated_on")
    if not isinstance(prepared_at, str) or not isinstance(evaluated_on, str):
        raise ValueError("Missing preparation provenance")
    timestamp = datetime.fromisoformat(prepared_at.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or date.fromisoformat(evaluated_on).isoformat() != evaluated_on:
        raise ValueError("Invalid preparation date")
    reason = value.get("reason")
    if (saved and reason not in {"INPUTS_CHANGED", "DAY_CHANGED", "MODEL_CHANGED", "PREPARATION_PENDING"}
            or not saved and reason is not None):
        raise ValueError("Invalid preparation reason")
    return {"status": value["status"], "prepared_at": prepared_at,
            "evaluated_on": evaluated_on, "reason": reason}


def read(repository, alias, kind, parameters=None):
    operation = getattr(repository, "prepared_view", None)
    if not callable(operation):
        raise pending()
    try:
        state = operation("READ", alias=alias, kind=kind,
            parameters=parameters or {}, model_key=model_key())
        if isinstance(state, dict) and state.get("limited") is True:
            raise HTTPException(429, "Подготвят се много изгледи. Опитай отново след малко.", headers={"Retry-After":"15"})
        saved = (isinstance(state, dict) and state.get("ready") is not True and state.get("saved") is True
                 and kind in SAVED_MANAGEMENT_VIEWS)
        if not isinstance(state, dict) or (state.get("ready") is not True and not saved):
            raise pending()
        if state.get("athlete_alias") != alias or state.get("kind") != kind:
            raise ValueError("Prepared view scope differs")
        payload = state.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Invalid prepared view payload")
        payload = prepared_view_storage.unpack(payload)
        error = payload.get("prepared_error")
        if isinstance(error, dict):
            # An older failure is not a usable saved program. Keep the worker
            # refresh visible rather than presenting that failure as current.
            if saved:
                raise pending()
            status = error.get("status")
            if type(status) is not int or not 400 <= status < 500:
                raise ValueError("Invalid prepared view error")
            raise HTTPException(status, error.get("detail", "Данните изискват преглед."))
        if kind in SAVED_MANAGEMENT_VIEWS:
            from .management_projection import public_management
            # Apply today's presentation policy even to ready cached pages. Their
            # immutable scientific evidence stays in storage; obsolete workflow
            # hints must not resurrect approval controls during a refresh.
            payload = public_management(payload)
            if kind == "management-active":
                payload["capabilities"] = {**(payload.get("capabilities") or {}), "automatic_publication": True}
            elif isinstance(payload.get("active"), dict):
                active = payload["active"]
                active["capabilities"] = {**(active.get("capabilities") or {}), "automatic_publication": True}
            elif payload.get("schema_version") == "management-overview-v1":
                payload["capabilities"] = {**(payload.get("capabilities") or {}), "automatic_publication": True}
        if kind in SAVED_MANAGEMENT_VIEWS:
            preparation = _preparation(state, saved=saved)
            if preparation is not None:
                # Both current and previous immutable pages retain their actual
                # provenance for the screen and assistant. A read never dates it anew.
                payload["preparation"] = preparation
        if kind == "management-view":
            sync_reader = getattr(repository, "sync_state", None)
            if callable(sync_reader):
                from .sync_service import public_sync_state
                # Queue progress is operational state, not scientific evidence.
                # Keep it live without invalidating/rebuilding the prepared page.
                prepared_sync = payload.get("sync")
                payload["rendered_generation_id"] = (prepared_sync.get("active_generation_id")
                    if isinstance(prepared_sync, dict) else None)
                try:
                    payload["sync"] = jsonable_encoder(public_sync_state(sync_reader(alias)))
                    payload.pop("sync_unavailable", None)
                except (PersistentStoreFailure, TypeError, ValueError) as exc:
                    payload["sync"] = None
                    payload["sync_unavailable"] = True
                    logger.warning("prepared_view state=sync_deferred error_type=%s", type(exc).__name__)
        return payload
    except (PersistentStoreFailure, TypeError, ValueError) as exc:
        logger.warning("prepared_view state=read_deferred error_type=%s", type(exc).__name__)
        raise pending() from exc


def endpoint(kind):
    """Keep FastAPI's validated signature and register the worker calculation."""
    def decorate(calculate):
        sig = signature(calculate)
        _registry[kind] = calculate

        @wraps(calculate)
        def prepared(*args, **kwargs):
            if worker_repository() is not None:
                return calculate(*args, **kwargs)
            from . import dependencies
            from .oauth_store import SupabasePilotRepository
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            authorization = bound.arguments.get("authorization")
            athlete_alias = bound.arguments.get("athlete_alias")
            dependencies.authorize(authorization)
            alias = dependencies.validated_alias(athlete_alias, fallback=kind == "trainability")
            if alias is None:
                raise HTTPException(401, "Athlete session is required")
            repository = dependencies.repository()
            # The offline file pilot and legacy unit adapters have no durable
            # projection store. The production Supabase adapter always does;
            # a store/transport failure never falls through to calculation.
            if not isinstance(repository, SupabasePilotRepository) and not callable(getattr(repository, "prepared_view", None)):
                return calculate(*args, **kwargs)
            parameters = {}
            for name, value in bound.arguments.items():
                if name in {"authorization", "athlete_alias", "actor", "response"} or value is None:
                    continue
                default = sig.parameters[name].default
                if value == default:
                    continue
                parameters[name] = jsonable_encoder(value)
            return read(repository, alias, kind, parameters)

        return prepared
    return decorate


def _load_registry():
    # Import lazily in the worker as in the API. No separate scientific path.
    from .routes import activities, management, management_speed_comparison, models, response  # noqa: F401


def calculate(repository, alias, kind, parameters, *, day=None):
    _load_registry()
    callback = _registry.get(kind)
    if callback is None:
        raise ValueError("Unknown prepared view kind")
    sig = signature(callback)
    arguments = deepcopy(parameters)
    for key in ("period_start", "period_end", "as_of", "start_date"):
        if isinstance(arguments.get(key), str):
            arguments[key] = date.fromisoformat(arguments[key])
    if "body" in arguments:
        annotation = sig.parameters["body"].annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            arguments["body"] = annotation.model_validate(arguments["body"])
    if kind == "trainability" and day is not None:
        arguments.setdefault("period_end", date.fromisoformat(day))
    arguments.update(athlete_alias=alias, authorization=None)
    if "response" in sig.parameters:
        arguments["response"] = Response()
    from .read_session import ReadSession
    token = _worker_repository.set(ReadSession(repository, planning_settings_bundle=True))
    try:
        try:
            value = callback(**arguments)
        except HTTPException as exc:
            if not 400 <= exc.status_code < 500 or exc.status_code == 429:
                raise
            value = {"prepared_error": {"status": exc.status_code, "detail": exc.detail}}
        value = jsonable_encoder(value)
        if not isinstance(value, dict):
            raise ValueError("Prepared views must be objects")
        return value
    finally:
        _worker_repository.reset(token)


def process_next(repository):
    """Claim one bounded view, with the same lease and memory lane as planning."""
    from .memory_budget import MODEL_WORK
    from .prepared_models import _lease
    operation = repository.prepared_view
    key, token = model_key(), str(uuid4())
    with MODEL_WORK:
        claim = operation("CLAIM", token=token, model_key=key)
        if claim is None:
            return False
        if (not isinstance(claim, dict) or not isinstance(claim.get("athlete_alias"), str)
                or not isinstance(claim.get("kind"), str) or not isinstance(claim.get("parameters"), dict)
                or type(claim.get("revision")) is not int or not isinstance(claim.get("day"), str)):
            raise PersistentStoreFailure("Invalid prepared view claim")
        alias, kind, parameters = claim["athlete_alias"], claim["kind"], claim["parameters"]

        def leased(op, **kw):
            return operation(op, kind=kind, parameters=parameters, **kw)

        try:
            with _lease(leased, alias, token) as lost:
                payload = calculate(repository, alias, kind, parameters, day=claim["day"])
                if lost.is_set():
                    raise HTTPException(409, "Prepared view lease changed")
                saved = operation("STORE", alias=alias, kind=kind, parameters=parameters,
                    token=token, revision=claim["revision"], day=claim["day"], model_key=key, payload=prepared_view_storage.pack(kind, payload))
                logger.info("prepared_view kind=%s state=%s", kind,
                    "prepared" if isinstance(saved, dict) and saved.get("stored") is True else "superseded")
        except Exception as exc:
            try:
                leased("FAIL", alias=alias, token=token)
            except Exception:
                pass
            logger.warning("prepared_view kind=%s state=deferred error_type=%s", kind, type(exc).__name__)
    return True
