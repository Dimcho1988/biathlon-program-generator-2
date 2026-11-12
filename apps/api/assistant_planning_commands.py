"""Partial planning input changes validated by the existing canonical schemas.

This module never generates, activates or approves a programme.  The prepared
payload is published by assistant_commands in one revision/receipt transaction.
"""
from copy import deepcopy
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import ValidationError

from .cloud import AthletePlanningCalendar
from .management_schemas import ManagementProfile
from .management_store import ManagementStore, PROFILE_KEY
from .oauth_store import PersistentStoreFailure


PROFILE_FIELDS = frozenset({
    "available_minutes", "training_days", "availability_mode", "planning_controls",
    "program_start", "program_end", "horizon_mode", "discipline", "race_duration_min",
    "sport", "actual_sport", "max_key_sessions_per_week", "ant_references",
})
CONTROL_FIELDS = frozenset({
    "sessions_per_week", "sessions_by_day", "threshold_days", "threshold_method",
    "double_threshold_days", "double_threshold_components", "mixed_sessions_enabled",
    "intensity_days", "strength_days", "long_session_day", "max_strength_sessions",
    "training_sports", "weekly_target_hours", "mesocycle_anchor", "accent_mode",
    "accent_limit", "accents", "cycles",
})
EVENT_FIELDS = frozenset({"event_type", "name", "start_date", "end_date", "race_course"})


def _merge(current, patch):
    result = deepcopy(current)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def calendar_state(repository, alias):
    rows = repository._json(repository._request("GET",
        "/onflows_athlete_settings?select=planning_calendar,calendar_revision"
        f"&athlete_alias=eq.{quote(alias, safe='')}&limit=1"))
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise PersistentStoreFailure("Planning calendar settings are unavailable")
    row = rows[0]
    if type(row.get("calendar_revision")) is not int or row["calendar_revision"] < 0:
        raise PersistentStoreFailure("Planning calendar revision is unavailable")
    payload = row.get("planning_calendar")
    if payload is None:
        payload = {"schema_version": "planning-calendar-v1", "events": []}
    if not isinstance(payload, dict):
        raise PersistentStoreFailure("Invalid planning calendar")
    return {"payload": deepcopy(payload), "revision": row["calendar_revision"]}


def _revision(current, expected, *, check_current=True):
    if type(expected) is not int or expected < 0:
        raise HTTPException(422, "A current input revision is required")
    if check_current and current != expected:
        raise HTTPException(409, "Planning inputs changed; reload before editing")


def _calendar(payload):
    try:
        return AthletePlanningCalendar.from_mapping(payload).to_payload()
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(422, "Planning calendar values are inconsistent") from exc


def prepare(repository, alias, kind, entry_key, patch, expected_revision, actor=None):
    if not isinstance(patch, dict) or not patch:
        raise HTTPException(422, "Provide an explicit partial change")
    if kind == "MANAGEMENT_PROFILE":
        if entry_key not in (None, "", PROFILE_KEY):
            raise HTTPException(422, "Unknown planning profile key")
        if set(patch) - PROFILE_FIELDS:
            raise HTTPException(422, "This planning setting is not editable through chat")
        if "planning_controls" in patch and (
            not isinstance(patch["planning_controls"], dict)
            or not patch["planning_controls"]
            or set(patch["planning_controls"]) - CONTROL_FIELDS
        ):
            raise HTTPException(422, "Only explicit planning controls are editable through chat")
        current = ManagementStore(repository).profile(alias)
        _revision(current["revision"], expected_revision)
        if not current["configured"]:
            raise HTTPException(409, "Configure the initial planning profile in the planning form")
        merged = _merge(current["profile"], patch)
        # Validate a complete profile, retaining every unedited current setting.
        try:
            payload = ManagementProfile.model_validate(merged).model_dump(mode="json")
        except ValidationError as exc:
            raise HTTPException(422, "Planning profile values are inconsistent") from exc
        return {"kind": kind, "key": PROFILE_KEY, "payload": payload,
                "expected_revision": expected_revision}
    if kind == "CALENDAR_EVENT":
        if not isinstance(entry_key, str) or not entry_key:
            raise HTTPException(422, "Choose the calendar event identifier")
        deletion = patch.get("delete") is True
        if set(patch) - EVENT_FIELDS - {"delete"} or ("delete" in patch and not deletion):
            raise HTTPException(422, "Unknown calendar event change")
        if deletion and set(patch) != {"delete"}:
            raise HTTPException(422, "A deletion cannot include event edits")
        current = calendar_state(repository, alias)
        _revision(current["revision"], expected_revision)
        payload = deepcopy(current["payload"])
        events = payload.get("events")
        if not isinstance(events, list):
            raise PersistentStoreFailure("Invalid planning calendar events")
        existing = next((e for e in events if e.get("event_id") == entry_key), None)
        if deletion and existing is None:
            raise HTTPException(404, "The selected calendar event is unavailable")
        updated = [e for e in events if e.get("event_id") != entry_key]
        if not deletion:
            updated.append(_merge(existing or {"event_id": entry_key}, patch))
        payload["events"] = updated
        return {"kind": kind, "key": entry_key, "payload": _calendar(payload),
                "expected_revision": expected_revision}
    if kind == "CALENDAR_REPLACE":
        current = calendar_state(repository, alias)
        # The atomic RPC reads an existing operation receipt before its CAS.
        # A form retry after a committed timeout can therefore recover success.
        _revision(current["revision"], expected_revision, check_current=False)
        return {"kind": kind, "key": "planning-calendar-v1", "payload": _calendar(patch),
                "expected_revision": expected_revision}
    raise HTTPException(422, "Unknown planning input command")
