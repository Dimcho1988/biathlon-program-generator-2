"""Lightweight, diagnostic sprint/STR detection on the existing Vflat stream.

The detector never changes Vflat speed and never writes canonical load.  It
marks only the short high-speed core of a candidate effort.  A centred local
median is used as a detection reference, not as a replacement speed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pandas as pd


SPRINT_STR_MODEL_VERSION = "vflat_sprint_str_v1"
SPRINT_STR_CONFIG_VERSION = "vflat_sprint_str_config_v1"


@dataclass(frozen=True, slots=True)
class SprintSTRConfig:
    config_version: str = SPRINT_STR_CONFIG_VERSION
    reference_window_s: int = 61
    min_raw_speed_kmh: float = 24.0
    min_speed_rise_kmh: float = 12.0
    min_grade_pct: float = -3.0
    min_onset_accel_mps2: float = 0.10
    onset_lookback_s: int = 8
    min_core_duration_s: int = 5
    max_core_duration_s: int = 25
    max_bridge_gap_s: int = 2

    def __post_init__(self) -> None:
        if self.config_version != SPRINT_STR_CONFIG_VERSION:
            raise ValueError("unsupported sprint/STR config version")
        if self.reference_window_s <= 0 or self.reference_window_s % 2 == 0:
            raise ValueError("sprint/STR reference window must be positive and odd")
        if self.min_raw_speed_kmh <= 0.0 or self.min_speed_rise_kmh <= 0.0:
            raise ValueError("sprint/STR speed thresholds must be positive")
        if self.min_onset_accel_mps2 <= 0.0:
            raise ValueError("sprint/STR onset acceleration must be positive")
        if self.onset_lookback_s < 0 or self.max_bridge_gap_s < 0:
            raise ValueError("sprint/STR lookback and bridge gap must be non-negative")
        if not 0 < self.min_core_duration_s <= self.max_core_duration_s:
            raise ValueError("invalid sprint/STR duration interval")

    def to_dict(self) -> dict[str, float | int | str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SprintSTRDetection:
    sample_mask: tuple[bool, ...]
    local_reference_kmh: tuple[float | None, ...]
    speed_rise_kmh: tuple[float | None, ...]
    intervals: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def _plain(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _timestamp(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _elapsed_seconds(timestamps: pd.Series) -> np.ndarray:
    parsed = pd.to_datetime(timestamps, utc=True, errors="coerce")
    if parsed.isna().all():
        return np.arange(len(timestamps), dtype=float)
    origin = parsed.dropna().iloc[0]
    elapsed = (parsed - origin).dt.total_seconds().to_numpy(dtype=float)
    fallback = np.arange(len(timestamps), dtype=float)
    return np.where(np.isfinite(elapsed), elapsed, fallback)


def detect_sprint_str(
    vflat_timeseries: pd.DataFrame,
    config: SprintSTRConfig | None = None,
) -> SprintSTRDetection:
    """Detect short effort cores without modifying the Vflat result.

    The speed jump must occur at a grade of at least -3% and must have a real
    positive-acceleration onset in the preceding few seconds.  This rejects a
    high speed that merely persists after a steeper descent.
    """
    selected = config or SprintSTRConfig()
    required = {
        "timestamp",
        "block",
        "speed_raw_kmh",
        "vflat_b65_kmh",
        "grade_actual_pct",
        "accel_mps2",
        "valid",
    }
    missing = required.difference(vflat_timeseries.columns)
    if missing:
        raise ValueError(f"Missing sprint/STR columns: {sorted(missing)}")

    frame = vflat_timeseries.reset_index(drop=True)
    count = len(frame)
    speed = frame.speed_raw_kmh.to_numpy(dtype=float)
    grade = frame.grade_actual_pct.to_numpy(dtype=float)
    accel = frame.accel_mps2.to_numpy(dtype=float)
    blocks = frame.block.to_numpy()
    valid = frame.valid.astype(bool).to_numpy()
    elapsed = _elapsed_seconds(frame.timestamp)

    reference = np.full(count, np.nan, dtype=float)
    for _, positions in frame.groupby("block", sort=False).indices.items():
        positions = np.asarray(positions, dtype=int)
        part = pd.Series(speed[positions])
        window = min(selected.reference_window_s, len(part))
        if window % 2 == 0:
            window = max(1, window - 1)
        reference[positions] = part.rolling(
            window,
            center=True,
            min_periods=max(1, window // 3),
        ).median().to_numpy(dtype=float)
    rise = speed - reference

    allowed = valid & np.isfinite(grade) & (grade >= selected.min_grade_pct)
    candidate = (
        allowed
        & np.isfinite(speed)
        & np.isfinite(rise)
        & (speed >= selected.min_raw_speed_kmh)
        & (rise >= selected.min_speed_rise_kmh)
    )

    # Fill only very short holes inside the same continuous, allowed block.
    active_positions = np.flatnonzero(candidate)
    for left, right in zip(active_positions[:-1], active_positions[1:], strict=False):
        gap = right - left - 1
        if (
            0 < gap <= selected.max_bridge_gap_s
            and blocks[left] == blocks[right]
            and elapsed[right] - elapsed[left] <= selected.max_bridge_gap_s + 1.0
            and np.all(allowed[left : right + 1])
        ):
            candidate[left : right + 1] = True

    mask = np.zeros(count, dtype=bool)
    intervals: list[dict[str, Any]] = []
    start: int | None = None
    for position in range(count + 1):
        active = position < count and candidate[position]
        continuous = (
            start is not None
            and position < count
            and position > start
            and blocks[position] == blocks[position - 1]
            and elapsed[position] - elapsed[position - 1] <= 1.5
        )
        if active and start is None:
            start = position
            continue
        if start is None or (active and continuous):
            continue

        end = position - 1
        duration = elapsed[end] - elapsed[start] + 1.0
        lookback_start = start
        while (
            lookback_start > 0
            and blocks[lookback_start - 1] == blocks[start]
            and elapsed[start] - elapsed[lookback_start - 1] <= selected.onset_lookback_s
        ):
            lookback_start -= 1
        onset = (
            allowed[lookback_start : start + 1]
            & np.isfinite(accel[lookback_start : start + 1])
            & (accel[lookback_start : start + 1] >= selected.min_onset_accel_mps2)
        )
        accepted = (
            selected.min_core_duration_s <= duration <= selected.max_core_duration_s
            and bool(np.any(onset))
        )
        if accepted:
            mask[start : end + 1] = True
            interval_speed = speed[start : end + 1]
            interval_vflat = frame.vflat_b65_kmh.to_numpy(dtype=float)[start : end + 1]
            interval_grade = grade[start : end + 1]
            intervals.append(
                {
                    "sprint_id": len(intervals) + 1,
                    "stimulus": "SPRINT_STR",
                    "start_timestamp": _timestamp(frame.timestamp.iloc[start]),
                    "end_timestamp": _timestamp(frame.timestamp.iloc[end]),
                    "start_elapsed_s": float(elapsed[start]),
                    "end_elapsed_s": float(elapsed[end]),
                    "duration_s": float(duration),
                    "sample_count": int(end - start + 1),
                    "peak_raw_speed_kmh": float(np.nanmax(interval_speed)),
                    "mean_raw_speed_kmh": float(np.nanmean(interval_speed)),
                    "peak_vflat_b65_kmh": float(np.nanmax(interval_vflat)),
                    "mean_vflat_b65_kmh": float(np.nanmean(interval_vflat)),
                    "local_reference_kmh": float(np.nanmedian(reference[start : end + 1])),
                    "max_speed_rise_kmh": float(np.nanmax(rise[start : end + 1])),
                    "mean_grade_pct": float(np.nanmean(interval_grade)),
                    "min_grade_pct": float(np.nanmin(interval_grade)),
                    "peak_acceleration_mps2": float(
                        np.nanmax(accel[lookback_start : end + 1])
                    ),
                    "affects_canonical_load": False,
                }
            )
        start = position if active else None

    active_seconds = float(sum(item["duration_s"] for item in intervals))
    summary = {
        "status": "computed",
        "model_version": SPRINT_STR_MODEL_VERSION,
        "config_version": SPRINT_STR_CONFIG_VERSION,
        "candidate_count": len(intervals),
        "candidate_active_seconds": active_seconds,
        "mean_core_duration_s": (
            active_seconds / len(intervals) if intervals else 0.0
        ),
        "peak_raw_speed_kmh": (
            max(item["peak_raw_speed_kmh"] for item in intervals)
            if intervals
            else None
        ),
        "affects_canonical_load": False,
        "double_counts_hr_zones": False,
        "config": selected.to_dict(),
    }
    return SprintSTRDetection(
        sample_mask=tuple(bool(value) for value in mask),
        local_reference_kmh=tuple(_plain(value) for value in reference),
        speed_rise_kmh=tuple(_plain(value) for value in rise),
        intervals=tuple(intervals),
        summary=summary,
    )


__all__ = [
    "SPRINT_STR_CONFIG_VERSION",
    "SPRINT_STR_MODEL_VERSION",
    "SprintSTRConfig",
    "SprintSTRDetection",
    "detect_sprint_str",
]
