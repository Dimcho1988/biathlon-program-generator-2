"""Privacy-minimized activity catalog metadata and stable provider identity."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import hashlib
import hmac
import math
import re
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ACTIVITY_REF_PATTERN = re.compile(r"^act_[a-f0-9]{32}$")
LEGACY_SHADOW_REF_PATTERN = re.compile(r"^shadow-[a-f0-9]{32}$")
CATALOG_SCHEMA_VERSION = "activity-catalog-v1"
SERIES_SCHEMA_VERSION = "activity-series-v1"


def provider_activity_key(
    *, provider_athlete_id: str, provider_activity_id: str, secret: str
) -> str:
    """Return a server-only join key without retaining either provider ID."""

    if not provider_athlete_id or not provider_activity_id or not secret:
        raise ValueError("provider activity identity is incomplete")
    message = f"intervals\0{provider_athlete_id}\0{provider_activity_id}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    rendered = "".join(
        character if character >= " " else " " for character in value
    ).strip()
    return rendered[:limit] or None


def _number(value: Any, *, minimum: float = 0.0) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        rendered = float(value)
    except (TypeError, ValueError):
        return None
    return rendered if math.isfinite(rendered) and rendered >= minimum else None


def _seconds(detail: Mapping[str, Any], name: str) -> int | None:
    value = _number(detail.get(name))
    return round(value) if value is not None else None


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.isoformat()
    return parsed.astimezone(UTC).isoformat()


def _local_start(detail: Mapping[str, Any]) -> tuple[str | None, str | None, str | None, int | None]:
    utc_value = _timestamp(detail.get("start_date"))
    local_raw = detail.get("start_date_local")
    local_value = _timestamp(local_raw)
    timezone_name = _text(detail.get("timezone"), 80)
    offset_minutes: int | None = None
    if utc_value and isinstance(local_raw, str):
        try:
            utc_start = datetime.fromisoformat(utc_value)
            local_start = datetime.fromisoformat(local_raw.strip().replace("Z", "+00:00"))
            if local_start.tzinfo is not None:
                offset = local_start.utcoffset()
                offset_minutes = round(offset.total_seconds() / 60) if offset else 0
                local_value = local_start.replace(tzinfo=None).isoformat()
            elif timezone_name:
                local_value = local_start.isoformat()
                offset = local_start.replace(tzinfo=ZoneInfo(timezone_name)).utcoffset()
                offset_minutes = round(offset.total_seconds() / 60) if offset else 0
            else:
                offset_minutes = round(
                    (local_start.replace(tzinfo=UTC) - utc_start).total_seconds() / 60
                )
        except (ValueError, ZoneInfoNotFoundError):
            offset_minutes = None
    if utc_value is None and isinstance(local_raw, str) and timezone_name:
        try:
            local_start = datetime.fromisoformat(local_raw.strip())
            aware = local_start.replace(tzinfo=ZoneInfo(timezone_name))
            utc_value = aware.astimezone(UTC).isoformat()
            local_value = local_start.isoformat()
            offset = aware.utcoffset()
            offset_minutes = round(offset.total_seconds() / 60) if offset else 0
        except (ValueError, ZoneInfoNotFoundError):
            pass
    return utc_value, local_value, timezone_name, offset_minutes


def _intervals(detail: Mapping[str, Any]) -> list[dict[str, Any]]:
    source = detail.get("intervals")
    if not isinstance(source, Sequence) or isinstance(source, (str, bytes, bytearray)):
        return []
    result: list[dict[str, Any]] = []
    for item in source[:200]:
        if not isinstance(item, Mapping):
            continue
        result.append(
            {
                "name": _text(item.get("name") or item.get("label"), 120),
                "start_index": item.get("start_index") if isinstance(item.get("start_index"), int) else None,
                "end_index": item.get("end_index") if isinstance(item.get("end_index"), int) else None,
                "elapsed_time_s": _seconds(item, "elapsed_time"),
                "moving_time_s": _seconds(item, "moving_time"),
                "distance_m": _number(item.get("distance")),
                "average_hr_bpm": _number(item.get("average_heartrate")),
                "max_hr_bpm": _number(item.get("max_heartrate")),
            }
        )
    return result


def extract_activity_metadata(activity_ref: str, detail: Mapping[str, Any]) -> dict[str, Any]:
    """Select only catalog fields; never copy a full provider payload."""

    if not ACTIVITY_REF_PATTERN.fullmatch(activity_ref):
        raise ValueError("invalid canonical activity reference")
    start_utc, start_local, timezone_name, offset_minutes = _local_start(detail)
    sport_type = _text(detail.get("type") or detail.get("sport"), 48) or "Activity"
    local_date = start_local[:10] if start_local else (start_utc[:10] if start_utc else None)
    return {
        "activity_ref": activity_ref,
        "start_at_utc": start_utc,
        "start_local": start_local,
        "local_date": local_date,
        "timezone": timezone_name,
        "utc_offset_minutes": offset_minutes,
        "sport": sport_type,
        "activity_type": _text(detail.get("type"), 48),
        "activity_sub_type": _text(detail.get("sub_type"), 48),
        "name": _text(detail.get("name"), 160),
        "description": _text(detail.get("description"), 8000),
        "moving_time_s": _seconds(detail, "moving_time"),
        "elapsed_time_s": _seconds(detail, "elapsed_time"),
        "recording_time_s": _seconds(detail, "icu_recording_time"),
        "distance_m": _number(detail.get("distance")),
        "elevation_gain_m": _number(detail.get("total_elevation_gain")),
        "average_hr_bpm": _number(detail.get("average_heartrate")),
        "max_hr_bpm": _number(detail.get("max_heartrate")),
        "average_speed_mps": _number(detail.get("average_speed")),
        "max_speed_mps": _number(detail.get("max_speed")),
        "provider_training_load": _number(detail.get("icu_training_load")),
        "provider_created_at": _timestamp(detail.get("created")),
        "provider_sync_at": _timestamp(detail.get("icu_sync_date")),
        "provider_analyzed_at": _timestamp(detail.get("analyzed")),
        "intervals": _intervals(detail),
    }


def downsample_model_input(payload: Mapping[str, Any], *, max_points: int = 3000) -> dict[str, Any]:
    """Publish display-safe scientific channels only when detail is opened."""

    samples = payload.get("samples")
    if not isinstance(samples, list):
        samples = []
    step = max(1, math.ceil(len(samples) / max_points))
    rows = []
    for sample in samples[::step]:
        if not isinstance(sample, Mapping):
            continue
        rows.append(
            {
                "timestamp": sample.get("timestamp"),
                "elapsed_s": sample.get("elapsed_s"),
                "hr_bpm": sample.get("hr_raw_bpm"),
                "speed_kmh": sample.get("speed_raw_kmh"),
                "altitude_m": sample.get("altitude_m"),
                "grade_pct": sample.get("grade_raw_pct"),
                "quality_flags": sample.get("quality_flags") or [],
            }
        )
    return {
        "schema_version": SERIES_SCHEMA_VERSION,
        "source_sample_count": len(samples),
        "returned_sample_count": len(rows),
        "downsample_step": step,
        "series": rows,
    }


def _zones(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    summary = row.get("canonical_summary")
    source = summary.get("zones") if isinstance(summary, Mapping) else None
    if not isinstance(source, list):
        return []
    result = []
    for item in source:
        if not isinstance(item, Mapping) or item.get("zone") not in {"Z1", "Z2", "Z3", "Z4", "Z5"}:
            continue
        result.append(
            {
                "zone": item["zone"],
                "raw_time_s": float(item.get("raw_time_s") or 0.0),
                "equivalent_time_s": float(item.get("equivalent_time_s") or 0.0),
                "effective_load": float(item.get("effective_load") or 0.0),
            }
        )
    return result


def _hrmod_zones(source: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    result = []
    for item in source or ():
        zone = item.get("zone_name")
        seconds = item.get("hrmod_final_seconds", item.get("hrmod_seconds"))
        if zone not in {"Z1", "Z2", "Z3", "Z4", "Z5"}:
            continue
        value = _number(seconds)
        if value is not None:
            result.append({"zone": zone, "final_time_s": value})
    return result


def calendar_item(
    row: Mapping[str, Any],
    hrmod_zone_summary: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    start_utc = str(row.get("start_at_utc") or "")
    start_local = str(row.get("start_local") or start_utc)
    local_date = str(row.get("local_date") or start_local[:10])
    summary = row.get("canonical_summary")
    duration = summary.get("duration_min") if isinstance(summary, Mapping) else None
    if duration is None:
        seconds = row.get("moving_time_s") or row.get("recording_time_s") or row.get("elapsed_time_s")
        duration = float(seconds) / 60.0 if seconds is not None else None
    canonical_zones = _zones(row)
    hrmod_zones = _hrmod_zones(hrmod_zone_summary)
    return {
        "activity_ref": str(row.get("activity_ref") or ""),
        "start_at_utc": start_utc,
        "start_local": start_local,
        "local_date": local_date,
        "local_time": start_local[11:16] if len(start_local) >= 16 else "--:--",
        "timezone": row.get("timezone"),
        "utc_offset_minutes": row.get("utc_offset_minutes"),
        "sport": str(row.get("sport") or "Activity"),
        "activity_type": row.get("activity_type"),
        "activity_sub_type": row.get("activity_sub_type"),
        "name": row.get("name"),
        "duration_min": float(duration) if duration is not None else None,
        "distance_m": row.get("distance_m"),
        "elevation_gain_m": row.get("elevation_gain_m"),
        "average_hr_bpm": row.get("average_hr_bpm"),
        "max_hr_bpm": row.get("max_hr_bpm"),
        "average_speed_mps": row.get("average_speed_mps"),
        "max_speed_mps": row.get("max_speed_mps"),
        "canonical_training_load": row.get("canonical_training_load"),
        "quality_status": row.get("quality_status") or "excluded",
        "quality_reason": row.get("quality_reason"),
        "hr_coverage_percent": row.get("hr_coverage_percent"),
        "shadow_available": bool(row.get("latest_shadow_run_key") or row.get("shadow_available")),
        "zones": canonical_zones,
        "hrmod_zones": hrmod_zones,
        "zone_visualization_source": (
            "hrmod_final"
            if hrmod_zones
            else "canonical_raw" if canonical_zones else "none"
        ),
    }


def activity_calendar_payload(
    *,
    athlete_alias: str,
    period_start: date,
    period_end: date,
    rows: Sequence[Mapping[str, Any]],
    shadow_zones: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    wellness_days: Sequence[Mapping[str, Any]] = (),
    wellness_status: Mapping[str, Any] | None = None,
    generation_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    zones_by_activity = shadow_zones or {}
    activities = [
        calendar_item(
            row,
            zones_by_activity.get(str(row.get("activity_ref") or "")),
        )
        for row in rows
        if row.get("start_at_utc")
    ]
    weeks: dict[date, dict[str, Any]] = {}
    for activity in activities:
        activity_date = date.fromisoformat(activity["local_date"])
        week_start = activity_date - timedelta(days=activity_date.weekday())
        week = weeks.setdefault(
            week_start,
            {
                "week_start": week_start.isoformat(),
                "week_end": (week_start + timedelta(days=6)).isoformat(),
                "activities_count": 0,
                "duration_min": 0.0,
                "distance_m": 0.0,
                "canonical_training_load": 0.0,
                "zones": {
                    zone: {"zone": zone, "raw_time_s": 0.0, "equivalent_time_s": 0.0, "effective_load": 0.0}
                    for zone in ("Z1", "Z2", "Z3", "Z4", "Z5")
                },
            },
        )
        week["activities_count"] += 1
        week["duration_min"] += activity["duration_min"] or 0.0
        week["distance_m"] += activity["distance_m"] or 0.0
        week["canonical_training_load"] += activity["canonical_training_load"] or 0.0
        for zone in activity["zones"]:
            target = week["zones"][zone["zone"]]
            for field in ("raw_time_s", "equivalent_time_s", "effective_load"):
                target[field] += zone[field]
    rendered_weeks = []
    for week in sorted(weeks.values(), key=lambda item: item["week_start"]):
        rendered_weeks.append({**week, "zones": list(week["zones"].values())})
    payload = {
        "schema_version": (
            "activity-calendar-index-v2"
            if generation_metadata is not None
            else "activity-calendar-index-v1"
        ),
        "athlete_id": athlete_alias,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "activities": activities,
        "weeks": rendered_weeks,
        "wellness_days": [dict(day) for day in wellness_days],
        "wellness_status": dict(wellness_status or {
            "state": "refresh_required",
            "records_received": 0,
            "stored_days": 0,
            "displayed_days": 0,
            "latest_observed_date": None,
        }),
        "wellness_integration": "DIAGNOSTIC_ONLY",
        "includes_timeseries": False,
    }
    if generation_metadata is not None:
        payload.update(
            {
                "generation_id": generation_metadata.get("generation_id"),
                "revision": int(generation_metadata.get("revision") or 0),
                "analysis_as_of": generation_metadata.get("analysis_as_of"),
                "activated_at": generation_metadata.get("activated_at"),
            }
        )
    return payload


def activity_detail_payload(
    row: Mapping[str, Any],
    hrmod_zone_summary: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    base = calendar_item(row, hrmod_zone_summary)
    return {
        **base,
        "schema_version": "activity-detail-v1",
        "description": row.get("description"),
        "moving_time_min": float(row["moving_time_s"]) / 60.0 if row.get("moving_time_s") is not None else None,
        "elapsed_time_min": float(row["elapsed_time_s"]) / 60.0 if row.get("elapsed_time_s") is not None else None,
        "recording_time_min": float(row["recording_time_s"]) / 60.0 if row.get("recording_time_s") is not None else None,
        "intervals": row.get("intervals") if isinstance(row.get("intervals"), list) else [],
        "previous_activity_ref": row.get("previous_activity_ref"),
        "next_activity_ref": row.get("next_activity_ref"),
    }
