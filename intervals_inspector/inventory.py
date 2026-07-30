"""Build deidentified inventories of Intervals.icu response structures.

The functions in this module deliberately return metadata only.  They never
include example values from an athlete's response.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import csv
from datetime import datetime
import io
import json
import math
import re
from statistics import median, pstdev
from typing import Any
from urllib.parse import urlsplit


_FIELD_EXPORT_COLUMNS = (
    "json_path",
    "source_endpoint",
    "value_types",
    "records_present",
    "non_empty_records",
    "coverage_percent",
    "classification",
)
_STREAM_EXPORT_COLUMNS = (
    "stream_name",
    "value_type",
    "unit",
    "activity_count",
    "total_points",
    "estimated_frequency_hz",
)
_ENDPOINT_EXPORT_COLUMNS = (
    "category",
    "endpoint",
    "http_status",
    "available",
    "record_count",
    "field_names",
    "safe_error",
)
_MAPPING_EXPORT_COLUMNS = (
    "target_field",
    "status",
    "matched_source_fields",
    "missing_source_fields",
    "model_consumers",
    "note",
)
_MODEL_EXPORT_COLUMNS = (
    "model",
    "readiness",
    "missing_or_limit",
)

_TYPE_ORDER = {
    "null": 0,
    "boolean": 1,
    "integer": 2,
    "number": 3,
    "string": 4,
    "array": 5,
    "object": 6,
}

_ALWAYS_SENSITIVE_TOKENS = {
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "credentials",
    "gps",
    "latitude",
    "longitude",
    "lat",
    "lng",
    "lon",
    "location",
    "map",
    "maps",
    "password",
    "passwd",
    "passphrase",
    "polyline",
    "route",
    "routes",
    "secret",
    "secrets",
    "token",
    "tokens",
}
_SENSITIVE_TEXT_TOKENS = {
    "comment",
    "comments",
    "contact",
    "contacts",
    "description",
    "descriptions",
    "message",
    "messages",
    "note",
    "notes",
}
_PROFILE_VALUE_KEYS = {
    "address",
    "athleteid",
    "avatar",
    "birthdate",
    "birthday",
    "city",
    "contact",
    "country",
    "dateofbirth",
    "displayname",
    "dob",
    "email",
    "emergencycontact",
    "firstname",
    "fullname",
    "gender",
    "id",
    "identifier",
    "lastname",
    "mobile",
    "name",
    "phone",
    "photo",
    "picture",
    "postalcode",
    "postcode",
    "profileid",
    "sex",
    "street",
    "userid",
    "username",
    "zipcode",
}
_PROFILE_CONTEXT_KEYS = {
    "account",
    "athlete",
    "contact",
    "identity",
    "owner",
    "profile",
    "user",
}
_ALWAYS_PROFILE_SENSITIVE_KEYS = {
    "address",
    "birthdate",
    "birthday",
    "dateofbirth",
    "displayname",
    "dob",
    "email",
    "emergencycontact",
    "firstname",
    "fullname",
    "lastname",
    "mobile",
    "phone",
    "postalcode",
    "postcode",
    "street",
    "username",
    "zipcode",
}
_SENSITIVE_COMPACT_KEYS = {
    "accesstoken",
    "apikey",
    "authorizationcode",
    "authcode",
    "clientid",
    "clientsecret",
    "code",
    "coordinates",
    "geolocation",
    "idtoken",
    "oauthcode",
    "refreshtoken",
    "setcookie",
    "waypoints",
}

_BEARER_RE = re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:access_token|refresh_token|id_token|token|client_secret|"
    r"authorization_code|auth_code|code|state|password|icu_api_key|api_key|"
    r"secret)=)[^&#\s]+"
)
_EMAIL_RE = re.compile(
    r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
)
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_PART_RE = re.compile(r"[^a-z0-9]+")
_SAFE_METADATA_RE = re.compile(r"^[\w%°/().+*^ -]{1,64}$", re.UNICODE)
_SAFE_JSON_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,127}$")
_SAFE_JSON_PATH_RE = re.compile(
    r"^(?:\$|[A-Za-z_][A-Za-z0-9_-]{0,127})(?:\[\])?"
    r"(?:\.(?:[A-Za-z_][A-Za-z0-9_-]{0,127})(?:\[\])?)*$"
)
_SAFE_ENDPOINT_RE = re.compile(r"^[A-Za-z0-9_{}./:-]{1,256}$")
_SAFE_LOCATION_STREAM_NAMES = {
    "gps",
    "lat",
    "latitude",
    "latlng",
    "lng",
    "lon",
    "longitude",
}


def _key_parts(value: Any) -> tuple[str, ...]:
    text = _CAMEL_BOUNDARY_RE.sub("_", str(value)).lower()
    return tuple(part for part in _KEY_PART_RE.split(text) if part)


def _compact_key(value: Any) -> str:
    return "".join(_key_parts(value))


def _is_sensitive_key(key: Any, ancestors: Sequence[str] = ()) -> bool:
    parts = set(_key_parts(key))
    compact = _compact_key(key)

    if parts & _ALWAYS_SENSITIVE_TOKENS:
        return True
    if parts & _SENSITIVE_TEXT_TOKENS:
        return True
    if compact in _SENSITIVE_COMPACT_KEYS:
        return True
    if any(
        marker in compact
        for marker in (
            "coordinate",
            "geolocation",
            "latitude",
            "latlng",
            "latlon",
            "lnglat",
            "longitude",
            "polyline",
        )
    ):
        return True
    if compact in _ALWAYS_PROFILE_SENSITIVE_KEYS:
        return True
    if (
        compact.endswith("token")
        or compact.endswith("secret")
        or compact.endswith("apikey")
    ):
        return True
    if compact in {"auth", "oauth"}:
        return True

    ancestor_compacts = {_compact_key(item) for item in ancestors}
    if ancestor_compacts & _PROFILE_CONTEXT_KEYS and compact in _PROFILE_VALUE_KEYS:
        return True
    if compact in {"athleteid", "profileid", "userid", "ownerid"}:
        return True
    return False


def _safe_json_key(value: Any) -> str | None:
    key = str(value).strip()
    if not key or _EMAIL_RE.search(key):
        return None
    if not _SAFE_JSON_KEY_RE.fullmatch(key):
        return None
    return key


def _redact_embedded_secrets(value: str) -> str:
    value = _BEARER_RE.sub(r"\1 [REDACTED]", value)
    value = _SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]", value)
    return _EMAIL_RE.sub("[REDACTED_EMAIL]", value)


def redact_sensitive_data(value: Any, _ancestors: tuple[str, ...] = ()) -> Any:
    """Return a recursive copy with sensitive fields omitted.

    Sensitive mappings are removed instead of replaced so that neither their
    values nor their structure can accidentally be displayed as raw response
    data.  Credential fragments and email addresses embedded in otherwise safe
    strings are replaced as a second line of defence.
    """

    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = _safe_json_key(raw_key)
            if key is None:
                continue
            if _is_sensitive_key(key, _ancestors):
                continue
            cleaned[key] = redact_sensitive_data(child, (*_ancestors, key))
        return cleaned

    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [redact_sensitive_data(item, _ancestors) for item in value]

    if isinstance(value, str):
        return _redact_embedded_secrets(value)
    return value


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return "array"
    return type(value).__name__.lower()


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return bool(value)
    return True


def _record_observations(record: Any) -> dict[str, list[Any]]:
    observations: dict[str, list[Any]] = {}

    def add(path: str, item: Any) -> None:
        observations.setdefault(path, []).append(item)

    def walk(item: Any, path: str) -> None:
        if path:
            add(path, item)
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = _safe_json_key(raw_key)
                if key is None:
                    continue
                child_path = f"{path}.{key}" if path else key
                walk(child, child_path)
        elif isinstance(item, Sequence) and not isinstance(
            item, (str, bytes, bytearray)
        ):
            item_path = f"{path}[]" if path else "$[]"
            for child in item:
                walk(child, item_path)

    if isinstance(record, Mapping):
        for raw_key, child in record.items():
            walk(child, str(raw_key))
    else:
        walk(record, "$")
    return observations


def _normalise_standard_path(path: str) -> str:
    return path.replace("[]", "").strip(".").casefold()


def _looks_custom(path: str) -> bool:
    for segment in path.replace("[]", "").split("."):
        parts = _key_parts(segment)
        compact = "".join(parts)
        if (
            compact.startswith("custom")
            or compact.startswith("unknown")
            or compact.startswith("userdefined")
            or segment.casefold().startswith("x_")
        ):
            return True
    return False


def _classification(
    path: str,
    standard_fields: set[str] | None,
) -> str:
    normalised = _normalise_standard_path(path)
    if standard_fields is None:
        return "unknown/custom" if _looks_custom(path) else "standard"

    if normalised in standard_fields:
        return "standard"
    prefix = f"{normalised}."
    if any(item.startswith(prefix) for item in standard_fields):
        return "standard"
    return "unknown/custom"


def _safe_source_endpoint(endpoint: Any) -> str:
    raw = str(endpoint or "").strip()
    if not raw:
        return "{endpoint}"

    split = urlsplit(raw)
    path = split.path if split.scheme or split.netloc else raw.split("?", 1)[0]
    path = path.split("#", 1)[0]
    if _EMAIL_RE.search(path) or any(ord(character) < 32 for character in path):
        return "{endpoint}"
    if any(_is_sensitive_key(part) for part in _key_parts(path)):
        return "{endpoint}"
    parts = path.split("/")
    for index in range(1, len(parts)):
        previous = parts[index - 1].casefold()
        current = parts[index]
        if not current or (current.startswith("{") and current.endswith("}")):
            continue
        if previous in {"athlete", "athletes"}:
            parts[index] = "{athlete_id}"
        elif previous in {"activity", "activities"} and (
            current.isdigit() or re.fullmatch(r"[iIaA]?\d+", current)
        ):
            parts[index] = "{activity_id}"
    safe_path = "/".join(parts)
    if not _SAFE_ENDPOINT_RE.fullmatch(safe_path):
        return "{endpoint}"
    return safe_path


def build_field_coverage(
    records: Iterable[Any] | Mapping[str, Any],
    source_endpoint: str,
    standard_fields: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Describe field presence and completeness without retaining sample values.

    Nested mapping paths use dots and list item paths use ``[]``.  Presence and
    non-empty counts are calculated once per top-level record, even when a path
    occurs in several list items.
    """

    if isinstance(records, Mapping):
        record_list = [records]
    elif isinstance(records, (str, bytes, bytearray)):
        raise TypeError("records must be a mapping or an iterable of records")
    else:
        record_list = list(records)

    total_records = len(record_list)
    if not total_records:
        return []

    standard_set = (
        {_normalise_standard_path(str(path)) for path in standard_fields}
        if standard_fields is not None
        else None
    )
    observations_by_path: dict[str, dict[str, Any]] = {}

    for record in record_list:
        safe_record = redact_sensitive_data(record)
        record_observations = _record_observations(safe_record)
        for path, observed_values in record_observations.items():
            accumulator = observations_by_path.setdefault(
                path,
                {"present": 0, "non_empty": 0, "types": set()},
            )
            accumulator["present"] += 1
            if any(_is_non_empty(item) for item in observed_values):
                accumulator["non_empty"] += 1
            accumulator["types"].update(
                _json_type(item) for item in observed_values
            )

    endpoint = _safe_source_endpoint(source_endpoint)
    rows: list[dict[str, Any]] = []
    for path in sorted(observations_by_path):
        accumulator = observations_by_path[path]
        value_types = sorted(
            accumulator["types"],
            key=lambda item: (_TYPE_ORDER.get(item, 99), item),
        )
        rows.append(
            {
                "json_path": path,
                "source_endpoint": endpoint,
                "value_types": ", ".join(value_types),
                "records_present": accumulator["present"],
                "non_empty_records": accumulator["non_empty"],
                "coverage_percent": round(
                    accumulator["non_empty"] / total_records * 100,
                    2,
                ),
                "classification": _classification(path, standard_set),
            }
        )
    return rows


def _looks_like_stream_object(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    keys = {str(key).casefold() for key in value}
    return bool(keys & {"data", "values", "points", "samples"}) and bool(
        keys & {"name", "stream", "stream_name", "type"}
    )


def _looks_like_points(value: Any) -> bool:
    if isinstance(value, Mapping):
        keys = {str(key).casefold() for key in value}
        return bool(keys & {"data", "values", "points", "samples"})
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        return False
    return not value or not isinstance(value[0], Mapping)


def _looks_like_stream_mapping(value: Mapping[str, Any]) -> bool:
    if not value:
        return True
    return all(_looks_like_points(item) for item in value.values())


def _activity_payloads(activity_results: Any) -> list[Any]:
    if isinstance(activity_results, Mapping):
        if "streams" in activity_results:
            return [activity_results]
        if _looks_like_stream_mapping(activity_results):
            return [{"streams": activity_results}]
        return list(activity_results.values())

    if isinstance(activity_results, Sequence) and not isinstance(
        activity_results, (str, bytes, bytearray)
    ):
        values = list(activity_results)
        if values and all(_looks_like_stream_object(item) for item in values):
            return [{"streams": values}]
        return values
    raise TypeError("activity_results must be a mapping or a sequence")


def _point_values(value: Any) -> list[Any]:
    if isinstance(value, Mapping):
        for key in ("data", "values", "points", "samples"):
            if key in value:
                return _point_values(value[key])
        return []
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return list(value)
    return []


def _safe_metadata_label(value: Any) -> str | None:
    if value is None or isinstance(value, (Mapping, list, tuple, set)):
        return None
    label = str(value).strip()
    if not label or not _SAFE_METADATA_RE.fullmatch(label):
        return None
    if _is_sensitive_key(label) or _EMAIL_RE.search(label):
        return None
    return label


def _safe_stream_name(value: Any) -> str | None:
    if value is None:
        return None
    label = str(value).strip()
    if not label or not _SAFE_METADATA_RE.fullmatch(label):
        return None
    compact = _compact_key(label)
    if compact in _SAFE_LOCATION_STREAM_NAMES:
        return label
    return _safe_metadata_label(label)


def _stream_from_object(
    value: Mapping[str, Any],
    fallback_name: str | None = None,
) -> tuple[str, list[Any], str | None, str | None] | None:
    name_value = (
        value.get("name")
        or value.get("stream_name")
        or value.get("stream")
        or fallback_name
    )
    type_is_name = False
    if name_value is None and value.get("type") is not None:
        name_value = value.get("type")
        type_is_name = True

    name = _safe_stream_name(name_value)
    if not name:
        return None

    value_type = (
        value.get("value_type")
        or value.get("data_type")
        or value.get("datatype")
    )
    if value_type is None and not type_is_name and value.get("type") != name:
        value_type = value.get("type")
    unit = value.get("unit") or value.get("units")
    return (
        name,
        _point_values(value),
        _safe_metadata_label(value_type),
        _safe_metadata_label(unit),
    )


def _parse_streams(
    activity_payload: Any,
) -> list[tuple[str, list[Any], str | None, str | None]]:
    if isinstance(activity_payload, Mapping) and "streams" in activity_payload:
        container = activity_payload["streams"]
    else:
        container = activity_payload

    parsed: list[tuple[str, list[Any], str | None, str | None]] = []
    if isinstance(container, Mapping):
        if _looks_like_stream_object(container):
            item = _stream_from_object(container)
            if item is not None:
                parsed.append(item)
            return parsed

        for raw_name, raw_value in container.items():
            name = str(raw_name)
            safe_name = _safe_stream_name(name)
            if safe_name is None:
                continue
            if isinstance(raw_value, Mapping):
                item = _stream_from_object(
                    raw_value, fallback_name=safe_name
                )
                if item is not None:
                    parsed.append(item)
            else:
                parsed.append(
                    (safe_name, _point_values(raw_value), None, None)
                )
        return parsed

    if isinstance(container, Sequence) and not isinstance(
        container, (str, bytes, bytearray)
    ):
        for raw_value in container:
            if not isinstance(raw_value, Mapping):
                continue
            item = _stream_from_object(raw_value)
            if item is not None:
                parsed.append(item)
    return parsed


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    if not isinstance(value, str):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp()
    except (ValueError, OverflowError):
        return None


def _estimate_frequency(time_values: Sequence[Any]) -> float | None:
    if len(time_values) < 3:
        return None
    seconds = [_seconds(value) for value in time_values]
    if any(value is None for value in seconds):
        return None
    numeric = [value for value in seconds if value is not None]
    deltas = [
        numeric[index] - numeric[index - 1]
        for index in range(1, len(numeric))
    ]
    if not deltas or any(delta <= 0 for delta in deltas):
        return None
    mean_delta = sum(deltas) / len(deltas)
    if mean_delta <= 0:
        return None
    if len(deltas) > 1 and pstdev(deltas) / mean_delta > 0.15:
        return None
    median_delta = median(deltas)
    if median_delta <= 0:
        return None
    return 1.0 / median_delta


def _combined_frequency(rates: Sequence[float]) -> float | None:
    if not rates:
        return None
    typical = median(rates)
    if typical <= 0:
        return None
    if any(abs(rate - typical) / typical > 0.15 for rate in rates):
        return None
    return round(typical, 6)


def summarize_streams(
    activity_results: Any,
    *,
    max_activities: int = 5,
) -> list[dict[str, Any]]:
    """Summarize streams without exposing activity IDs or stream point values.

    Frequency is reported only when an activity has at least three stable,
    increasing time samples and the target stream has the same number of
    points.  Estimates that disagree by more than 15% across activities are
    omitted.
    """

    if max_activities < 1:
        raise ValueError("max_activities must be at least 1")

    accumulators: dict[str, dict[str, Any]] = {}
    for activity in _activity_payloads(activity_results)[:max_activities]:
        streams = _parse_streams(activity)
        time_points: list[Any] | None = None
        for name, points, _value_type, _unit in streams:
            normalised_name = _compact_key(name)
            if normalised_name in {
                "elapsedtime",
                "time",
                "times",
                "timestamp",
                "timestamps",
            }:
                time_points = points
                break
        time_rate = (
            _estimate_frequency(time_points) if time_points is not None else None
        )

        seen_in_activity: set[str] = set()
        for name, points, value_type, unit in streams:
            key = name.casefold()
            accumulator = accumulators.setdefault(
                key,
                {
                    "name": name,
                    "value_types": set(),
                    "units": set(),
                    "activity_count": 0,
                    "total_points": 0,
                    "rates": [],
                },
            )
            if key not in seen_in_activity:
                accumulator["activity_count"] += 1
                seen_in_activity.add(key)
            accumulator["total_points"] += len(points)
            if value_type:
                accumulator["value_types"].add(value_type)
            if unit:
                accumulator["units"].add(unit)
            if (
                time_rate is not None
                and time_points is not None
                and len(points) == len(time_points)
            ):
                accumulator["rates"].append(time_rate)

    rows: list[dict[str, Any]] = []
    for key in sorted(accumulators):
        accumulator = accumulators[key]
        rows.append(
            {
                "stream_name": accumulator["name"],
                "value_type": (
                    ", ".join(sorted(accumulator["value_types"])) or None
                ),
                "unit": ", ".join(sorted(accumulator["units"])) or None,
                "activity_count": accumulator["activity_count"],
                "total_points": accumulator["total_points"],
                "estimated_frequency_hz": _combined_frequency(
                    accumulator["rates"]
                ),
            }
        )
    return rows


def _safe_int(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(result, 0)


def _safe_float(value: Any, *, maximum: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or result < 0:
        return None
    if maximum is not None:
        result = min(result, maximum)
    return result


def _safe_path(path: Any) -> str | None:
    value = str(path or "").strip()
    if not value or any(ord(character) < 32 for character in value):
        return None
    if _EMAIL_RE.search(value) or not _SAFE_JSON_PATH_RE.fullmatch(value):
        return None
    segments = [segment for segment in re.split(r"[.\[\]]+", value) if segment]
    ancestors: list[str] = []
    for segment in segments:
        if _is_sensitive_key(segment, ancestors):
            return None
        ancestors.append(segment)
    return value


def _neutralize_csv_formula(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    if value[0] in "\t\r\n":
        return f"'{value}"
    visible = value.lstrip()
    if visible and visible[0] in "=+-@":
        return f"'{value}"
    return value


def _project_coverage_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    path = _safe_path(row.get("json_path"))
    if path is None:
        return None
    value_types = _safe_metadata_label(row.get("value_types")) or ""
    classification = str(row.get("classification", "")).casefold()
    if classification not in {"standard", "unknown/custom"}:
        classification = "unknown/custom"
    coverage = _safe_float(row.get("coverage_percent"), maximum=100.0)
    return {
        "json_path": path,
        "source_endpoint": _safe_source_endpoint(row.get("source_endpoint")),
        "value_types": value_types,
        "records_present": _safe_int(row.get("records_present")),
        "non_empty_records": _safe_int(row.get("non_empty_records")),
        "coverage_percent": coverage if coverage is not None else 0.0,
        "classification": classification,
    }


def _project_stream_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    name = _safe_stream_name(row.get("stream_name"))
    if not name:
        return None
    return {
        "stream_name": name,
        "value_type": _safe_metadata_label(row.get("value_type")),
        "unit": _safe_metadata_label(row.get("unit")),
        "activity_count": _safe_int(row.get("activity_count")),
        "total_points": _safe_int(row.get("total_points")),
        "estimated_frequency_hz": _safe_float(
            row.get("estimated_frequency_hz")
        ),
    }


def _safe_report_text(value: Any, *, maximum: int = 500) -> str:
    rendered = _redact_embedded_secrets(str(value or ""))
    rendered = "".join(
        character for character in rendered if ord(character) >= 32
    ).strip()
    return rendered[:maximum]


def _project_endpoint_row(
    row: Mapping[str, Any],
) -> dict[str, Any] | None:
    category = _safe_metadata_label(row.get("category"))
    if not category:
        return None
    raw_status = row.get("http_status")
    status = (
        int(raw_status)
        if isinstance(raw_status, int) and 100 <= raw_status <= 599
        else None
    )
    raw_fields = row.get("field_names", ())
    if isinstance(raw_fields, str):
        raw_fields = [item.strip() for item in raw_fields.split(",")]
    if not isinstance(raw_fields, Sequence):
        raw_fields = ()
    field_names = sorted(
        {
            safe_path
            for item in raw_fields
            if (safe_path := _safe_path(item)) is not None
        }
    )
    return {
        "category": category,
        "endpoint": _safe_source_endpoint(row.get("endpoint")),
        "http_status": status,
        "available": bool(row.get("available")),
        "record_count": _safe_int(row.get("record_count")),
        "field_names": ", ".join(field_names),
        "safe_error": _safe_report_text(row.get("safe_error")),
    }


def _project_mapping_row(
    row: Mapping[str, Any],
) -> dict[str, Any] | None:
    target_field = _safe_report_text(row.get("target_field"), maximum=96)
    if not target_field:
        return None
    status = str(row.get("status", "")).casefold()
    if status not in {"direct", "derived", "manual", "missing"}:
        status = "missing"
    return {
        "target_field": target_field,
        "status": status,
        "matched_source_fields": _safe_report_text(
            row.get("matched_source_fields"), maximum=500
        ),
        "missing_source_fields": _safe_report_text(
            row.get("missing_source_fields"), maximum=500
        ),
        "model_consumers": _safe_report_text(
            row.get("model_consumers"), maximum=300
        ),
        "note": _safe_report_text(row.get("note"), maximum=500),
    }


def _project_model_row(
    row: Mapping[str, Any],
) -> dict[str, Any] | None:
    model = _safe_report_text(row.get("model"), maximum=160)
    if not model:
        return None
    readiness = str(row.get("readiness", "")).casefold()
    if readiness not in {"ready", "partial", "blocked"}:
        readiness = "blocked"
    return {
        "model": model,
        "readiness": readiness,
        "missing_or_limit": _safe_report_text(
            row.get("missing_or_limit"), maximum=500
        ),
    }


def _safe_export_rows(
    field_coverage: Iterable[Mapping[str, Any]],
    stream_summary: Iterable[Mapping[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    safe_fields = [
        projected
        for row in field_coverage
        if (projected := _project_coverage_row(row)) is not None
    ]
    safe_streams = [
        projected
        for row in (stream_summary or ())
        if (projected := _project_stream_row(row)) is not None
    ]
    return safe_fields, safe_streams


def export_inventory_json(
    field_coverage: Iterable[Mapping[str, Any]],
    stream_summary: Iterable[Mapping[str, Any]] | None = None,
    endpoint_checks: Iterable[Mapping[str, Any]] | None = None,
    mapping_report: Iterable[Mapping[str, Any]] | None = None,
    model_readiness: Iterable[Mapping[str, Any]] | None = None,
) -> str:
    """Return a JSON report containing only approved inventory columns."""

    safe_fields, safe_streams = _safe_export_rows(
        field_coverage, stream_summary
    )
    safe_endpoints = [
        projected
        for row in (endpoint_checks or ())
        if (projected := _project_endpoint_row(row)) is not None
    ]
    safe_mappings = [
        projected
        for row in (mapping_report or ())
        if (projected := _project_mapping_row(row)) is not None
    ]
    safe_models = [
        projected
        for row in (model_readiness or ())
        if (projected := _project_model_row(row)) is not None
    ]
    return json.dumps(
        {
            "endpoint_checks": safe_endpoints,
            "field_coverage": safe_fields,
            "mapping_report": safe_mappings,
            "model_readiness": safe_models,
            "streams": safe_streams,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def export_inventory_csv(
    field_coverage: Iterable[Mapping[str, Any]],
    stream_summary: Iterable[Mapping[str, Any]] | None = None,
    endpoint_checks: Iterable[Mapping[str, Any]] | None = None,
    mapping_report: Iterable[Mapping[str, Any]] | None = None,
    model_readiness: Iterable[Mapping[str, Any]] | None = None,
) -> str:
    """Return one CSV with separate field-coverage and stream rows."""

    safe_fields, safe_streams = _safe_export_rows(
        field_coverage, stream_summary
    )
    safe_endpoints = [
        projected
        for row in (endpoint_checks or ())
        if (projected := _project_endpoint_row(row)) is not None
    ]
    safe_mappings = [
        projected
        for row in (mapping_report or ())
        if (projected := _project_mapping_row(row)) is not None
    ]
    safe_models = [
        projected
        for row in (model_readiness or ())
        if (projected := _project_model_row(row)) is not None
    ]
    columns = (
        "report_section",
        *_FIELD_EXPORT_COLUMNS,
        *_STREAM_EXPORT_COLUMNS,
        *_ENDPOINT_EXPORT_COLUMNS,
        *_MAPPING_EXPORT_COLUMNS,
        *_MODEL_EXPORT_COLUMNS,
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in safe_fields:
        csv_row = {"report_section": "field_coverage", **row}
        writer.writerow(
            {key: _neutralize_csv_formula(value) for key, value in csv_row.items()}
        )
    for row in safe_streams:
        csv_row = {"report_section": "streams", **row}
        writer.writerow(
            {key: _neutralize_csv_formula(value) for key, value in csv_row.items()}
        )
    for row in safe_endpoints:
        csv_row = {"report_section": "endpoint_checks", **row}
        writer.writerow(
            {key: _neutralize_csv_formula(value) for key, value in csv_row.items()}
        )
    for row in safe_mappings:
        csv_row = {"report_section": "mapping_report", **row}
        writer.writerow(
            {key: _neutralize_csv_formula(value) for key, value in csv_row.items()}
        )
    for row in safe_models:
        csv_row = {"report_section": "model_readiness", **row}
        writer.writerow(
            {key: _neutralize_csv_formula(value) for key, value in csv_row.items()}
        )
    return output.getvalue()
