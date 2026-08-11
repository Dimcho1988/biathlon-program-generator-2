"""Transparent HR-only cleaning and segmentation.

Cleaning is intentionally conservative.  The measured values remain available
as ``raw_hr``; interpolation and artifact decisions are represented explicitly
by per-sample flags.  No smoothing is performed here because the separately
labelled smoothed series is only an inverse-kinetics diagnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Sequence

import numpy as np

from .schemas import HRSample, HRmodConfig


@dataclass(frozen=True, slots=True)
class CleanedHRSignal:
    samples: tuple[HRSample, ...]
    timestamps: tuple[datetime, ...]
    elapsed_s: np.ndarray
    dt_s: np.ndarray
    raw_hr: np.ndarray
    clean_hr: np.ndarray
    segment_ids: np.ndarray
    quality_flags: tuple[tuple[str, ...], ...]
    artifact_mask: np.ndarray
    interpolated_mask: np.ndarray
    long_gap_mask: np.ndarray
    gap_affected_mask: np.ndarray
    duplicate_timestamp_count: int
    long_gap_count: int


def _normalise_samples(hr_samples: Sequence[HRSample]) -> tuple[tuple[HRSample, ...], int]:
    if not isinstance(hr_samples, Sequence) or isinstance(hr_samples, (str, bytes)):
        raise TypeError("hr_samples must be a sequence of HRSample values")
    if not hr_samples:
        raise ValueError("at least one HR sample is required")
    for sample in hr_samples:
        if not isinstance(sample, HRSample):
            raise TypeError(
                "compute_hrmod_hr_only accepts only HRSample values; generic rows "
                "and reference-bearing records are intentionally rejected"
            )

    ordered = sorted(enumerate(hr_samples), key=lambda item: (item[1].timestamp, item[0]))
    normalised: list[HRSample] = []
    duplicate_count = 0
    cursor = 0
    while cursor < len(ordered):
        timestamp = ordered[cursor][1].timestamp
        group: list[HRSample] = []
        while cursor < len(ordered) and ordered[cursor][1].timestamp == timestamp:
            group.append(ordered[cursor][1])
            cursor += 1
        if len(group) == 1:
            normalised.append(group[0])
            continue

        duplicate_count += len(group) - 1
        finite_values = sorted(
            float(sample.heart_rate_bpm)
            for sample in group
            if sample.heart_rate_bpm is not None and isfinite(float(sample.heart_rate_bpm))
        )
        # A median makes duplicate handling independent of XML/order artefacts.
        value = float(np.median(finite_values)) if finite_values else None
        flags = {flag for sample in group for flag in sample.quality_flags}
        flags.add("DUPLICATE_TIMESTAMP")
        normalised.append(HRSample(timestamp, value, tuple(sorted(flags))))
    return tuple(normalised), duplicate_count


def _input_flag_marks_artifact(flags: tuple[str, ...]) -> bool:
    artifact_tokens = (
        "ARTIFACT",
        "INVALID_HR",
        "SENSOR_DROPOUT",
        "SENSOR_ERROR",
    )
    return any(any(token in flag.upper() for token in artifact_tokens) for flag in flags)


def clean_hr_signal(
    hr_samples: Sequence[HRSample], config: HRmodConfig
) -> CleanedHRSignal:
    """Validate, clean, interpolate short invalid runs, and split HR segments."""

    samples, duplicate_count = _normalise_samples(hr_samples)
    count = len(samples)
    timestamps = tuple(sample.timestamp for sample in samples)
    epoch_s = np.asarray(
        [sample.timestamp.astimezone(timezone.utc).timestamp() for sample in samples],
        dtype=float,
    )
    if count > 1 and np.any(np.diff(epoch_s) <= 0.0):
        raise ValueError("timestamps must be strictly increasing after deduplication")
    elapsed_s = epoch_s - epoch_s[0]

    raw_hr = np.full(count, np.nan, dtype=float)
    quality: list[set[str]] = [set(sample.quality_flags) for sample in samples]
    artifact = np.zeros(count, dtype=bool)
    for index, sample in enumerate(samples):
        value = sample.heart_rate_bpm
        if value is None or not isfinite(float(value)):
            quality[index].add("MISSING_HR")
            continue
        raw_hr[index] = float(value)
        if _input_flag_marks_artifact(sample.quality_flags):
            artifact[index] = True
            quality[index].add("HR_ARTIFACT")
        elif not config.artifact_min_hr_bpm <= raw_hr[index] <= config.artifact_max_hr_bpm:
            artifact[index] = True
            quality[index].update(("HR_ARTIFACT", "HR_OUT_OF_RANGE"))

    # Only isolated reversal spikes are auto-marked.  Sustained fast changes are
    # retained because they may be genuine and the cleaning policy is conservative.
    plausible = np.isfinite(raw_hr) & ~artifact
    for index in range(1, count - 1):
        if not (plausible[index - 1] and plausible[index] and plausible[index + 1]):
            continue
        dt_left = epoch_s[index] - epoch_s[index - 1]
        dt_right = epoch_s[index + 1] - epoch_s[index]
        if (
            dt_left <= 0.0
            or dt_right <= 0.0
            or dt_left > config.long_gap_threshold_s
            or dt_right > config.long_gap_threshold_s
        ):
            continue
        span = dt_left + dt_right
        expected = raw_hr[index - 1] + (raw_hr[index + 1] - raw_hr[index - 1]) * (
            dt_left / span
        )
        left_rate = (raw_hr[index] - raw_hr[index - 1]) / dt_left
        right_rate = (raw_hr[index + 1] - raw_hr[index]) / dt_right
        reversal = left_rate * right_rate < 0.0
        if (
            reversal
            and abs(raw_hr[index] - expected) >= config.artifact_spike_deviation_bpm
            and abs(left_rate) >= config.artifact_max_rate_bpm_per_s
            and abs(right_rate) >= config.artifact_max_rate_bpm_per_s
        ):
            artifact[index] = True
            plausible[index] = False
            quality[index].update(("HR_ARTIFACT", "HR_SPIKE_ARTIFACT"))

    clean_hr = raw_hr.copy()
    clean_hr[artifact] = np.nan
    invalid = ~np.isfinite(clean_hr)
    interpolated = np.zeros(count, dtype=bool)

    cursor = 0
    while cursor < count:
        if not invalid[cursor]:
            cursor += 1
            continue
        start = cursor
        while cursor < count and invalid[cursor]:
            cursor += 1
        end = cursor - 1
        left = start - 1
        right = end + 1
        if left < 0 or right >= count or invalid[left] or invalid[right]:
            continue
        enclosing_span_s = epoch_s[right] - epoch_s[left]
        adjacent_dts = np.diff(epoch_s[left : right + 1])
        if (
            enclosing_span_s > config.max_interpolation_gap_s
            or np.any(adjacent_dts > config.long_gap_threshold_s)
        ):
            continue
        clean_hr[start : end + 1] = np.interp(
            epoch_s[start : end + 1],
            np.asarray([epoch_s[left], epoch_s[right]]),
            np.asarray([clean_hr[left], clean_hr[right]]),
        )
        interpolated[start : end + 1] = True
        for index in range(start, end + 1):
            quality[index].add("INTERPOLATED_HR")

    segment_ids = np.full(count, -1, dtype=int)
    dt_s = np.zeros(count, dtype=float)
    long_gap_mask = np.zeros(count, dtype=bool)
    gap_affected = np.zeros(count, dtype=bool)
    # Public segment identifiers are one-based; ``-1`` remains the internal
    # sentinel for rows without usable HR.
    segment_id = 0
    previous_clean_index: int | None = None
    long_gap_count = 0

    for index in range(count):
        if not np.isfinite(clean_hr[index]):
            gap_affected[index] = True
            quality[index].add("LONG_GAP")
            previous_clean_index = None
            continue

        starts_segment = previous_clean_index is None
        timestamp_gap = False
        if previous_clean_index is not None:
            delta = epoch_s[index] - epoch_s[previous_clean_index]
            timestamp_gap = delta > config.long_gap_threshold_s
            starts_segment = timestamp_gap or index != previous_clean_index + 1
        if starts_segment:
            segment_id += 1
            dt_s[index] = 0.0
            if index > 0:
                long_gap_count += 1
                long_gap_mask[index] = True
                gap_affected[index] = True
                quality[index].add("LONG_GAP")
                prior = index - 1
                long_gap_mask[prior] = True
                gap_affected[prior] = True
                quality[prior].add("LONG_GAP")
        else:
            dt_s[index] = epoch_s[index] - epoch_s[previous_clean_index]
        segment_ids[index] = segment_id
        previous_clean_index = index

    # A large timestamp jump is a long gap even when both endpoint HR values exist.
    for index in range(1, count):
        if epoch_s[index] - epoch_s[index - 1] > config.long_gap_threshold_s:
            long_gap_mask[index - 1 : index + 1] = True
            gap_affected[index - 1 : index + 1] = True
            quality[index - 1].add("LONG_GAP")
            quality[index].add("LONG_GAP")

    return CleanedHRSignal(
        samples=samples,
        timestamps=timestamps,
        elapsed_s=elapsed_s,
        dt_s=dt_s,
        raw_hr=raw_hr,
        clean_hr=clean_hr,
        segment_ids=segment_ids,
        quality_flags=tuple(tuple(sorted(flags)) for flags in quality),
        artifact_mask=artifact,
        interpolated_mask=interpolated,
        long_gap_mask=long_gap_mask,
        gap_affected_mask=gap_affected,
        duplicate_timestamp_count=duplicate_count,
        long_gap_count=long_gap_count,
    )


__all__ = ["CleanedHRSignal", "clean_hr_signal"]
