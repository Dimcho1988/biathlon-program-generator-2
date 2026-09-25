"""Freeze management inputs and append review-only plan revisions.

Draft generation never changes the athlete's actual load, model coefficients,
activity calendar, or an approved training prescription.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from biathlon.periodization import ENGINE_VERSION as PERIODIZATION_VERSION

from .management_schemas import ManagementProfile
from .management_store import ManagementStore
from .model_service import ModelStore
from .oauth_store import PersistentStoreFailure
from . import training_plan_engine, race_duration


def profile_view(repository, alias, *, now=None):
    """Saved profile and its historical volume basis, without generating a plan."""
    profile = ManagementStore(repository).profile(alias)
    settings = repository.athlete_settings(alias)
    athlete_timezone = settings.timezone if settings else "UTC"
    today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(athlete_timezone)).date()
    history = None
    if hasattr(repository, "active_analysis"):
        from biathlon.planning_controls import volume_history
        analysis = repository.active_analysis(alias) or {}
        source = (analysis.get("snapshot_payload") or {}).get("load_history") or {}
        covered = len({r["date"] for r in source.get("daily", [])
                       if (today-timedelta(days=28)).isoformat() <= r["date"] < today.isoformat()})
        history_controls = (profile.get("profile") or {}).get("planning_controls") or {}
        history = {**volume_history(source, today, covered,
                                   gap_days=history_controls.get("history_gap_days", 10)),
                   "as_of": source.get("period_end")}
    return {**profile, "timezone": athlete_timezone, "today": today.isoformat(), "history": history}


def outlook(repository, alias, *, now=None):
    """Current saved goals, independent of any frozen draft or active plan.

    Reads profile, calendar, actual history and the event's speed curve.
    No future sessions, lifecycle refresh, or persistence.
    """
    stored = ManagementStore(repository).profile(alias)
    if not stored["configured"]:
        return {"configured": False, "outlook": None}
    profile = ManagementProfile.model_validate(stored["profile"]).model_dump(mode="json")
    event_duration = race_duration.preview(repository, alias, profile)
    settings = repository.athlete_settings(alias)
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(settings.timezone) if settings else timezone.utc).date()
    engine = training_plan_engine
    analysis = repository.active_analysis(alias) or {}
    if event_duration["source"] == "SPEED_DURATION" and (
            event_duration.get("source_generation_id") != analysis.get("generation_id") or
            event_duration.get("source_revision") != analysis.get("revision")):
        event_duration = {**race_duration.resolve(profile), "reason": "MODEL_UPDATED"}
    profile = race_duration.applied(profile, event_duration)
    source = (analysis.get("snapshot_payload") or {}).get("load_history") or {}
    rows = engine._daily_rows(source, today)
    calendar = engine._read_optional(repository, "athlete_planning_calendar", alias) or {"events": []}
    profile, horizon = engine.planning_schedule.horizon(profile, calendar["events"])
    preferences = engine._read_optional(repository, "athlete_planning_profile", alias) or {}
    accents = engine._read_optional(repository, "athlete_mesocycle_accent_preferences", alias)
    controls = profile.get("planning_controls")
    if controls:
        preferences = {"mesocycle_anchor_date": controls.get("mesocycle_anchor") or profile["program_start"],
                       "mesocycle_length_weeks": len(controls["wave"])}
    anchor = date.fromisoformat(str(preferences.get("mesocycle_anchor_date", profile["program_start"])))
    length = preferences.get("mesocycle_length_weeks", 4)
    reference = engine.training_targets.development_reference(rows, today, anchor, length)
    quality = source.get("quality") or {}
    history = engine.planning_controls.volume_history(source, today, 0, gap_days=(controls or {}).get("history_gap_days", engine.planning_history.DEFAULT_GAP_DAYS))
    limited = (not history["history_policy"]["usable"] or any(not v["known"] for v in engine.planning_controls.reference(rows, today).values())
               or bool(quality.get("limited_activities") or quality.get("excluded_activities"))
               or source.get("period_end") != today.isoformat())
    volume = engine.planning_controls.volume_basis(profile, history)
    reentry_days, reentry_reason = engine.planning_history.reentry(profile, history)
    phases = engine.build_periodization(profile["program_start"], profile["program_end"], calendar["events"],
                                       reentry_days_override=reentry_days,
                                       taper_days=profile["taper_days"], transition_days=profile["transition_days"])
    phases["entry_basis"] = {"days_override": reentry_days, "reason": reentry_reason}
    progression = engine.progression_context(repository, alias, profile, source, rows, today)
    projection = engine._long_term_outlook(profile, phases, reference, accents, preferences, rows, today,
                                         limited, volume=volume, events=calendar["events"], progression=progression)
    return {"configured": True, "outlook": {
        "schema_version": "training-outlook-preview-v1", "profile_revision": stored["revision"],
        "race_duration": event_duration,
        "generated_at": now.isoformat(), "engine_version": engine.VERSION,
        "source": {"generation_id": analysis.get("generation_id"), "revision": analysis.get("revision"), "as_of": source.get("period_end")},
        "periodization": phases, "long_term": projection, "history_comparison": history["weeks"],
        "component_history": engine.load_progression.history(source, rows, today),
        "input_snapshot": {"calendar": calendar, "profile_revision": stored["revision"], "horizon": horizon,
                           "planning_controls": controls},
        "volume_context": {**volume, "available_weekly_minutes": sum(engine.planning_history.availability(profile)) if engine.planning_history.availability_mode(profile) == "MANUAL" else None,
                           "weekly_time_limit_minutes": controls["weekly_target_hours"]*60 if controls and controls.get("weekly_target_hours") is not None else None,
                           "availability_mode": engine.planning_history.availability_mode(profile), "history_policy": history["history_policy"], "volume_evidence": history},
    }}


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False, default=str).encode()).hexdigest()


def _payload(value):
    return value.to_payload() if value is not None else None


def input_state(repository, alias, *, evaluated_at=None, include_response=False):
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
        "response_fingerprint": _hash(training_plan_engine.ResponseStore(repository).entries(alias)) if include_response else None,
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


def build_draft(repository, alias, start_date, expected_profile_revision, *, now=None, decisions=None, locked_day=None):
    store = ManagementStore(repository)
    stored = store.profile(alias)
    if not stored["configured"]:
        raise HTTPException(409, "Complete the management profile first")
    if stored["revision"] != expected_profile_revision:
        raise HTTPException(409, "The management profile changed; reload before generating")
    profile = ManagementProfile.model_validate(stored["profile"])
    settings = repository.athlete_settings(alias)
    if settings is None:
        raise HTTPException(409, "Configure athlete HR zones and timezone before generating")
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(settings.timezone)).date()
    if not today <= start_date <= today + timedelta(days=7):
        raise HTTPException(422, "Choose a start from today through the next seven days")
    calendar = training_plan_engine._read_optional(repository, "athlete_planning_calendar", alias) or {"events": []}
    resolved, _ = training_plan_engine.planning_schedule.horizon(profile.model_dump(mode="json"), calendar["events"])
    if start_date < profile.program_start or start_date > date.fromisoformat(resolved["program_end"]):
        raise HTTPException(422, "The draft must start within the planning period")
    include_response = bool(profile.load_progression and profile.load_progression.enabled and profile.load_progression.feedback_enabled)
    before = input_state(repository, alias, evaluated_at=now, include_response=include_response)
    payload = training_plan_engine.generate_plan(
        repository, alias, profile.model_dump(mode="json"), start_date=start_date, now=now,
        decisions=decisions, locked_day=locked_day,
    )
    after = input_state(repository, alias, evaluated_at=now, include_response=include_response)
    if _hash(before) != _hash(after):
        raise HTTPException(409, "Analysis or planning inputs changed; generate a new draft")
    source_generation = (payload.get("source") or {}).get("generation_id")
    if source_generation != before["generation_id"]:
        raise HTTPException(409, "The draft used a different analysis generation; try again")
    frozen = {**before, "management_profile": profile.model_dump(mode="json"),
              "profile_revision": stored["revision"]}
    payload.update({"input_snapshot": frozen, "input_fingerprint": _hash(frozen),
                    "review_required": True, "automatically_published": False,
                    "operational_context": {"decisions": decisions or {}, "locked_day": locked_day}})
    return payload


def generate(repository, alias, body, actor, *, now=None):
    payload = build_draft(repository, alias, body.start_date, body.expected_profile_revision, now=now)
    return ManagementStore(repository).save_draft(
        alias, payload, actor, body.expected_profile_revision, expected_revision=body.expected_draft_revision,
        check_generation=True, expected_generation_id=payload["source"]["generation_id"])


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
        normalized = ManagementProfile.model_validate(profile["profile"]).model_dump(mode="json") if profile["profile"] else None
        progression = training_plan_engine.load_progression.settings(normalized or {})
        state = input_state(repository, alias, include_response=bool(progression and progression["feedback_enabled"]))
        fingerprint = _hash({**state, "management_profile": normalized,
                             "profile_revision": profile["revision"]})
    except PersistentStoreFailure:
        return {"drafts": [{**row, "stale": None,
                            "stale_reason": "Актуалността на входните данни не е потвърдена."}
                           for row in rows]}
    return {"current_input_fingerprint": fingerprint, "drafts": [{**row,
                        "stale": row["payload"].get("input_fingerprint") != fingerprint,
                        "stale_reason": (
                            "Данните, моделът или денят на оценката са променени — създай нов проект."
                            if row["payload"].get("input_fingerprint") != fingerprint else None),
                        } for row in rows]}
