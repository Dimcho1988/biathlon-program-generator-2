"""Public orchestration for the physically isolated HR-only HRmod core."""

from __future__ import annotations

import hashlib
import json
from datetime import timezone
from math import isfinite
from typing import Sequence

import numpy as np

from .conservative_redistribution import (
    ConservativeRedistributionResult,
    redistribute_conservatively,
)
from .episode_detection import EpisodeDetectionResult, detect_response_episodes
from .inverse_kinetics import InverseKineticsResult, compute_inverse_kinetics
from .schemas import (
    MODEL_VERSION,
    AthleteHRProfile,
    EpisodeSummary,
    HRmodConfig,
    HRmodDiagnostics,
    HRmodResult,
    HRmodTimeseriesPoint,
    HRSample,
    ZoneSummary,
)
from .signal_cleaning import CleanedHRSignal, clean_hr_signal


def _optional_float(value: float) -> float | None:
    return float(value) if isfinite(float(value)) else None


def _hash_hr_input(samples: tuple[HRSample, ...]) -> str:
    """Hash only timestamp, measured HR, and HR quality flags."""

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
) -> tuple[tuple[ZoneSummary, ...], tuple[str | None, ...], tuple[str | None, ...], tuple[str | None, ...]]:
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
            sum(dt_s[index] for index, label in enumerate(clean_labels) if label == zone.name)
        )
        hrmod_seconds = float(
            sum(dt_s[index] for index, label in enumerate(hrmod_labels) if label == zone.name)
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
    return (
        tuple(summaries),
        raw_labels,
        clean_labels,
        hrmod_labels,
    )


def _episode_summaries(
    *,
    cleaned: CleanedHRSignal,
    redistribution: ConservativeRedistributionResult,
) -> tuple[EpisodeSummary, ...]:
    summaries: list[EpisodeSummary] = []
    for result in redistribution.episode_results:
        episode = result.episode
        start = episode.start_index
        end = episode.end_index
        summaries.append(
            EpisodeSummary(
                episode_id=episode.episode_id,
                segment_id=episode.segment_id,
                start_timestamp=cleaned.timestamps[start],
                end_timestamp=cleaned.timestamps[end],
                start_elapsed_s=float(cleaned.elapsed_s[start]),
                end_elapsed_s=float(cleaned.elapsed_s[end]),
                duration_s=max(
                    0.0, float(cleaned.elapsed_s[end] - cleaned.elapsed_s[start])
                ),
                state=episode.state,
                complete=episode.complete,
                corrected=result.corrected,
                incomplete_reason=episode.incomplete_reason,
                lobe_count=len(episode.lobes),
                positive_lobe_count=episode.positive_lobe_count,
                negative_lobe_count=episode.negative_lobe_count,
                positive_area_bpm_s=result.positive_area_bpm_s,
                negative_area_bpm_s=result.negative_area_bpm_s,
                target_balanced_area_bpm_s=result.target_balanced_area_bpm_s,
                moved_area_bpm_s=result.moved_area_bpm_s,
                added_area_bpm_s=result.added_area_bpm_s,
                removed_area_bpm_s=result.removed_area_bpm_s,
                area_balance_error_bpm_s=result.area_balance_error_bpm_s,
                capacity_limited_area_bpm_s=result.capacity_limited_area_bpm_s,
                unpaired_positive_area_bpm_s=result.unpaired_positive_area_bpm_s,
                unpaired_negative_area_bpm_s=result.unpaired_negative_area_bpm_s,
                positive_capacity_bpm_s=result.positive_capacity_bpm_s,
                negative_capacity_bpm_s=result.negative_capacity_bpm_s,
                capacity_ratio=result.capacity_ratio,
                flags=result.flags,
            )
        )
    return tuple(summaries)


def _diagnostics(
    *,
    cleaned: CleanedHRSignal,
    inverse: InverseKineticsResult,
    episode_detection: EpisodeDetectionResult,
    redistribution: ConservativeRedistributionResult,
    config: HRmodConfig,
) -> HRmodDiagnostics:
    count = len(cleaned.samples)
    raw_valid = int(np.count_nonzero(np.isfinite(cleaned.raw_hr)))
    clean_valid = int(np.count_nonzero(np.isfinite(cleaned.clean_hr)))
    artifact_count = int(np.count_nonzero(cleaned.artifact_mask))
    interpolated_count = int(np.count_nonzero(cleaned.interpolated_mask))
    derivative_count = int(np.count_nonzero(inverse.derivative_supported_mask))
    intervals = np.diff(cleaned.elapsed_s)
    intervals = intervals[np.isfinite(intervals) & (intervals > 0.0)]
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

    episode_results = redistribution.episode_results
    complete_count = sum(result.episode.complete for result in episode_results)
    incomplete_count = len(episode_results) - complete_count
    corrected_count = sum(result.corrected for result in episode_results)
    positive_area = float(sum(result.positive_area_bpm_s for result in episode_results))
    negative_area = float(sum(result.negative_area_bpm_s for result in episode_results))
    added_area = float(sum(result.added_area_bpm_s for result in episode_results))
    removed_area = float(sum(result.removed_area_bpm_s for result in episode_results))
    errors = [result.area_balance_error_bpm_s for result in episode_results]
    total_error = float(sum(errors))
    max_error = float(max((abs(value) for value in errors), default=0.0))
    capacity_limited = float(
        sum(result.capacity_limited_area_bpm_s for result in episode_results)
    )
    unpaired_positive = float(
        sum(result.unpaired_positive_area_bpm_s for result in episode_results)
    )
    unpaired_negative = float(
        sum(result.unpaired_negative_area_bpm_s for result in episode_results)
    )
    eligible_results = [
        result
        for result in episode_results
        if result.episode.complete or config.edge_episode_policy == "correct_if_balanced"
    ]
    total_target = float(
        sum(result.target_balanced_area_bpm_s for result in eligible_results)
    )
    total_moved = float(sum(result.moved_area_bpm_s for result in eligible_results))
    capacity_ratio = 1.0 if total_target <= 0.0 else total_moved / total_target

    flags: set[str] = set()
    if artifact_count:
        flags.add("HR_ARTIFACTS_PRESENT")
    if interpolated_count:
        flags.add("INTERPOLATED_HR")
    if cleaned.long_gap_count or np.any(cleaned.long_gap_mask):
        flags.add("LONG_GAP")
    if derivative_count < clean_valid:
        flags.add("INSUFFICIENT_DERIVATIVE_SUPPORT")
    for result in episode_results:
        flags.update(result.flags)
    area_passed = not any("AREA_BALANCE_FAILED" in result.flags for result in episode_results)

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
        derivative_supported_samples=derivative_count,
        derivative_support_fraction=(derivative_count / clean_valid if clean_valid else 0.0),
        segment_count=len({int(value) for value in cleaned.segment_ids if value >= 0}),
        long_gap_count=cleaned.long_gap_count,
        edge_affected_samples=int(np.count_nonzero(inverse.edge_affected_mask)),
        gap_affected_samples=int(np.count_nonzero(cleaned.gap_affected_mask)),
        complete_episode_count=int(complete_count),
        incomplete_episode_count=int(incomplete_count),
        corrected_episode_count=int(corrected_count),
        total_positive_area_bpm_s=positive_area,
        total_negative_area_bpm_s=negative_area,
        total_added_area_bpm_s=added_area,
        total_removed_area_bpm_s=removed_area,
        total_area_balance_error_bpm_s=total_error,
        max_abs_area_balance_error_bpm_s=max_error,
        total_capacity_limited_area_bpm_s=capacity_limited,
        total_unpaired_positive_area_bpm_s=unpaired_positive,
        total_unpaired_negative_area_bpm_s=unpaired_negative,
        capacity_ratio=capacity_ratio,
        area_conservation_passed=area_passed,
    )


def compute_hrmod_hr_only(
    *,
    hr_samples: Sequence[HRSample],
    athlete_profile: AthleteHRProfile,
    config: HRmodConfig | None = None,
) -> HRmodResult:
    """Compute HRmod solely from timestamped HR and explicit HR settings.

    This is an offline function: ``delay_s`` uses HR look-ahead within each
    continuous segment.  The strict input type contains no reference channels.
    """

    if not isinstance(athlete_profile, AthleteHRProfile):
        raise TypeError("athlete_profile must be an AthleteHRProfile")
    if config is None:
        config = HRmodConfig()
    if not isinstance(config, HRmodConfig):
        raise TypeError("config must be an HRmodConfig")

    cleaned = clean_hr_signal(hr_samples, config)
    finite_clean = cleaned.clean_hr[np.isfinite(cleaned.clean_hr)]
    if len(finite_clean) == 0:
        raise ValueError("no usable HR remains after transparent cleaning")
    if np.any(finite_clean < athlete_profile.hr_floor_bpm) or np.any(
        finite_clean > athlete_profile.hrmax_bpm
    ):
        raise ValueError(
            "clean HR lies outside the explicitly supplied HR floor/HRmax; "
            "adjust the profile or HR cleaning settings"
        )

    hr_input_hash = _hash_hr_input(cleaned.samples)
    inverse = compute_inverse_kinetics(
        elapsed_s=cleaned.elapsed_s,
        clean_hr=cleaned.clean_hr,
        segment_ids=cleaned.segment_ids,
        config=config,
    )
    detection = detect_response_episodes(
        elapsed_s=cleaned.elapsed_s,
        dt_s=cleaned.dt_s,
        segment_ids=cleaned.segment_ids,
        raw_correction=inverse.raw_correction,
        config=config,
    )
    redistribution = redistribute_conservatively(
        clean_hr=cleaned.clean_hr,
        raw_correction=inverse.raw_correction,
        dt_s=cleaned.dt_s,
        episodes=detection.episodes,
        athlete_profile=athlete_profile,
        config=config,
    )

    episode_summary = _episode_summaries(
        cleaned=cleaned, redistribution=redistribution
    )
    zone_summary, raw_zones, clean_zones, hrmod_zones = _zone_summaries(
        raw_hr=cleaned.raw_hr,
        clean_hr=cleaned.clean_hr,
        hrmod=redistribution.hrmod,
        dt_s=cleaned.dt_s,
        profile=athlete_profile,
    )
    diagnostics = _diagnostics(
        cleaned=cleaned,
        inverse=inverse,
        episode_detection=detection,
        redistribution=redistribution,
        config=config,
    )

    point_model_flags: list[set[str]] = [set() for _ in cleaned.samples]
    for index in range(len(cleaned.samples)):
        if np.isfinite(cleaned.clean_hr[index]) and not inverse.derivative_supported_mask[index]:
            point_model_flags[index].add("INSUFFICIENT_DERIVATIVE_SUPPORT")
        if inverse.edge_affected_mask[index]:
            point_model_flags[index].add("LOOKAHEAD_EDGE_EFFECT")
        if cleaned.long_gap_mask[index]:
            point_model_flags[index].add("LONG_GAP")
        if detection.suppressed_lobe_mask[index]:
            point_model_flags[index].add("LOBE_SUPPRESSED")
    for result in redistribution.episode_results:
        start = result.episode.start_index
        end = result.episode.end_index
        for index in range(start, end + 1):
            point_model_flags[index].update(result.flags)

    timeseries: list[HRmodTimeseriesPoint] = []
    for index, timestamp in enumerate(cleaned.timestamps):
        episode_id = int(detection.episode_ids[index])
        timeseries.append(
            HRmodTimeseriesPoint(
                timestamp=timestamp,
                elapsed_s=float(cleaned.elapsed_s[index]),
                dt_s=float(cleaned.dt_s[index]),
                raw_hr_bpm=_optional_float(cleaned.raw_hr[index]),
                clean_hr_bpm=_optional_float(cleaned.clean_hr[index]),
                smoothed_hr_bpm=_optional_float(inverse.smoothed_hr[index]),
                # This is the robust derivative at t + delay actually used in
                # d_raw, not a derivative of measured/raw HR.
                derivative_bpm_per_s=_optional_float(
                    inverse.lookahead_derivative_bpm_per_s[index]
                ),
                lookahead_hr_bpm=_optional_float(inverse.lookahead_hr[index]),
                provisional_demand_bpm=_optional_float(
                    inverse.provisional_demand[index]
                ),
                raw_correction_bpm=_optional_float(inverse.raw_correction[index]),
                added_correction_bpm=float(redistribution.added_correction[index]),
                removed_correction_bpm=float(redistribution.removed_correction[index]),
                hrmod_bpm=_optional_float(redistribution.hrmod[index]),
                segment_id=(
                    int(cleaned.segment_ids[index])
                    if cleaned.segment_ids[index] >= 0
                    else None
                ),
                episode_id=episode_id if episode_id >= 0 else None,
                episode_state=detection.episode_states[index],
                raw_hr_zone=raw_zones[index],
                clean_hr_zone=clean_zones[index],
                hrmod_zone=hrmod_zones[index],
                quality_flags=cleaned.quality_flags[index],
                model_flags=tuple(sorted(point_model_flags[index])),
            )
        )

    return HRmodResult(
        timeseries=tuple(timeseries),
        episode_summary=episode_summary,
        zone_summary=zone_summary,
        diagnostics=diagnostics,
        config=config,
        hr_input_hash=hr_input_hash,
        model_version=MODEL_VERSION,
    )


__all__ = ["compute_hrmod_hr_only"]
