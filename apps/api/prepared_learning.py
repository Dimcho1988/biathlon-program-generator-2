"""Private worker-prepared learning projection; HTTP never fits the model.

The RPC owns local-day, input-revision, model-version and publication fences.
Only a current projection is returned: an older recommendation must not look
current while a new activity or observation is being processed.
"""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
import logging
import os
from uuid import uuid4

from fastapi import HTTPException

from .oauth_store import PersistentStoreFailure
from .prepared_models import _lease

logger = logging.getLogger(__name__)
VERSION = "prepared-learning-v4-lifetime-history"
PREPARING = "Оценката на самообучението се подготвя във фонов режим. Опитай отново след малко."


def model_key():
    from .recovery_policy import VERSION as policy_version
    from biathlon.individual_learning import VERSION as learning_version
    return sha256(json.dumps({"version": VERSION, "policy": policy_version,
        "learning": learning_version, "build": os.getenv("RENDER_GIT_COMMIT", VERSION)},
        sort_keys=True).encode()).hexdigest()


def _validate(value, alias):
    if not isinstance(value, dict) or value.get("athlete_alias") != alias:
        raise ValueError("Prepared learning belongs to another athlete")
    report = value.get("report")
    if (not isinstance(report, dict) or report.get("schema_version") != "recovery-learning-view-v1"
            or not isinstance(report.get("series"), list) or len(report["series"]) > 367):
        raise ValueError("Invalid prepared learning report")
    day = report.get("as_of")
    if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError("Invalid prepared learning date")
    for row in report["series"]:
        if (not isinstance(row, dict) or not isinstance(row.get("day"), str)
                or date.fromisoformat(row["day"]).isoformat() != row["day"] or row["day"] > day):
            raise ValueError("Invalid prepared learning series")
    return report


def read_view(repository, alias, *, days=90, end=None):
    """One small projection read, including on misses; no model lane on HTTP."""
    operation = getattr(repository, "learning_preparation", None)
    if not callable(operation):
        raise HTTPException(503, PREPARING, headers={"Retry-After": "15", "Cache-Control": "no-store"})
    try:
        history_reader = getattr(repository, "learning_history_view", None)
        if callable(history_reader):
            state = history_reader(alias, days=days, end=end, model_key=model_key())
        else:
            state = operation("READ", alias=alias, model_key=model_key())
        if isinstance(state, dict) and state.get("invalid_period"):
            raise HTTPException(422, "Избраният период е извън наличния календар.")
        if not isinstance(state, dict) or state.get("ready") is not True:
            raise HTTPException(503, PREPARING, headers={"Retry-After": "15", "Cache-Control": "no-store"})
        report = deepcopy(_validate(state.get("payload"), alias))
        if report["as_of"] != state.get("day"):
            raise ValueError("Prepared learning day differs")
        last = end or date.fromisoformat(report["as_of"])
        first = (last-timedelta(days=days-1)).isoformat()
        report["series"] = [row for row in report["series"] if first <= row["day"] <= last.isoformat()]
        report["preparation"] = {"status": "READY", "prepared_at": state.get("prepared_at")}
        logger.info("prepared_learning state=hit")
        return report
    except (PersistentStoreFailure, TypeError, ValueError) as exc:
        logger.warning("prepared_learning state=read_deferred error_type=%s", type(exc).__name__)
        raise HTTPException(503, PREPARING, headers={"Retry-After": "15", "Cache-Control": "no-store"}) from exc


def process_learning(repository, alias=None):
    from . import learning_view
    from .memory_budget import MODEL_WORK
    operation = repository.learning_preparation
    key, token = model_key(), str(uuid4())
    with MODEL_WORK:
        claim = operation("CLAIM", model_key=key, token=token, **({"alias": alias} if alias else {}))
        if claim is None:
            from .lifetime_history import process_older_window
            return process_older_window(repository, key)
        if (not isinstance(claim, dict) or not isinstance(claim.get("athlete_alias"), str)
                or type(claim.get("revision")) is not int or not isinstance(claim.get("day"), str)):
            raise PersistentStoreFailure("Invalid learning preparation claim")
        alias = claim["athlete_alias"]
        try:
            with _lease(operation, alias, token) as lost:
                report = learning_view.view(repository, alias, days=90)
                from .lifetime_history import extend_report
                report = extend_report(repository, alias, report)
                payload = {"athlete_alias": alias, "report": report}
                _validate(payload, alias)
                if lost.is_set() or report["as_of"] != claim["day"]:
                    raise HTTPException(409, "Learning preparation lease or day changed")
                saved = operation("STORE", alias=alias, token=token, revision=claim["revision"],
                    day=claim["day"], model_key=key, payload=payload)
                logger.info("prepared_learning state=%s", "prepared" if isinstance(saved, dict)
                    and saved.get("stored") is True else "superseded")
        except Exception as exc:
            try:
                operation("FAIL", alias=alias, token=token)
            except Exception:
                pass
            logger.warning("prepared_learning state=deferred error_type=%s", type(exc).__name__)
    return True
