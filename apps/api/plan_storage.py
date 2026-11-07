"""Lossless storage of the legacy primary-session alias.

The complete ordered sessions, blocks and frozen evidence remain in JSON.
Only a day.session equal to sessions[0] becomes a local, versioned reference.
SQL overview projections already prefer sessions. Public readers restore the
existing contract; no lookup in a mutable method catalog is involved.
"""
from copy import deepcopy
import json
from .oauth_store import PersistentStoreFailure

FORMAT = "primary-session-reference-v1"
REFERENCE = {"$session_ref": 0}


def _identical(left, right):
    # Python equality equates True with 1; frozen JSON evidence must not.
    return left is right or json.dumps(left, sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=False, allow_nan=False)


def transform(value, *, encode):
    if isinstance(value, list):
        return [transform(item, encode=encode) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("schema_version") != "planning-draft-v1":
        return {key: transform(item, encode=encode) if key in {"payload", "plan", "proposal", "record"}
                else item for key, item in value.items()}
    marker = value.get("_storage")
    if marker is not None and marker != FORMAT:
        raise PersistentStoreFailure("Unsupported stored planning format")
    if encode and marker == FORMAT or not encode and marker is None:
        return value
    result = dict(value)
    references = 0
    for key in ("days", "lookahead_days"):
        if not isinstance(value.get(key), list):
            continue
        days = []
        for day in value[key]:
            if not isinstance(day, dict):
                days.append(day)
                continue
            sessions = day.get("sessions")
            primary = sessions[0] if isinstance(sessions, list) and sessions else None
            if encode and isinstance(primary, dict) and _identical(day.get("session"), primary):
                days.append({**day, "session": dict(REFERENCE)})
                references += 1
            elif not encode and isinstance(day.get("session"), dict) and "$session_ref" in day["session"]:
                ref = day["session"]
                if (set(ref) != {"$session_ref"} or type(ref["$session_ref"]) is not int
                        or ref["$session_ref"] != 0 or not isinstance(primary, dict)):
                    raise PersistentStoreFailure("Invalid stored primary-session reference")
                # JSON readers historically return independent legacy aliases.
                days.append({**day, "session": deepcopy(primary)})
            else:
                days.append(day)
        result[key] = days
    if encode and references:
        result["_storage"] = FORMAT
    elif not encode:
        result.pop("_storage", None)
    return result


def pack(value):
    return transform(value, encode=True)


def unpack(value):
    return transform(value, encode=False)
