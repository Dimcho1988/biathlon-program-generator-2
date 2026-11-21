"""Read original watch laps once; retain only their compact summaries.

The cache bundle lives in the catalog's existing intervals JSON. It is pinned
with that catalog, and never changes a scientific input or model result.
"""
from collections.abc import Mapping
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
import warnings
import xml.etree.ElementTree as ET
import zlib

VERSION = "device-laps-v1"
MAX_FILE_BYTES = 32 * 1024 * 1024


def catalog_laps(catalog):
    """Expose lap summaries, never the private file-cache envelope."""
    stored = catalog.get("intervals") or []
    bundle = next((r for r in stored if isinstance(r, Mapping) and r.get("source") == "device_laps"), {})
    intervals = [dict(r) for r in stored if isinstance(r, Mapping) and r.get("source") != "device_laps"]
    laps = [dict(r) for r in bundle.get("laps", []) if isinstance(r, Mapping)]
    return laps + intervals, bundle.get("status", "refresh_required")


def number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def timestamp(value):
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (TypeError, ValueError):
        return None


def _offset(value, origin):
    parsed = timestamp(value)
    return (parsed-origin).total_seconds() if parsed and origin else None


def _fit_laps(data, origin):
    import fitdecode
    laps = []
    with warnings.catch_warnings(), fitdecode.FitReader(io.BytesIO(data)) as reader:
        warnings.simplefilter("ignore", UserWarning)
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage) or frame.name != "lap":
                continue
            get = lambda key: frame.get_value(key, fallback=None)
            elapsed = number(get("total_elapsed_time"))
            start = _offset(get("start_time"), origin)
            end = start + elapsed if start is not None and elapsed is not None else _offset(get("timestamp"), origin)
            speed = number(get("enhanced_avg_speed"))
            if speed is None:
                speed = number(get("avg_speed"))
            laps.append({
                "name": None, "source": "device", "start_time_s": start, "end_time_s": end,
                "elapsed_time_s": elapsed, "timer_time_s": number(get("total_timer_time")),
                "moving_time_s": None, "distance_m": number(get("total_distance")),
                "average_hr_bpm": number(get("avg_heart_rate")), "max_hr_bpm": number(get("max_heart_rate")),
                "average_speed_kmh": speed * 3.6 if speed is not None else None,
                "intensity": str(get("intensity") or "").upper() or None,
            })
    return laps


def _tcx_laps(data, origin):
    # ElementTree does not fetch external entities. Reject entity declarations.
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Unsupported XML declaration")
    root = ET.fromstring(data)
    laps = []
    for lap in root.findall(".//{*}Lap"):
        def value(path):
            text = lap.findtext(path)
            try:
                return number(float(text)) if text is not None else None
            except ValueError:
                return None
        timer = value("{*}TotalTimeSeconds")
        start = _offset(lap.get("StartTime"), origin)
        track_times = [_offset(point.text, origin) for point in lap.findall(".//{*}Trackpoint/{*}Time")]
        end = max((t for t in track_times if t is not None), default=None)
        elapsed = end-start if start is not None and end is not None and end >= start else None
        distance = value("{*}DistanceMeters")
        laps.append({
            "source": "device", "name": None, "start_time_s": start,
            "end_time_s": end,
            "elapsed_time_s": elapsed, "timer_time_s": timer, "moving_time_s": None,
            "distance_m": distance, "average_hr_bpm": value("{*}AverageHeartRateBpm/{*}Value"),
            "max_hr_bpm": value("{*}MaximumHeartRateBpm/{*}Value"),
            "average_speed_kmh": None, "intensity": (lap.findtext("{*}Intensity") or "").upper() or None,
        })
    return laps


def parse_device_laps(data, detail):
    if not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES:
        raise ValueError("Invalid original file")
    if data.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as source:
            data = source.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("Expanded original file is too large")
    origin = timestamp(detail.get("start_date"))
    if origin is None:
        raise ValueError("Original activity start is unavailable")
    laps = _fit_laps(data, origin) if data[8:12] == b".FIT" else _tcx_laps(data, origin)
    duration = number(detail.get("elapsed_time"))
    # A multisport original file can contain other sessions. Do not attach
    # those laps to this activity or silently renumber a partially overlapping lap.
    return [lap for lap in laps if lap["start_time_s"] is not None and lap["start_time_s"] >= 0
            and (duration is None or lap["start_time_s"] <= duration)
            and (lap["end_time_s"] is None or (lap["end_time_s"] >= lap["start_time_s"]
                 and (duration is None or lap["end_time_s"] <= duration + 2)))]


def load_device_laps(client, detail, previous=()):
    # Keep the web/API import path independent of provider integrations.
    from intervals_inspector.intervals_client import IntervalsAPIError
    fingerprint = hashlib.sha256(json.dumps({key: detail.get(key) for key in (
        "start_date", "elapsed_time", "distance", "file_type", "file_sport_index", "icu_sync_date", "created",
    )}, sort_keys=True, default=str).encode()).hexdigest()
    for item in previous:
        if (isinstance(item, Mapping) and item.get("source") == "device_laps"
                and item.get("version") == VERSION and item.get("fingerprint") == fingerprint
                and item.get("status") in {"available", "empty", "unsupported", "unavailable"}):
            return dict(item)
    bundle = {"source": "device_laps", "version": VERSION, "fingerprint": fingerprint,
              "status": "unsupported", "laps": []}
    method = getattr(client, "get_activity_file_result", None)
    if (not callable(method) or str(detail.get("file_type") or "").lower().lstrip(".") not in {"fit", "tcx"}
            or detail.get("source") == "STRAVA"):
        return bundle
    try:
        laps = parse_device_laps(method(str(detail["id"])).payload, detail)
        return {**bundle, "status": "available" if laps else "empty", "laps": laps}
    except IntervalsAPIError as exc:
        # Optional original-file access cannot break an otherwise valid refresh.
        # Retry transient failures on the next refresh; cache denied/missing files.
        return {**bundle, "status": "unavailable" if exc.status_code in {403, 404, 410} else "retry"}
    except (ValueError, TypeError, KeyError, OSError, EOFError, zlib.error, ET.ParseError):
        return {**bundle, "status": "retry"}
    except Exception as exc:
        # Malformed FIT messages raise decoder-specific exceptions. Do not expose
        # file content, tokens or an optional decoder error to the main pipeline.
        if type(exc).__module__.startswith("fitdecode"):
            return {**bundle, "status": "retry"}
        raise
