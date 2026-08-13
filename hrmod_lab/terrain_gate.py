"""Post-core terrain gating for the standalone HRmod Lab.

``terrain_gate_v1`` is deliberately outside the HR-only model boundary.  It
consumes an already completed :class:`~hrmod_lab.schemas.HRmodResult` plus the
physically separate TCX reference channels, and never calls wave detection or
area redistribution.  Grade is either read from a sufficiently complete TCX
grade channel or derived from altitude and cumulative distance in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.ndimage import median_filter

from .schemas import HRmodResult
from .tcx_adapter import ReferenceChannels


TERRAIN_MODEL_VERSION = "terrain_gate_v1"
TERRAIN_CONFIG_VERSION = "terrain_gate_config_v1"


def _finite(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        _plain(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class TerrainGateConfig:
    """Versioned, post-core terrain settings.

    ``grade_smoothing_window_s`` is a centred running median.  Derived grade
    first applies a five-point altitude median, then uses the altitude change
    across a centred distance span.  Both operations suppress isolated GPS or
    barometric spikes without inventing flat (zero-grade) samples.
    """

    config_version: str = TERRAIN_CONFIG_VERSION
    terrain_gate_enabled: bool = True
    downhill_threshold_pct: float = -3.0
    min_sustained_downhill_s: float = 5.0
    terrain_transition_buffer_s: float = 5.0
    grade_smoothing_window_s: float = 7.0
    derived_grade_window_m: float = 30.0
    derived_min_distance_span_m: float = 10.0
    min_grade_coverage_fraction: float = 0.80
    max_terrain_sample_gap_s: float = 5.0

    def __post_init__(self) -> None:
        if self.config_version != TERRAIN_CONFIG_VERSION:
            raise ValueError(f"unsupported config_version: {self.config_version!r}")
        object.__setattr__(self, "terrain_gate_enabled", bool(self.terrain_gate_enabled))
        for name in (
            "downhill_threshold_pct",
            "min_sustained_downhill_s",
            "terrain_transition_buffer_s",
            "grade_smoothing_window_s",
            "derived_grade_window_m",
            "derived_min_distance_span_m",
            "min_grade_coverage_fraction",
            "max_terrain_sample_gap_s",
        ):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
        if self.downhill_threshold_pct >= 0.0:
            raise ValueError("downhill_threshold_pct must be negative")
        for name in (
            "min_sustained_downhill_s",
            "grade_smoothing_window_s",
            "derived_grade_window_m",
            "derived_min_distance_span_m",
            "max_terrain_sample_gap_s",
        ):
            if getattr(self, name) <= 0.0:
                raise ValueError(f"{name} must be positive")
        if self.terrain_transition_buffer_s < 0.0:
            raise ValueError("terrain_transition_buffer_s must be non-negative")
        if not 0.0 < self.min_grade_coverage_fraction <= 1.0:
            raise ValueError("min_grade_coverage_fraction must be in (0, 1]")
        if self.derived_min_distance_span_m > self.derived_grade_window_m:
            raise ValueError(
                "derived_min_distance_span_m cannot exceed derived_grade_window_m"
            )

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True, slots=True)
class TerrainInterval:
    start_elapsed_s: float
    end_elapsed_s: float
    buffered_start_elapsed_s: float
    buffered_end_elapsed_s: float

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_elapsed_s - self.start_elapsed_s)

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True, slots=True)
class PreparedTerrain:
    timestamps: tuple[datetime, ...]
    elapsed_s: tuple[float, ...]
    smoothed_grade_pct: tuple[float | None, ...]
    downhill_mask: tuple[bool, ...]
    buffered_downhill_mask: tuple[bool, ...]
    downhill_intervals: tuple[TerrainInterval, ...]
    grade_source: str
    grade_coverage_fraction: float
    available: bool
    flags: tuple[str, ...]
    config: TerrainGateConfig
    terrain_input_hash: str

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True, slots=True)
class TerrainTimeseriesPoint:
    timestamp: datetime
    elapsed_s: float
    dt_s: float
    raw_hr_bpm: float | None
    hrmod_candidate_bpm: float | None
    hrmod_final_bpm: float | None
    smoothed_grade_pct: float | None
    downhill_mask: bool
    buffered_downhill_mask: bool
    terrain_status: str
    wave_id: int | None

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True, slots=True)
class TerrainWaveSummary:
    wave_id: int
    terrain_status: str
    terrain_rejection_reason: str | None
    downhill_overlap_s: float
    downhill_overlap_fraction: float
    min_smoothed_grade_pct: float | None
    moved_area_candidate_bpm_s: float
    moved_area_final_bpm_s: float

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True, slots=True)
class TerrainZoneSummary:
    """Unrounded zone durations before and after terrain post-processing.

    Zone names and bounds are copied from the completed HR-only result.  The
    terrain layer therefore reports how its final signal differs from that
    immutable candidate without reconstructing an athlete profile or changing
    any core-owned zone result.
    """

    zone_name: str
    lower_bpm: float
    upper_bpm: float
    raw_seconds: float
    raw_percent: float
    hrmod_candidate_seconds: float
    hrmod_candidate_percent: float
    hrmod_final_seconds: float
    hrmod_final_percent: float
    final_minus_candidate_seconds: float
    final_minus_raw_seconds: float

    @property
    def zone_label(self) -> str:
        return self.zone_name

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True, slots=True)
class TerrainGateDiagnostics:
    terrain_gate_enabled: bool
    terrain_gate_applied: bool
    grade_source: str
    grade_coverage_fraction: float
    candidate_wave_count: int
    accepted_wave_count: int
    terrain_rejected_wave_count: int
    terrain_rejected_fraction: float
    total_candidate_moved_area_bpm_s: float
    total_final_moved_area_bpm_s: float
    sustained_downhill_interval_count: int
    sustained_downhill_duration_s: float
    flags: tuple[str, ...] = ()
    status_counts: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "status_counts", MappingProxyType(dict(self.status_counts)))

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)


@dataclass(frozen=True, slots=True)
class TerrainGateResult:
    timeseries: tuple[TerrainTimeseriesPoint, ...]
    wave_summary: tuple[TerrainWaveSummary, ...]
    zone_summary: tuple[TerrainZoneSummary, ...]
    diagnostics: TerrainGateDiagnostics
    config: TerrainGateConfig
    hr_input_hash: str
    terrain_input_hash: str
    final_result_hash: str
    model_version: str
    terrain_model_version: str = TERRAIN_MODEL_VERSION

    @property
    def waves(self) -> tuple[TerrainWaveSummary, ...]:
        return self.wave_summary

    @property
    def zones(self) -> tuple[TerrainZoneSummary, ...]:
        return self.zone_summary

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)


def _nanmedian_filter(values: np.ndarray, window_points: int) -> np.ndarray:
    """Fast centred median while preserving genuinely missing grade rows."""

    if values.size == 0:
        return values.copy()
    width = max(1, int(window_points))
    if width % 2 == 0:
        width += 1
    finite = np.isfinite(values)
    if not finite.any():
        return np.full(values.shape, np.nan, dtype=float)
    # Linear interpolation is only an internal smoothing aid; the original
    # coverage mask is restored below, so missing terrain is never exported as
    # an invented value (and certainly never as grade=0).
    indices = np.arange(values.size, dtype=float)
    filled = np.interp(indices, indices[finite], values[finite])
    result = median_filter(filled, size=width, mode="nearest")
    result[~finite] = np.nan
    return np.asarray(result, dtype=float)


def _median_dt(elapsed_s: np.ndarray) -> float:
    if elapsed_s.size < 2:
        return 1.0
    deltas = np.diff(elapsed_s)
    valid = deltas[np.isfinite(deltas) & (deltas > 0.0)]
    return float(np.median(valid)) if valid.size else 1.0


def _derived_grade(
    altitude: np.ndarray,
    distance: np.ndarray,
    *,
    config: TerrainGateConfig,
) -> np.ndarray:
    """Derive grade in one pass over valid monotonic altitude/distance rows."""

    result = np.full(altitude.shape, np.nan, dtype=float)
    valid_indices = np.flatnonzero(np.isfinite(altitude) & np.isfinite(distance))
    if valid_indices.size < 2:
        return result
    valid_distance = distance[valid_indices]
    if np.any(np.diff(valid_distance) < 0.0):
        return result

    # Five-point running median removes an isolated altitude spike before the
    # distance-domain slope is taken.  Missing rows remain absent from input;
    # no grade=0 substitute is introduced.
    valid_altitude = _nanmedian_filter(altitude[valid_indices], 5)
    half_window = config.derived_grade_window_m / 2.0
    count = valid_indices.size
    left = 0
    right = 0
    for local_index in range(count):
        center_distance = valid_distance[local_index]
        lower_target = center_distance - half_window
        upper_target = center_distance + half_window
        while left + 1 < count and valid_distance[left + 1] <= lower_target:
            left += 1
        if left > local_index:
            left = local_index
        if right < local_index:
            right = local_index
        while right + 1 < count and valid_distance[right] < upper_target:
            right += 1
        distance_span = valid_distance[right] - valid_distance[left]
        if distance_span < config.derived_min_distance_span_m:
            continue
        altitude_change = valid_altitude[right] - valid_altitude[left]
        if math.isfinite(float(altitude_change)):
            result[valid_indices[local_index]] = 100.0 * altitude_change / distance_span
    return result


def _sustained_intervals(
    elapsed_s: np.ndarray,
    below_threshold: np.ndarray,
    *,
    minimum_duration_s: float,
    buffer_s: float,
    maximum_sample_gap_s: float,
) -> tuple[np.ndarray, np.ndarray, tuple[TerrainInterval, ...]]:
    sustained = np.zeros(below_threshold.shape, dtype=bool)
    buffered = np.zeros(below_threshold.shape, dtype=bool)
    intervals: list[TerrainInterval] = []
    index = 0
    while index < below_threshold.size:
        if not below_threshold[index]:
            index += 1
            continue
        start = index
        while (
            index + 1 < below_threshold.size
            and below_threshold[index + 1]
            and elapsed_s[index + 1] - elapsed_s[index] <= maximum_sample_gap_s
        ):
            index += 1
        end = index
        duration = float(elapsed_s[end] - elapsed_s[start])
        if duration >= minimum_duration_s:
            sustained[start : end + 1] = True
            buffered_start = float(elapsed_s[start] - buffer_s)
            buffered_end = float(elapsed_s[end] + buffer_s)
            buffered |= (elapsed_s >= buffered_start) & (elapsed_s <= buffered_end)
            intervals.append(
                TerrainInterval(
                    start_elapsed_s=float(elapsed_s[start]),
                    end_elapsed_s=float(elapsed_s[end]),
                    buffered_start_elapsed_s=buffered_start,
                    buffered_end_elapsed_s=buffered_end,
                )
            )
        index += 1
    return sustained, buffered, tuple(intervals)


def prepare_terrain(
    reference_channels: ReferenceChannels,
    config: TerrainGateConfig | None = None,
) -> PreparedTerrain:
    """Prepare smoothed grade and the sustained downhill mask exactly once."""

    if not isinstance(reference_channels, ReferenceChannels):
        raise TypeError("reference_channels must be ReferenceChannels")
    effective = config or TerrainGateConfig()
    samples = tuple(reference_channels.samples)
    timestamps = tuple(sample.timestamp for sample in samples)
    elapsed_s = np.asarray([sample.elapsed_s for sample in samples], dtype=float)
    if elapsed_s.size and (np.any(~np.isfinite(elapsed_s)) or np.any(np.diff(elapsed_s) < 0)):
        raise ValueError("reference sample elapsed_s must be finite and nondecreasing")

    ready_grade = np.asarray(
        [np.nan if sample.grade is None else float(sample.grade) for sample in samples],
        dtype=float,
    )
    altitude = np.asarray(
        [np.nan if sample.altitude_m is None else float(sample.altitude_m) for sample in samples],
        dtype=float,
    )
    distance = np.asarray(
        [np.nan if sample.distance_m is None else float(sample.distance_m) for sample in samples],
        dtype=float,
    )
    count = len(samples)
    ready_coverage = float(np.isfinite(ready_grade).sum() / count) if count else 0.0
    joint_coverage = (
        float((np.isfinite(altitude) & np.isfinite(distance)).sum() / count)
        if count
        else 0.0
    )
    flags: set[str] = set()
    grade_source = "unavailable"
    raw_grade = np.full((count,), np.nan, dtype=float)
    if ready_coverage >= effective.min_grade_coverage_fraction:
        raw_grade = ready_grade
        grade_source = "tcx_grade"
    elif joint_coverage >= effective.min_grade_coverage_fraction:
        derived = _derived_grade(altitude, distance, config=effective)
        derived_coverage = float(np.isfinite(derived).sum() / count) if count else 0.0
        if derived_coverage >= effective.min_grade_coverage_fraction:
            raw_grade = derived
            grade_source = "derived_altitude_distance"
        else:
            flags.add("TERRAIN_GRADE_UNRELIABLE")
    else:
        flags.add("TERRAIN_GRADE_UNAVAILABLE")

    if count:
        window_points = max(
            1,
            int(round(effective.grade_smoothing_window_s / _median_dt(elapsed_s))),
        )
        smoothed = _nanmedian_filter(raw_grade, window_points)
    else:
        smoothed = raw_grade
    grade_coverage = float(np.isfinite(smoothed).sum() / count) if count else 0.0
    available = (
        grade_source != "unavailable"
        and grade_coverage >= effective.min_grade_coverage_fraction
    )
    if not available:
        grade_source = "unavailable"
        smoothed[:] = np.nan
        flags.add("TERRAIN_GATE_UNAVAILABLE")

    below = np.isfinite(smoothed) & (smoothed <= effective.downhill_threshold_pct)
    if available:
        downhill, buffered, intervals = _sustained_intervals(
            elapsed_s,
            below,
            minimum_duration_s=effective.min_sustained_downhill_s,
            buffer_s=effective.terrain_transition_buffer_s,
            maximum_sample_gap_s=effective.max_terrain_sample_gap_s,
        )
    else:
        downhill = np.zeros((count,), dtype=bool)
        buffered = np.zeros((count,), dtype=bool)
        intervals = ()

    hash_payload = {
        "terrain_model_version": TERRAIN_MODEL_VERSION,
        "config": effective.to_dict(),
        "reference": [
            {
                "timestamp": sample.timestamp,
                "elapsed_s": sample.elapsed_s,
                "grade": sample.grade,
                "altitude_m": sample.altitude_m,
                "distance_m": sample.distance_m,
            }
            for sample in samples
        ],
        "grade_source": grade_source,
    }
    return PreparedTerrain(
        timestamps=timestamps,
        elapsed_s=tuple(float(value) for value in elapsed_s),
        smoothed_grade_pct=tuple(
            None if not math.isfinite(float(value)) else float(value)
            for value in smoothed
        ),
        downhill_mask=tuple(bool(value) for value in downhill),
        buffered_downhill_mask=tuple(bool(value) for value in buffered),
        downhill_intervals=intervals,
        grade_source=grade_source,
        grade_coverage_fraction=grade_coverage if available else 0.0,
        available=available,
        flags=tuple(sorted(flags)),
        config=effective,
        terrain_input_hash=_canonical_hash(hash_payload),
    )


def _aligned_prepared(
    hrmod_result: HRmodResult,
    prepared: PreparedTerrain,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    by_timestamp = {
        timestamp: index for index, timestamp in enumerate(prepared.timestamps)
    }
    grades = np.full((len(hrmod_result.timeseries),), np.nan, dtype=float)
    downhill = np.zeros((len(hrmod_result.timeseries),), dtype=bool)
    buffered = np.zeros((len(hrmod_result.timeseries),), dtype=bool)
    for index, point in enumerate(hrmod_result.timeseries):
        reference_index = by_timestamp.get(point.timestamp)
        if reference_index is None:
            continue
        grade = prepared.smoothed_grade_pct[reference_index]
        if grade is not None:
            grades[index] = grade
        downhill[index] = prepared.downhill_mask[reference_index]
        buffered[index] = prepared.buffered_downhill_mask[reference_index]
    return grades, downhill, buffered


def _prefix_value_at(
    elapsed_s: np.ndarray,
    interval_weights: np.ndarray,
    prefix: np.ndarray,
    query_s: float,
) -> float:
    """Return integrated mask duration before ``query_s`` in O(log samples)."""

    if elapsed_s.size < 2 or query_s <= elapsed_s[0]:
        return 0.0
    if query_s >= elapsed_s[-1]:
        return float(prefix[-1])
    index = int(np.searchsorted(elapsed_s, query_s, side="right") - 1)
    return float(prefix[index] + interval_weights[index] * (query_s - elapsed_s[index]))


def _prefix_overlap(
    elapsed_s: np.ndarray,
    interval_weights: np.ndarray,
    prefix: np.ndarray,
    query_start: float,
    query_end: float,
) -> float:
    if query_end <= query_start:
        return 0.0
    return max(
        0.0,
        _prefix_value_at(elapsed_s, interval_weights, prefix, query_end)
        - _prefix_value_at(elapsed_s, interval_weights, prefix, query_start),
    )


def _classify_terrain_zone(value: float | None, zones: Sequence[Any]) -> str | None:
    """Classify with the exact lower/final-upper inclusivity used by the core."""

    if value is None or not math.isfinite(float(value)):
        return None
    number = float(value)
    for index, zone in enumerate(zones):
        is_final = index == len(zones) - 1
        if number >= zone.lower_bpm and (
            number < zone.upper_bpm or (is_final and number <= zone.upper_bpm)
        ):
            return str(zone.zone_name)
    return None


def _terrain_zone_summaries(
    hrmod_result: HRmodResult,
    terrain_points: Sequence[TerrainTimeseriesPoint],
) -> tuple[TerrainZoneSummary, ...]:
    """Aggregate raw, immutable candidate, and terrain-final zone durations."""

    zones = tuple(hrmod_result.zone_summary)
    dt_s = np.asarray([point.dt_s for point in terrain_points], dtype=float)
    raw_values = tuple(point.raw_hr_bpm for point in terrain_points)
    candidate_values = tuple(point.hrmod_candidate_bpm for point in terrain_points)
    final_values = tuple(point.hrmod_final_bpm for point in terrain_points)
    raw_labels = tuple(_classify_terrain_zone(value, zones) for value in raw_values)
    candidate_labels = tuple(
        _classify_terrain_zone(value, zones) for value in candidate_values
    )
    final_labels = tuple(_classify_terrain_zone(value, zones) for value in final_values)

    raw_total = float(
        sum(
            dt_s[index]
            for index, value in enumerate(raw_values)
            if value is not None and math.isfinite(float(value))
        )
    )
    candidate_total = float(
        sum(
            dt_s[index]
            for index, value in enumerate(candidate_values)
            if value is not None and math.isfinite(float(value))
        )
    )
    final_total = float(
        sum(
            dt_s[index]
            for index, value in enumerate(final_values)
            if value is not None and math.isfinite(float(value))
        )
    )

    summaries: list[TerrainZoneSummary] = []
    for zone in zones:
        raw_seconds = float(
            sum(
                dt_s[index]
                for index, label in enumerate(raw_labels)
                if label == zone.zone_name
            )
        )
        candidate_seconds = float(
            sum(
                dt_s[index]
                for index, label in enumerate(candidate_labels)
                if label == zone.zone_name
            )
        )
        final_seconds = float(
            sum(
                dt_s[index]
                for index, label in enumerate(final_labels)
                if label == zone.zone_name
            )
        )
        summaries.append(
            TerrainZoneSummary(
                zone_name=str(zone.zone_name),
                lower_bpm=float(zone.lower_bpm),
                upper_bpm=float(zone.upper_bpm),
                raw_seconds=raw_seconds,
                raw_percent=(100.0 * raw_seconds / raw_total if raw_total > 0.0 else 0.0),
                hrmod_candidate_seconds=candidate_seconds,
                hrmod_candidate_percent=(
                    100.0 * candidate_seconds / candidate_total
                    if candidate_total > 0.0
                    else 0.0
                ),
                hrmod_final_seconds=final_seconds,
                hrmod_final_percent=(
                    100.0 * final_seconds / final_total if final_total > 0.0 else 0.0
                ),
                final_minus_candidate_seconds=final_seconds - candidate_seconds,
                final_minus_raw_seconds=final_seconds - raw_seconds,
            )
        )
    return tuple(summaries)


def apply_terrain_gate(
    hrmod_result: HRmodResult,
    reference_channels: ReferenceChannels | None = None,
    config: TerrainGateConfig | None = None,
    *,
    prepared_terrain: PreparedTerrain | None = None,
) -> TerrainGateResult:
    """Apply terrain rejection to a completed, immutable HR-only candidate."""

    if not isinstance(hrmod_result, HRmodResult):
        raise TypeError("hrmod_result must be a completed HRmodResult")
    if prepared_terrain is None:
        if reference_channels is None:
            raise TypeError("reference_channels or prepared_terrain is required")
        prepared = prepare_terrain(reference_channels, config=config)
    else:
        prepared = prepared_terrain
        if config is not None and config != prepared.config:
            raise ValueError("config does not match prepared_terrain.config")
    effective = prepared.config
    points = tuple(hrmod_result.timeseries)
    elapsed = np.asarray([point.elapsed_s for point in points], dtype=float)
    grades, downhill, buffered = _aligned_prepared(hrmod_result, prepared)

    # A grade sample at t_i represents the following half-open interval
    # [t_i, t_{i+1}); the final sample has zero duration.  One prefix pass then
    # answers each wave-overlap query without rescanning the sample series.
    interval_durations = np.diff(elapsed) if elapsed.size >= 2 else np.asarray([], dtype=float)
    # A downhill duration exists only between two adjacent sustained samples.
    # This matches TerrainInterval.duration_s (last timestamp - first timestamp)
    # and never integrates across a configured terrain-data gap.
    interval_downhill = (
        (
            downhill[:-1]
            & downhill[1:]
            & (interval_durations <= effective.max_terrain_sample_gap_s)
        ).astype(float)
        if downhill.size >= 2
        else np.asarray([], dtype=float)
    )
    downhill_prefix = np.concatenate(
        ([0.0], np.cumsum(interval_downhill * interval_durations))
    )
    final_values = [point.hrmod_bpm for point in points]
    terrain_status_by_sample = [
        "terrain_gate_disabled"
        if not effective.terrain_gate_enabled
        else "terrain_gate_unavailable"
        if not prepared.available
        else "accepted"
        for _ in points
    ]
    terrain_waves: list[TerrainWaveSummary] = []
    rejected_wave_ids: set[int] = set()
    rejected_ranges: list[tuple[int, int]] = []
    status_counts: dict[str, int] = {}

    interval_starts = np.asarray(
        [item.buffered_start_elapsed_s for item in prepared.downhill_intervals],
        dtype=float,
    )
    interval_ends = np.asarray(
        [item.buffered_end_elapsed_s for item in prepared.downhill_intervals],
        dtype=float,
    )
    sorted_waves = sorted(
        hrmod_result.wave_summary,
        key=lambda item: (item.rise_start_elapsed_s, item.tail_end_elapsed_s, item.wave_id),
    )
    grade_minimum = np.full((len(sorted_waves),), np.nan, dtype=float)
    # Detected waves are non-overlapping.  Two monotonic pointers therefore
    # collect per-wave minimum grade in O(samples + waves), with no mask alloc.
    point_index = 0
    for wave_index, wave in enumerate(sorted_waves):
        while point_index < elapsed.size and elapsed[point_index] < wave.rise_start_elapsed_s:
            point_index += 1
        scan_index = point_index
        minimum = math.inf
        while scan_index < elapsed.size and elapsed[scan_index] <= wave.tail_end_elapsed_s:
            if math.isfinite(float(grades[scan_index])):
                minimum = min(minimum, float(grades[scan_index]))
            scan_index += 1
        grade_minimum[wave_index] = minimum if math.isfinite(minimum) else np.nan
        point_index = scan_index

    for wave_index, wave in enumerate(sorted_waves):
        wave_start = float(wave.rise_start_elapsed_s)
        wave_end = float(wave.tail_end_elapsed_s)
        duration = max(0.0, wave_end - wave_start)
        overlap_s = _prefix_overlap(
            elapsed,
            interval_downhill,
            downhill_prefix,
            wave_start,
            wave_end,
        )
        overlap_fraction = overlap_s / duration if duration > 0.0 else 0.0
        minimum_grade = (
            float(grade_minimum[wave_index])
            if math.isfinite(float(grade_minimum[wave_index]))
            else None
        )
        status = "accepted"
        reason: str | None = None
        if not effective.terrain_gate_enabled:
            status = "terrain_gate_disabled"
        elif not prepared.available:
            status = "terrain_gate_unavailable"
        elif interval_starts.size:
            candidate = int(np.searchsorted(interval_ends, wave_start, side="left"))
            if candidate < interval_starts.size and interval_starts[candidate] <= wave_end:
                status = "terrain_confounded"
                reason = (
                    "sustained_downhill_overlap"
                    if overlap_s > 0.0
                    else "terrain_transition_buffer"
                )

        candidate_area = float(wave.moved_area_bpm_s)
        final_area = candidate_area
        if status == "terrain_confounded":
            final_area = 0.0
            rejected_wave_ids.add(wave.wave_id)
            start_index = int(np.searchsorted(elapsed, wave_start, side="left"))
            end_index = int(np.searchsorted(elapsed, wave_end, side="right"))
            rejected_ranges.append((start_index, end_index))
        status_counts[status] = status_counts.get(status, 0) + 1
        terrain_waves.append(
            TerrainWaveSummary(
                wave_id=wave.wave_id,
                terrain_status=status,
                terrain_rejection_reason=reason,
                downhill_overlap_s=overlap_s,
                downhill_overlap_fraction=overlap_fraction,
                min_smoothed_grade_pct=minimum_grade,
                moved_area_candidate_bpm_s=candidate_area,
                moved_area_final_bpm_s=final_area,
            )
        )

    # One range-difference pass applies every rejected wave to final HR/status.
    rejection_delta = np.zeros((len(points) + 1,), dtype=int)
    for start_index, end_index in rejected_ranges:
        rejection_delta[start_index] += 1
        rejection_delta[end_index] -= 1
    rejected_samples = np.cumsum(rejection_delta[:-1]) > 0
    for sample_index, rejected in enumerate(rejected_samples):
        if rejected:
            final_values[sample_index] = points[sample_index].raw_hr_bpm
            terrain_status_by_sample[sample_index] = "terrain_confounded"

    terrain_points = tuple(
        TerrainTimeseriesPoint(
            timestamp=point.timestamp,
            elapsed_s=point.elapsed_s,
            dt_s=point.dt_s,
            raw_hr_bpm=point.raw_hr_bpm,
            hrmod_candidate_bpm=point.hrmod_bpm,
            hrmod_final_bpm=final_values[index],
            smoothed_grade_pct=(
                float(grades[index]) if math.isfinite(float(grades[index])) else None
            ),
            downhill_mask=bool(downhill[index]),
            buffered_downhill_mask=bool(buffered[index]),
            terrain_status=terrain_status_by_sample[index],
            wave_id=point.wave_id,
        )
        for index, point in enumerate(points)
    )
    zone_summary = _terrain_zone_summaries(hrmod_result, terrain_points)
    total_candidate = float(
        sum(item.moved_area_candidate_bpm_s for item in terrain_waves)
    )
    total_final = float(sum(item.moved_area_final_bpm_s for item in terrain_waves))
    rejected_count = len(rejected_wave_ids)
    candidate_count = len(terrain_waves)
    diagnostics_flags = set(prepared.flags)
    if not effective.terrain_gate_enabled:
        diagnostics_flags.add("TERRAIN_GATE_DISABLED")
    elif not prepared.available:
        diagnostics_flags.add("TERRAIN_GATE_UNAVAILABLE")
    diagnostics = TerrainGateDiagnostics(
        terrain_gate_enabled=effective.terrain_gate_enabled,
        terrain_gate_applied=effective.terrain_gate_enabled and prepared.available,
        grade_source=prepared.grade_source,
        grade_coverage_fraction=prepared.grade_coverage_fraction,
        candidate_wave_count=candidate_count,
        accepted_wave_count=status_counts.get("accepted", 0),
        terrain_rejected_wave_count=rejected_count,
        terrain_rejected_fraction=(
            rejected_count / candidate_count if candidate_count else 0.0
        ),
        total_candidate_moved_area_bpm_s=total_candidate,
        total_final_moved_area_bpm_s=total_final,
        sustained_downhill_interval_count=len(prepared.downhill_intervals),
        sustained_downhill_duration_s=float(
            sum(item.duration_s for item in prepared.downhill_intervals)
        ),
        flags=tuple(sorted(diagnostics_flags)),
        status_counts=status_counts,
    )
    final_hash = _canonical_hash(
        {
            "hr_input_hash": hrmod_result.hr_input_hash,
            "core_model_version": hrmod_result.model_version,
            "core_config": hrmod_result.config.to_dict(),
            "terrain_input_hash": prepared.terrain_input_hash,
            "terrain_model_version": TERRAIN_MODEL_VERSION,
            "final_hr": [point.hrmod_final_bpm for point in terrain_points],
            "waves": [wave.to_dict() for wave in terrain_waves],
            "zones": [zone.to_dict() for zone in zone_summary],
        }
    )
    result = TerrainGateResult(
        timeseries=terrain_points,
        wave_summary=tuple(terrain_waves),
        zone_summary=zone_summary,
        diagnostics=diagnostics,
        config=effective,
        hr_input_hash=hrmod_result.hr_input_hash,
        terrain_input_hash=prepared.terrain_input_hash,
        final_result_hash=final_hash,
        model_version=hrmod_result.model_version,
    )
    return result


__all__ = [
    "PreparedTerrain",
    "TERRAIN_CONFIG_VERSION",
    "TERRAIN_MODEL_VERSION",
    "TerrainGateConfig",
    "TerrainGateDiagnostics",
    "TerrainGateResult",
    "TerrainInterval",
    "TerrainTimeseriesPoint",
    "TerrainWaveSummary",
    "TerrainZoneSummary",
    "apply_terrain_gate",
    "prepare_terrain",
]
