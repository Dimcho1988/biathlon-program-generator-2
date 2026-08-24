"""One-pass activity shadow computation and immutable persistence payloads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from apps.api.shadow_models.hrmod_v4 import SOURCE_COMMIT, run_hrmod_v4_shadow
from apps.api.shadow_models.vflat_b65 import run_vflat_b65_shadow
from hrmod_lab.schemas import (
    CONFIG_VERSION as HRMOD_CONFIG_VERSION,
    MODEL_VERSION as HRMOD_MODEL_VERSION,
    AthleteHRProfile,
    HRInputSample,
    HRZone,
)
from hrmod_lab.tcx_adapter import ReferenceChannels, ReferenceSample
from hrmod_lab.terrain_gate import TERRAIN_CONFIG_VERSION, TERRAIN_MODEL_VERSION
from intervals_inspector.stream_normalizer import IntervalAwareResult, materialize_1hz
from vflat_b65 import (
    CONFIG_VERSION as VFLAT_CONFIG_VERSION,
    MODEL_VERSION as VFLAT_MODEL_VERSION,
    VFlatB65Config,
    derive_grade_from_altitude_distance,
)


INPUT_SCHEMA_VERSION = "activity-model-input-v1"
DERIVED_SCHEMA_VERSION = "activity-shadow-derived-v1"
SHADOW_CONFIGURATION_SCHEMA_VERSION = "activity-shadow-configuration-v1"

_HR_NAMES = ("heartrate", "fixed_heartrate", "heart_rate", "hr")
_SPEED_NAMES = ("velocity_smooth", "fixed_velocity_smooth", "speed", "velocity")
_GRADE_NAMES = ("gradient",)
_ALTITUDE_NAMES = ("altitude", "fixed_altitude")
_DISTANCE_NAMES = ("distance",)


def _first(point: Any, names: Sequence[str]) -> float | None:
    for name in names:
        value = point.value(name)
        if value is not None and math.isfinite(float(value)):
            return float(value)
    return None


def _start_time(detail: Mapping[str, Any]) -> tuple[datetime, tuple[str, ...]]:
    raw = detail.get("start_date") or detail.get("start_date_local")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("activity start timestamp is unavailable")
    parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC), ("NAIVE_TIMESTAMP_ASSUMED_UTC",)
    return parsed.astimezone(UTC), ()


def _canonical_hash(payload: Any) -> str:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def activity_shadow_configuration_fingerprint(
    zone_bounds_bpm: Sequence[int], explicit_hrmax_bpm: int | None
) -> str:
    """Identify every setting that can change a derived shadow result.

    The immutable activity input remains a hash of provider observations only.
    This separate fingerprint invalidates derived runs when athlete physiology or
    a shadow model/configuration changes.
    """
    payload = {
        "schema_version": SHADOW_CONFIGURATION_SCHEMA_VERSION,
        "zone_bounds_bpm": [int(value) for value in zone_bounds_bpm],
        "explicit_hrmax_bpm": explicit_hrmax_bpm,
        "vflat_model_version": VFLAT_MODEL_VERSION,
        "vflat_config_version": VFLAT_CONFIG_VERSION,
        "hrmod_model_version": HRMOD_MODEL_VERSION,
        "hrmod_config_version": HRMOD_CONFIG_VERSION,
        "hrmod_source_commit": SOURCE_COMMIT,
        "terrain_model_version": TERRAIN_MODEL_VERSION,
        "terrain_config_version": TERRAIN_CONFIG_VERSION,
    }
    return _canonical_hash(payload)


def _plain_number(value: float | None) -> float | None:
    return float(value) if value is not None and math.isfinite(float(value)) else None


def build_immutable_activity_input(
    detail: Mapping[str, Any],
    normalized: IntervalAwareResult,
) -> dict[str, Any]:
    start, timestamp_flags = _start_time(detail)
    rows = []
    for point in normalized.points:
        rows.append(
            {
                "timestamp": (start + timedelta(seconds=point.offset_sec)).isoformat(),
                "elapsed_s": point.offset_sec,
                "hr_raw_bpm": _first(point, _HR_NAMES),
                "speed_raw_kmh": (
                    _first(point, _SPEED_NAMES) * 3.6
                    if _first(point, _SPEED_NAMES) is not None
                    else None
                ),
                "grade_raw_pct": _first(point, _GRADE_NAMES),
                "altitude_m": _first(point, _ALTITUDE_NAMES),
                "cumulative_distance_m": _first(point, _DISTANCE_NAMES),
                "quality_flags": sorted(set(point.quality_flags) | set(timestamp_flags)),
            }
        )
    payload = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "normalization_version": normalized.algorithm_version,
        "samples": rows,
    }
    return {**payload, "input_hash": _canonical_hash(payload)}


def _model_inputs(detail: Mapping[str, Any], normalized: IntervalAwareResult):
    start, timestamp_flags = _start_time(detail)
    original_points = normalized.points
    original_timestamps = [
        start + timedelta(seconds=point.offset_sec) for point in original_points
    ]
    original_flags = [
        tuple(sorted(set(point.quality_flags) | set(timestamp_flags)))
        for point in original_points
    ]
    samples = tuple(
        HRInputSample(
            timestamp=stamp,
            heart_rate_bpm=_first(point, _HR_NAMES),
            quality_flags=flag,
        )
        for stamp, point, flag in zip(
            original_timestamps, original_points, original_flags, strict=True
        )
    )
    references = ReferenceChannels(
        samples=tuple(
            ReferenceSample(
                timestamp=stamp,
                elapsed_s=float(point.offset_sec),
                dt_s=(
                    0.0
                    if index == 0
                    else float(point.offset_sec - original_points[index - 1].offset_sec)
                ),
                speed_mps=_plain_number(_first(point, _SPEED_NAMES)),
                altitude_m=_plain_number(_first(point, _ALTITUDE_NAMES)),
                distance_m=_plain_number(_first(point, _DISTANCE_NAMES)),
                grade=_plain_number(_first(point, _GRADE_NAMES)),
            )
            for index, (stamp, point) in enumerate(
                zip(original_timestamps, original_points, strict=True)
            )
        ),
        available_channels=tuple(
            name
            for name, names in (
                ("speed_mps", _SPEED_NAMES),
                ("altitude_m", _ALTITUDE_NAMES),
                ("distance_m", _DISTANCE_NAMES),
                ("grade", _GRADE_NAMES),
            )
            if any(_first(point, names) is not None for point in original_points)
        ),
    )

    # Vflat is evaluated on the explicit temporary 1 Hz active view. HRmod is
    # deliberately evaluated above on original timestamps so irregular timing,
    # gaps and quality flags retain the exact v4 semantics.
    one_hz = materialize_1hz(normalized)
    points = one_hz.points
    timestamps = [start + timedelta(seconds=point.offset_sec) for point in points]
    speed = np.asarray([
        np.nan if _first(point, _SPEED_NAMES) is None else _first(point, _SPEED_NAMES)
        for point in points
    ], dtype=float)
    provider_grade = np.asarray([
        np.nan if _first(point, _GRADE_NAMES) is None else _first(point, _GRADE_NAMES)
        for point in points
    ], dtype=float)
    altitude = np.asarray([
        np.nan if _first(point, _ALTITUDE_NAMES) is None else _first(point, _ALTITUDE_NAMES)
        for point in points
    ], dtype=float)
    distance = np.asarray([
        np.nan if _first(point, _DISTANCE_NAMES) is None else _first(point, _DISTANCE_NAMES)
        for point in points
    ], dtype=float)
    derived_grade = derive_grade_from_altitude_distance(
        altitude, distance, smoothing_m=VFlatB65Config().altitude_smoothing_m
    )
    grade = np.where(np.isfinite(derived_grade), derived_grade, provider_grade)
    blocks = np.full(len(points), -1, dtype=int)
    for block_id, (left, right) in enumerate(one_hz.segment_slices):
        blocks[left:right] = block_id
    flags = [tuple(sorted(set(point.quality_flags) | set(timestamp_flags))) for point in points]
    smooth_speed = pd.Series(speed).rolling(
        15, center=True, min_periods=5
    ).median().to_numpy()
    acceleration = np.gradient(smooth_speed) if len(smooth_speed) else np.asarray([])
    vflat_frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "grade_pct": grade,
            "speed_raw_mps": speed,
            "speed_mps": smooth_speed,
            "accel_mps2": acceleration,
            "block": blocks,
            "turn_flag": np.zeros(len(points), dtype=bool),
            "quality_flags": flags,
        }
    )
    return samples, references, vflat_frame


def _profile(
    zone_bounds_bpm: Sequence[int], hrmax_bpm: int | None
) -> AthleteHRProfile | None:
    if hrmax_bpm is None:
        return None
    bounds = tuple(float(value) for value in zone_bounds_bpm)
    if len(bounds) != 6 or bounds[-1] > float(hrmax_bpm):
        raise ValueError("HR zones must lie at or below explicit HRmax")
    return AthleteHRProfile(
        hrmax_bpm=float(hrmax_bpm),
        hr_floor_bpm=bounds[0],
        zones=tuple(
            HRZone(f"Z{index + 1}", bounds[index], bounds[index + 1])
            for index in range(5)
        ),
    )


def _segments_15s(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        bucket = int(float(row["elapsed_s"]) // 15.0)
        buckets.setdefault(bucket, []).append(row)
    result = []
    for bucket, values in sorted(buckets.items()):
        def mean(name: str):
            available = [float(row[name]) for row in values if row.get(name) is not None]
            return sum(available) / len(available) if available else None
        result.append(
            {
                "segment_index": bucket,
                "start_elapsed_s": bucket * 15.0,
                "end_elapsed_s": (bucket + 1) * 15.0,
                "sample_count": len(values),
                "speed_raw_kmh": mean("speed_raw_kmh"),
                "vflat_b65_kmh": mean("vflat_b65_kmh"),
                "hr_raw_bpm": mean("hr_raw_bpm"),
                "hrmod_final_bpm": mean("hrmod_final_bpm"),
                "added_bpm": mean("added_bpm"),
                "removed_bpm": mean("removed_bpm"),
                "grade_smoothed_pct": mean("grade_smoothed_pct"),
            }
        )
    return result


def compute_activity_shadow(
    *,
    detail: Mapping[str, Any],
    normalized: IntervalAwareResult,
    zone_bounds_bpm: Sequence[int],
    explicit_hrmax_bpm: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    immutable_input = build_immutable_activity_input(detail, normalized)
    samples, references, vflat_frame = _model_inputs(
        detail, normalized
    )
    vflat = run_vflat_b65_shadow(vflat_frame)
    profile = _profile(zone_bounds_bpm, explicit_hrmax_bpm)
    configuration_fingerprint = activity_shadow_configuration_fingerprint(
        zone_bounds_bpm, explicit_hrmax_bpm
    )
    if profile is None:
        hrmod = {
            "status": "excluded",
            "exclusion_reason": "EXPLICIT_HRMAX_MISSING",
            "model_version": HRMOD_MODEL_VERSION,
            "config_version": HRMOD_CONFIG_VERSION,
            "terrain_model_version": TERRAIN_MODEL_VERSION,
            "source_commit": SOURCE_COMMIT,
            "timeseries": [{} for _ in samples],
            "waves": [],
            "zones": [],
            "diagnostics": {"flags": ["EXPLICIT_HRMAX_MISSING"]},
        }
    else:
        hrmod = run_hrmod_v4_shadow(
            hr_samples=samples,
            athlete_profile=profile,
            reference_channels=references,
        )
    vflat_by_timestamp = {
        str(row.get("timestamp")): row for row in vflat["timeseries"]
    }
    rows = []
    for index, sample in enumerate(samples):
        hr_row = hrmod["timeseries"][index] if index < len(hrmod["timeseries"]) else {}
        vf_row = vflat_by_timestamp.get(sample.timestamp.isoformat(), {})
        combined_flags = sorted(
            set(sample.quality_flags)
            | set(hr_row.get("quality_flags") or ())
            | set(vf_row.get("quality_flags") or ())
        )
        exclusion = (
            hr_row.get("exclusion_reason")
            or (hrmod.get("exclusion_reason") if hrmod.get("status") == "excluded" else None)
            or vf_row.get("exclusion_reason")
        )
        rows.append(
            {
                "timestamp": sample.timestamp.isoformat(),
                "elapsed_s": references.samples[index].elapsed_s,
                "speed_raw_kmh": (
                    vf_row.get("speed_raw_kmh")
                    if vf_row
                    else (
                        references.samples[index].speed_mps * 3.6
                        if references.samples[index].speed_mps is not None
                        else None
                    )
                ),
                "vflat_b65_kmh": vf_row.get("vflat_b65_kmh"),
                "vflat_delta_kmh": vf_row.get("vflat_delta_kmh"),
                "hr_raw_bpm": hr_row.get("hr_raw_bpm", sample.heart_rate_bpm),
                "hr_clean_bpm": hr_row.get("hr_clean_bpm"),
                "hrmod_candidate_bpm": hr_row.get("hrmod_candidate_bpm"),
                "hrmod_final_bpm": hr_row.get("hrmod_final_bpm"),
                "hrmod_delta_bpm": hr_row.get("hrmod_delta_bpm"),
                "added_bpm": hr_row.get("added_bpm"),
                "removed_bpm": hr_row.get("removed_bpm"),
                "receiver_flag": hr_row.get("receiver_flag", False),
                "donor_flag": hr_row.get("donor_flag", False),
                "wave_id": hr_row.get("wave_id"),
                "grade_raw_pct": hr_row.get(
                    "grade_raw_pct", references.samples[index].grade
                ),
                "grade_smoothed_pct": hr_row.get(
                    "grade_smoothed_pct", vf_row.get("grade_smoothed_pct")
                ),
                "vflat_model_version": VFLAT_MODEL_VERSION,
                "hrmod_model_version": hrmod.get("model_version"),
                "terrain_model_version": hrmod.get("terrain_model_version"),
                "quality_flags": combined_flags,
                "model_flags": hr_row.get("model_flags", []),
                "exclusion_reason": exclusion,
            }
        )
    payload = {
        "schema_version": DERIVED_SCHEMA_VERSION,
        "experimental": True,
        "affects_canonical_load": False,
        "input_hash": immutable_input["input_hash"],
        "configuration_fingerprint": configuration_fingerprint,
        "vflat_model_version": VFLAT_MODEL_VERSION,
        "vflat_config_version": VFLAT_CONFIG_VERSION,
        "hrmod_model_version": hrmod.get("model_version"),
        "hrmod_config_version": hrmod.get("config_version"),
        "hrmod_source_commit": hrmod.get("source_commit"),
        "terrain_model_version": hrmod.get("terrain_model_version"),
        "timeseries": rows,
        "segments_15s": _segments_15s(rows),
        "hrmod_waves": hrmod.get("waves", []),
        "zone_summary": hrmod.get("zones", []),
        "diagnostics": {
            "hrmod": hrmod.get("diagnostics", {}),
            "vflat": {"status": vflat.get("status")},
        },
        "hashes": {
            "hr_input_hash": hrmod.get("hr_input_hash"),
            "terrain_input_hash": hrmod.get("terrain_input_hash"),
            "hrmod_final_result_hash": hrmod.get("final_result_hash"),
        },
    }
    return immutable_input, {**payload, "result_hash": _canonical_hash(payload)}
