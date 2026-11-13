"""Partial planning input changes validated by the existing canonical schemas.

This module never generates, activates or approves a programme.  The prepared
payload is published by assistant_commands in one revision/receipt transaction.
"""
from copy import deepcopy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import ValidationError

from .cloud import AthletePlanningCalendar
from .management_schemas import ManagementProfile, PlanningControls
from .management_store import ManagementStore, PROFILE_KEY
from .oauth_store import PersistentStoreFailure


# The app and chat edit the same validated profile contract. Derived dose
# fractions remain server-owned; all actual form controls are available.
PROFILE_FIELDS = frozenset(ManagementProfile.model_fields) - {
    "schema_version", "maintenance_fraction", "reentry_fraction", "training_structure",
}
CONTROL_FIELDS = frozenset(PlanningControls.model_fields)
EVENT_FIELDS = frozenset({"event_type", "name", "start_date", "end_date", "race_course"})


def identical_event_ids(events, event):
    """Exact copies only; matching dates alone never merge separate races."""
    if not event:
        return []
    content = {k: v for k, v in event.items() if k != "event_id"}
    return [e["event_id"] for e in events if {k: v for k, v in e.items() if k != "event_id"} == content]


def _merge(current, patch):
    result = deepcopy(current)
    for key, value in patch.items():
        if key == "overrides" and isinstance(value, dict):
            # Explicit empty map resets the catalogue overrides; null removes
            # a named override while retaining the other method preferences.
            overrides = deepcopy(result.get(key, {})) if value else {}
            for name, item in value.items():
                if item is None:
                    overrides.pop(name, None)
                elif isinstance(item, dict):
                    overrides[name] = _merge(overrides.get(name, {}), item)
                else:
                    overrides[name] = deepcopy(item)
            result[key] = overrides
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
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
        if isinstance(patch.get("method_library"), dict) and "personal_methods" in patch["method_library"]:
            raise HTTPException(422, "Личните методи се променят чрез преглед на тренировъчно предложение.")
        if "planning_controls" in patch and patch["planning_controls"] is not None and (
            not isinstance(patch["planning_controls"], dict)
            or not patch["planning_controls"]
            or set(patch["planning_controls"]) - CONTROL_FIELDS
        ):
            raise HTTPException(422, "Only explicit planning controls are editable through chat")
        current = ManagementStore(repository).profile(alias)
        _revision(current["revision"], expected_revision)
        merged = _merge(current["profile"] or {}, patch)
        # Validate a complete profile, retaining every unedited current setting.
        try:
            profile = ManagementProfile.model_validate(merged)
            payload = profile.model_dump(mode="json")
            if "method_library" in patch:
                from .method_library_service import validate_preferences
                payload["method_library"] = validate_preferences(payload["method_library"], payload)
            if "lactate_profiles" in patch and profile.lactate_profiles:
                settings = repository.athlete_settings(alias)
                today = datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone if settings else "UTC")).date()
                if any(item.assessed_on > today for item in profile.lactate_profiles):
                    raise HTTPException(422, "A lactate observation cannot be in the future")
        except ValidationError as exc:
            raise HTTPException(422, "Planning profile values are inconsistent") from exc
        return {"kind": kind, "key": PROFILE_KEY, "payload": payload,
                "expected_revision": expected_revision}
    if kind == "CALENDAR_EVENT":
        if not isinstance(entry_key, str) or not entry_key:
            raise HTTPException(422, "Choose the calendar event identifier")
        deletion = patch.get("delete") is True
        if set(patch) - EVENT_FIELDS - {"delete", "include_identical_copies"} or ("delete" in patch and not deletion):
            raise HTTPException(422, "Unknown calendar event change")
        if "include_identical_copies" in patch and patch["include_identical_copies"] is not True:
            raise HTTPException(422, "Identical copies must be explicitly included")
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
        group = identical_event_ids(events, existing) if patch.get("include_identical_copies") else [entry_key]
        if patch.get("include_identical_copies") and (not existing or len(group) < 2):
            raise HTTPException(409, "The identical calendar copies changed; reload before editing")
        updated = [e for e in events if e.get("event_id") not in group]
        if not deletion:
            fields = {k: v for k, v in patch.items() if k in EVENT_FIELDS}
            if not fields:
                raise HTTPException(422, "Provide the event fields to change")
            for identifier in group:
                original = next((e for e in events if e.get("event_id") == identifier), {"event_id": identifier})
                updated.append(_merge(original, fields))
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
