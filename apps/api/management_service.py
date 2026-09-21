"""Freeze management inputs and append review-only plan revisions.

Draft generation never changes the athlete's actual load, model coefficients,
activity calendar, or an approved training prescription.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from biathlon.periodization import ENGINE_VERSION as PERIODIZATION_VERSION

from .management_schemas import ManagementProfile
from .management_store import ManagementStore
from .model_service import ModelStore
from .oauth_store import PersistentStoreFailure
from . import training_plan_engine


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False, default=str).encode()).hexdigest()


def _payload(value):
    return value.to_payload() if value is not None else None


def input_state(repository, alias, *, evaluated_at=None):
    """Capture settings and source identity; never store credentials or wellness."""
    settings = repository.athlete_settings(alias)
    analysis = repository.active_analysis(alias) or {}
    entries = ModelStore(repository).entries(alias)
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    return {
        "evaluation_date": evaluated_at.astimezone(ZoneInfo(settings.timezone) if settings else timezone.utc).date().isoformat(),
        "rule_versions": {
            "engine": training_plan_engine.VERSION,
            "parameters": training_plan_engine.PARAMETER_VERSION,
            "methods": training_plan_engine.METHODS_VERSION,
            "periodization": PERIODIZATION_VERSION,
            "speed_duration": training_plan_engine.speed_duration.VERSION,
            "recovery": training_plan_engine.recovery_v2.VERSION,
        },
        "generation_id": analysis.get("generation_id"),
        "analysis_revision": analysis.get("revision"),
        "analysis_as_of": analysis.get("analysis_as_of"),
        "snapshot_fingerprint": _hash(analysis.get("snapshot_payload")),
        "physiology": None if settings is None else {
            "zone_bounds_bpm": list(settings.zone_bounds_bpm),
            "hrmax_bpm": settings.hrmax_bpm,
            "timezone": settings.timezone,
        },
        "calendar": _payload(repository.athlete_planning_calendar(alias)),
        "planning_preferences": _payload(repository.athlete_planning_profile(alias)),
        "accent_preferences": _payload(repository.athlete_mesocycle_accent_preferences(alias)),
        "model_entries": sorted([
            {"kind": e["kind"], "entry_key": e["entry_key"],
             "revision": e["revision"], "payload": e["payload"]}
            for e in entries
        ], key=lambda e: (e["kind"], e["entry_key"])),
    }


def generate(repository, alias, body, actor, *, now=None):
    store = ManagementStore(repository)
    stored = store.profile(alias)
    if not stored["configured"]:
        raise HTTPException(409, "Complete the management profile first")
    if stored["revision"] != body.expected_profile_revision:
        raise HTTPException(409, "The management profile changed; reload before generating")
    profile = ManagementProfile.model_validate(stored["profile"])
    settings = repository.athlete_settings(alias)
    if settings is None:
        raise HTTPException(409, "Configure athlete HR zones and timezone before generating")
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(settings.timezone)).date()
    if not today <= body.start_date <= today + timedelta(days=7):
        raise HTTPException(422, "Choose a start from today through the next seven days")
    if body.start_date < profile.program_start or body.start_date + timedelta(days=6) > profile.program_end:
        raise HTTPException(422, "The full seven-day draft must fit within the planning period")
    before = input_state(repository, alias, evaluated_at=now)
    payload = training_plan_engine.generate_plan(
        repository, alias, profile.model_dump(mode="json"), start_date=body.start_date, now=now,
    )
    after = input_state(repository, alias, evaluated_at=now)
    if _hash(before) != _hash(after):
        raise HTTPException(409, "Analysis or planning inputs changed; generate a new draft")
    source_generation = (payload.get("source") or {}).get("generation_id")
    if source_generation != before["generation_id"]:
        raise HTTPException(409, "The draft used a different analysis generation; try again")
    frozen = {**before, "management_profile": profile.model_dump(mode="json"),
              "profile_revision": stored["revision"]}
    payload.update({"input_snapshot": frozen, "input_fingerprint": _hash(frozen),
                    "review_required": True, "automatically_published": False})
    return store.save_draft(
        alias, payload, actor, body.expected_profile_revision,
        expected_revision=body.expected_draft_revision,
        check_generation=True, expected_generation_id=before["generation_id"],
    )


def history(repository, alias, *, start_date=None):
    """Retain historical evidence while clearly marking changed inputs.

    These are drafts, not an atomically activated schedule. A sync can finish
    after a draft was saved; that does not rewrite its frozen source context.
    """
    store = ManagementStore(repository)
    rows = store.drafts(alias, start_date=start_date) if start_date is not None else store.drafts(alias)
    if not rows:
        return {"drafts": []}
    try:
        profile = store.profile(alias)
        state = input_state(repository, alias)
        fingerprint = _hash({**state, "management_profile": profile["profile"],
                             "profile_revision": profile["revision"]})
    except PersistentStoreFailure:
        return {"drafts": [{**row, "stale": None,
                            "stale_reason": "Актуалността на входните данни не е потвърдена."}
                           for row in rows]}
    return {"drafts": [{**row,
                        "stale": row["payload"].get("input_fingerprint") != fingerprint,
                        "stale_reason": (
                            "Данните, моделът или денят на оценката са променени — създай нов проект."
                            if row["payload"].get("input_fingerprint") != fingerprint else None),
                        } for row in rows]}
