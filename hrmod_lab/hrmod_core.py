"""Public orchestration for the physically isolated HR-only HRmod v4 core."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from datetime import timezone
from math import isfinite
from typing import Sequence

import numpy as np

from .schemas import (
    MODEL_VERSION,
    AthleteHRProfile,
    HRmodConfig,
    HRmodDiagnostics,
    HRmodResult,
    HRmodTimeseriesPoint,
    HRSample,
    WaveSummary,
    ZoneSummary,
)
from .signal_cleaning import CleanedHRSignal, clean_hr_signal
from .mirror_area_shift import WaveAreaShiftResult, shift_mirror_wave_areas
from .wave_detection_v4 import WaveDetectionResult, detect_hr_waves


class HRmodInputUnsuitableError(ValueError):
    """Typed, formula-neutral rejection of an unsuitable activity HR input."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _optional_float(value: float) -> float | None:
    return float(value) if isfinite(float(value)) else None


def _hash_hr_input(samples: tuple[HRSample, ...]) -> str:
    """Hash only timestamps, measured HR, and HR quality flags."""

    records = []
    for sample in samples:
        value = sample.heart_rate_bpm
        records.append(
            {
                "timestamp_utc": sample.timestamp.astimezone(timezone.utc).isoformat(
                    timespec="microseconds"
                ),
                "heart_rate_bpm": (
                    float(value)
                    if value is not None and isfinite(float(value))
                    else None
                ),
                "quality_flags": list(sample.quality_flags),
            }
        )
    payload = json.dumps(
        {"hr_input_schema": "hr_only_samples_v1", "samples": records},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _classify_zone(value: float, profile: AthleteHRProfile) -> str | None:
    if not np.isfinite(value):
        return None
    for index, zone in enumerate(profile.zones):
        is_final = index == len(profile.zones) - 1
        if value >= zone.lower_bpm and (
            value < zone.upper_bpm or (is_final and value <= zone.upper_bpm)
        ):
            return zone.name
    return None


def _zone_summaries(
    *,
    raw_hr: np.ndarray,
    clean_hr: np.ndarray,
    hrmod: np.ndarray,
    dt_s: np.ndarray,
    profile: AthleteHRProfile,
) -> tuple[
    tuple[ZoneSummary, ...],
    tuple[str | None, ...],
    tuple[str | None, ...],
    tuple[str | None, ...],
]:
    raw_labels = tuple(_classify_zone(value, profile) for value in raw_hr)
    clean_labels = tuple(_classify_zone(value, profile) for value in clean_hr)
    hrmod_labels = tuple(_classify_zone(value, profile) for value in hrmod)
    raw_total = float(np.sum(dt_s[np.isfinite(raw_hr)]))
    clean_total = float(np.sum(dt_s[np.isfinite(clean_hr)]))
    hrmod_total = float(np.sum(dt_s[np.isfinite(hrmod)]))
    summaries: list[ZoneSummary] = []
    for zone in profile.zones:
        raw_seconds = float(
            sum(dt_s[index] for index, label in enumerate(raw_labels) if label == zone.name)
        )
        clean_seconds = float(
            sum(
                dt_s[index]
                for index, label in enumerate(clean_labels)
                if label == zone.name
            )
        )
        hrmod_seconds = float(
            sum(
                dt_s[index]
                for index, label in enumerate(hrmod_labels)
                if label == zone.name
            )
        )
        summaries.append(
            ZoneSummary(
                zone_name=zone.name,
                lower_bpm=zone.lower_bpm,
                upper_bpm=zone.upper_bpm,
                raw_seconds=raw_seconds,
                raw_percent=(100.0 * raw_seconds / raw_total if raw_total > 0.0 else 0.0),
                clean_seconds=clean_seconds,
                clean_percent=(
                    100.0 * clean_seconds / clean_total if clean_total > 0.0 else 0.0
                ),
                hrmod_seconds=hrmod_seconds,
                hrmod_percent=(
                    100.0 * hrmod_seconds / hrmod_total if hrmod_total > 0.0 else 0.0
                ),
                hrmod_minus_clean_seconds=hrmod_seconds - clean_seconds,
            )
        )
    return tuple(summaries), raw_labels, clean_labels, hrmod_labels


def _zone_seconds_for_window(
    *,
    labels: tuple[str | None, ...],
    dt_s: np.ndarray,
    start: int,
    end: int,
    profile: AthleteHRProfile,
) -> dict[str, float]:
    return {
        zone.name: float(
            sum(
                dt_s[index]
                for index in range(start, end + 1)
                if labels[index] == zone.name
            )
        )
        for zone in profile.zones
    }


def _wave_summaries(
    *,
    cleaned: CleanedHRSignal,
    detection: WaveDetectionResult,
    redistribution: WaveAreaShiftResult,
    raw_zones: tuple[str | None, ...],
    clean_zones: tuple[str | None, ...],
    hrmod_zones: tuple[str | None, ...],
    profile: AthleteHRProfile,
) -> tuple[WaveSummary, ...]:
    summaries: list[WaveSummary] = []
    for result in redistribution.wave_results:
        wave = result.wave
        start = wave.rise_start_index
        peak = wave.peak_index
        end = wave.tail_end_index
        raw_seconds = _zone_seconds_for_window(
            labels=raw_zones,
            dt_s=cleaned.dt_s,
            start=start,
            end=end,
            profile=profile,
        )
        clean_seconds = _zone_seconds_for_window(
            labels=clean_zones,
            dt_s=cleaned.dt_s,
            start=start,
            end=end,
            profile=profile,
        )
        hrmod_seconds = _zone_seconds_for_window(
            labels=hrmod_zones,
            dt_s=cleaned.dt_s,
            start=start,
            end=end,
            profile=profile,
        )
        status = "corrected" if result.corrected else (
            "skipped" if wave.complete else "incomplete"
        )
        summaries.append(
            WaveSummary(
                wave_id=wave.wave_id,
                segment_id=wave.segment_id,
                status=status,
                complete=wave.complete,
                corrected=result.corrected,
                rise_start_timestamp=cleaned.timestamps[start],
                peak_timestamp=cleaned.timestamps[peak],
                tail_end_timestamp=cleaned.timestamps[end],
                rise_start_elapsed_s=float(cleaned.elapsed_s[start]),
                peak_elapsed_s=float(cleaned.elapsed_s[peak]),
                tail_end_elapsed_s=float(cleaned.elapsed_s[end]),
                end_reason=wave.end_reason,
                baseline_hr_bpm=wave.baseline_hr_bpm,
                donor_floor_bpm=result.donor_floor_bpm,
                rise_bpm=max(
                    0.0,
                    float(detection.h_detect[peak] - detection.h_detect[start]),
                ),
                fall_bpm=max(
                    0.0,
                    float(detection.h_detect[peak] - detection.h_detect[end]),
                ),
                receiver_duration_s=result.receiver_duration_s,
                donor_duration_s=result.donor_duration_s,
                donor_available_area_bpm_s=result.donor_available_area_bpm_s,
                requested_area_bpm_s=result.requested_area_bpm_s,
                receiver_capacity_bpm_s=result.receiver_capacity_bpm_s,
                moved_area_bpm_s=result.moved_area_bpm_s,
                moved_fraction_of_donor=(
                    result.moved_area_bpm_s / result.donor_available_area_bpm_s
                    if result.donor_available_area_bpm_s > 0.0
                    else 0.0
                ),
                added_area_bpm_s=result.added_area_bpm_s,
                removed_area_bpm_s=result.removed_area_bpm_s,
                area_balance_error_bpm_s=result.area_balance_error_bpm_s,
                capacity_limited_area_bpm_s=result.capacity_limited_area_bpm_s,
                capacity_limited=result.capacity_limited,
                skip_reason=result.skip_reason,
                morphology=wave.morphology,
                morphology_reason=wave.morphology_reason,
                correction_strategy=wave.correction_strategy,
                raw_zone_seconds=raw_seconds,
                clean_zone_seconds=clean_seconds,
                hrmod_zone_seconds=hrmod_seconds,
                hrmod_minus_raw_zone_seconds={
                    name: hrmod_seconds[name] - raw_seconds[name]
                    for name in hrmod_seconds
                },
                hrmod_minus_clean_zone_seconds={
                    name: hrmod_seconds[name] - clean_seconds[name]
                    for name in hrmod_seconds
                },
                flags=result.flags,
            )
        )
    return tuple(summaries)


def _diagnostics(
    *,
    cleaned: CleanedHRSignal,
    detection: WaveDetectionResult,
    redistribution: WaveAreaShiftResult,
    config: HRmodConfig,
) -> HRmodDiagnostics:
    count = len(cleaned.samples)
    raw_valid = int(np.count_nonzero(np.isfinite(cleaned.raw_hr)))
    clean_valid = int(np.count_nonzero(np.isfinite(cleaned.clean_hr)))
    artifact_count = int(np.count_nonzero(cleaned.artifact_mask))
    interpolated_count = int(np.count_nonzero(cleaned.interpolated_mask))
    supported_count = int(np.count_nonzero(detection.trend_supported_mask))
    intervals = cleaned.dt_s[np.isfinite(cleaned.dt_s) & (cleaned.dt_s > 0.0)]
    if len(intervals):
        mean_dt = float(np.mean(intervals))
        median_dt = float(np.median(intervals))
        dt_cv = float(np.std(intervals) / mean_dt) if mean_dt > 0.0 else None
        regular_fraction = float(
            np.mean(
                np.abs(intervals - median_dt)
                <= config.sampling_regularity_tolerance_s
            )
        )
    else:
        mean_dt = median_dt = dt_cv = regular_fraction = None

    wave_results = redistribution.wave_results
    complete_count = sum(result.wave.complete for result in wave_results)
    incomplete_count = len(wave_results) - complete_count
    corrected_count = sum(result.corrected for result in wave_results)
    donor_available = float(
        sum(result.donor_available_area_bpm_s for result in wave_results)
    )
    requested = float(sum(result.requested_area_bpm_s for result in wave_results))
    receiver_capacity = float(
        sum(result.receiver_capacity_bpm_s for result in wave_results)
    )
    moved = float(sum(result.moved_area_bpm_s for result in wave_results))
    added = float(sum(result.added_area_bpm_s for result in wave_results))
    removed = float(sum(result.removed_area_bpm_s for result in wave_results))
    errors = [result.area_balance_error_bpm_s for result in wave_results]
    total_error = float(sum(errors))
    max_error = float(max((abs(value) for value in errors), default=0.0))
    capacity_limited_area = float(
        sum(result.capacity_limited_area_bpm_s for result in wave_results)
    )
    skip_reasons = Counter(
        result.skip_reason for result in wave_results if result.skip_reason is not None
    )

    flags: set[str] = set()
    if artifact_count:
        flags.add("HR_ARTIFACTS_PRESENT")
    if interpolated_count:
        flags.add("INTERPOLATED_HR")
    if cleaned.long_gap_count or np.any(cleaned.long_gap_mask):
        flags.add("LONG_GAP")
    if supported_count < clean_valid:
        flags.add("INSUFFICIENT_DETECTION_SUPPORT")
    for result in wave_results:
        flags.update(result.flags)
    area_passed = max_error <= config.area_conservation_tolerance_bpm_s

    return HRmodDiagnostics(
        flags=tuple(sorted(flags)),
        total_samples=count,
        valid_raw_samples=raw_valid,
        clean_samples=clean_valid,
        hr_coverage_fraction=(raw_valid / count if count else 0.0),
        sampling_interval_count=len(intervals),
        mean_dt_s=mean_dt,
        median_dt_s=median_dt,
        dt_cv=dt_cv,
        regular_sampling_fraction=regular_fraction,
        interpolated_samples=interpolated_count,
        interpolated_fraction=(interpolated_count / count if count else 0.0),
        artifact_samples=artifact_count,
        artifact_fraction=(artifact_count / count if count else 0.0),
        detection_supported_samples=supported_count,
        detection_support_fraction=(
            supported_count / clean_valid if clean_valid else 0.0
        ),
        segment_count=len({int(value) for value in cleaned.segment_ids if value >= 0}),
        long_gap_count=cleaned.long_gap_count,
        edge_affected_samples=int(np.count_nonzero(detection.edge_affected_mask)),
        gap_affected_samples=int(np.count_nonzero(cleaned.gap_affected_mask)),
        detected_wave_count=len(wave_results),
        complete_wave_count=int(complete_count),
        incomplete_wave_count=int(incomplete_count),
        corrected_wave_count=int(corrected_count),
        skipped_wave_count=len(wave_results) - int(corrected_count),
        total_donor_available_area_bpm_s=donor_available,
        total_requested_area_bpm_s=requested,
        total_receiver_capacity_bpm_s=receiver_capacity,
        total_moved_area_bpm_s=moved,
        total_added_area_bpm_s=added,
        total_removed_area_bpm_s=removed,
        total_area_balance_error_bpm_s=total_error,
        max_abs_area_balance_error_bpm_s=max_error,
        total_capacity_limited_area_bpm_s=capacity_limited_area,
        moved_fraction_of_donor=(moved / donor_available if donor_available > 0.0 else 0.0),
        capacity_limited_wave_count=sum(
            result.capacity_limited for result in wave_results
        ),
        skip_reason_counts=dict(sorted(skip_reasons.items())),
        area_conservation_passed=area_passed,
    )


def compute_hrmod_hr_only(
    *,
    hr_samples: Sequence[HRSample],
    athlete_profile: AthleteHRProfile,
    config: HRmodConfig | None = None,
) -> HRmodResult:
    """Compute v4 solely from timestamped HR and explicit HR settings."""

    if not isinstance(athlete_profile, AthleteHRProfile):
        raise TypeError("athlete_profile must be an AthleteHRProfile")
    if config is None:
        config = HRmodConfig()
    if not isinstance(config, HRmodConfig):
        raise TypeError("config must be an HRmodConfig")

    cleaned = clean_hr_signal(hr_samples, config)
    finite_clean = cleaned.clean_hr[np.isfinite(cleaned.clean_hr)]
    if len(finite_clean) == 0:
        raise HRmodInputUnsuitableError(
            "HRMOD_NO_USABLE_HR",
            "no usable HR remains after transparent cleaning",
        )
    if np.any(finite_clean < athlete_profile.hr_floor_bpm) or np.any(
        finite_clean > athlete_profile.hrmax_bpm
    ):
        raise HRmodInputUnsuitableError(
            "HRMOD_HR_OUTSIDE_PROFILE",
            "clean HR lies outside the explicitly supplied HR floor/HRmax; "
            "adjust the profile or HR cleaning settings",
        )

    detection = detect_hr_waves(
        elapsed_s=cleaned.elapsed_s,
        dt_s=cleaned.dt_s,
        clean_hr=cleaned.clean_hr,
        segment_ids=cleaned.segment_ids,
        config=config,
    )
    redistribution = shift_mirror_wave_areas(
        clean_hr=cleaned.clean_hr,
        elapsed_s=cleaned.elapsed_s,
        dt_s=cleaned.dt_s,
        waves=detection.waves,
        athlete_profile=athlete_profile,
        config=config,
    )
    zone_summary, raw_zones, clean_zones, hrmod_zones = _zone_summaries(
        raw_hr=cleaned.raw_hr,
        clean_hr=cleaned.clean_hr,
        hrmod=redistribution.hrmod,
        dt_s=cleaned.dt_s,
        profile=athlete_profile,
    )
    wave_summary = _wave_summaries(
        cleaned=cleaned,
        detection=detection,
        redistribution=redistribution,
        raw_zones=raw_zones,
        clean_zones=clean_zones,
        hrmod_zones=hrmod_zones,
        profile=athlete_profile,
    )
    diagnostics = _diagnostics(
        cleaned=cleaned,
        detection=detection,
        redistribution=redistribution,
        config=config,
    )

    point_model_flags: list[set[str]] = [set() for _ in cleaned.samples]
    for index in range(len(cleaned.samples)):
        if (
            np.isfinite(cleaned.clean_hr[index])
            and not detection.trend_supported_mask[index]
        ):
            point_model_flags[index].add("INSUFFICIENT_DETECTION_SUPPORT")
        if detection.edge_affected_mask[index]:
            point_model_flags[index].add("DETECTION_EDGE_EFFECT")
        if cleaned.long_gap_mask[index]:
            point_model_flags[index].add("LONG_GAP")
    for result in redistribution.wave_results:
        wave = result.wave
        for index in range(wave.rise_start_index, wave.tail_end_index + 1):
            point_model_flags[index].update(result.flags)

    timeseries: list[HRmodTimeseriesPoint] = []
    for index, timestamp in enumerate(cleaned.timestamps):
        wave_id = int(detection.wave_ids[index])
        timeseries.append(
            HRmodTimeseriesPoint(
                timestamp=timestamp,
                elapsed_s=float(cleaned.elapsed_s[index]),
                dt_s=float(cleaned.dt_s[index]),
                raw_hr_bpm=_optional_float(cleaned.raw_hr[index]),
                clean_hr_bpm=_optional_float(cleaned.clean_hr[index]),
                h_detect_bpm=_optional_float(detection.h_detect[index]),
                trend_bpm_per_s=_optional_float(detection.trend_bpm_per_s[index]),
                segment_id=(
                    int(cleaned.segment_ids[index])
                    if cleaned.segment_ids[index] >= 0
                    else None
                ),
                wave_id=wave_id if wave_id >= 0 else None,
                wave_state=detection.wave_states[index],
                local_baseline_hr_bpm=_optional_float(
                    detection.local_baseline_hr[index]
                ),
                receiver_flag=bool(redistribution.receiver_mask[index]),
                donor_flag=bool(redistribution.donor_mask[index]),
                added_bpm=float(redistribution.added_bpm[index]),
                removed_bpm=float(redistribution.removed_bpm[index]),
                hrmod_bpm=_optional_float(redistribution.hrmod[index]),
                raw_hr_zone=raw_zones[index],
                clean_hr_zone=clean_zones[index],
                hrmod_zone=hrmod_zones[index],
                quality_flags=cleaned.quality_flags[index],
                model_flags=tuple(sorted(point_model_flags[index])),
            )
        )

    return HRmodResult(
        timeseries=tuple(timeseries),
        wave_summary=wave_summary,
        zone_summary=zone_summary,
        diagnostics=diagnostics,
        config=config,
        hr_input_hash=_hash_hr_input(cleaned.samples),
        athlete_hrmax_bpm=athlete_profile.hrmax_bpm,
        model_version=MODEL_VERSION,
    )


__all__ = ["compute_hrmod_hr_only"]
