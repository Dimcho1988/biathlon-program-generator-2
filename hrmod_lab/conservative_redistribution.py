"""Area-conserving, bounded redistribution for detected HR episodes."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .episode_detection import DetectedEpisode
from .schemas import AthleteHRProfile, HRmodConfig


@dataclass(frozen=True, slots=True)
class EpisodeRedistribution:
    episode: DetectedEpisode
    corrected: bool
    positive_area_bpm_s: float
    negative_area_bpm_s: float
    target_balanced_area_bpm_s: float
    moved_area_bpm_s: float
    added_area_bpm_s: float
    removed_area_bpm_s: float
    area_balance_error_bpm_s: float
    capacity_limited_area_bpm_s: float
    unpaired_positive_area_bpm_s: float
    unpaired_negative_area_bpm_s: float
    positive_capacity_bpm_s: float
    negative_capacity_bpm_s: float
    capacity_ratio: float
    flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConservativeRedistributionResult:
    added_correction: np.ndarray
    removed_correction: np.ndarray
    hrmod: np.ndarray
    episode_results: tuple[EpisodeRedistribution, ...]


def _capped_water_fill(
    *, shape: np.ndarray, caps: np.ndarray, dt_s: np.ndarray, target_area: float
) -> np.ndarray:
    """Allocate ``target_area`` proportionally, saturating caps deterministically."""

    allocation = np.zeros_like(shape, dtype=float)
    if target_area <= 0.0:
        return allocation
    eligible = np.flatnonzero((shape > 0.0) & (caps > 0.0) & (dt_s > 0.0))
    if len(eligible) == 0:
        return allocation
    capacity = float(np.sum(caps[eligible] * dt_s[eligible]))
    target = min(float(target_area), capacity)
    remaining = target
    active = eligible.copy()
    tolerance = max(1e-12, abs(target) * 1e-14)

    while len(active) and remaining > tolerance:
        denominator = float(np.sum(shape[active] * dt_s[active]))
        if denominator <= 0.0:
            break
        scale = remaining / denominator
        proposed = scale * shape[active]
        saturated_mask = proposed >= caps[active] - tolerance
        if not np.any(saturated_mask):
            allocation[active] = proposed
            remaining = 0.0
            break
        saturated = active[saturated_mask]
        allocation[saturated] = caps[saturated]
        remaining -= float(np.sum(caps[saturated] * dt_s[saturated]))
        remaining = max(0.0, remaining)
        active = active[~saturated_mask]

    # Correct floating residuals in stable index order.  This makes the discrete
    # area invariant as exact as binary floating arithmetic permits.
    residual = target - float(np.sum(allocation * dt_s))
    if residual > tolerance:
        for index in eligible:
            room = max(0.0, caps[index] - allocation[index])
            area_room = room * dt_s[index]
            if area_room <= 0.0:
                continue
            area = min(residual, area_room)
            allocation[index] += area / dt_s[index]
            residual -= area
            if residual <= tolerance:
                break
    elif residual < -tolerance:
        excess = -residual
        for index in eligible[::-1]:
            area_available = allocation[index] * dt_s[index]
            if area_available <= 0.0:
                continue
            area = min(excess, area_available)
            allocation[index] -= area / dt_s[index]
            excess -= area
            if excess <= tolerance:
                break
    return allocation


def redistribute_conservatively(
    *,
    clean_hr: np.ndarray,
    raw_correction: np.ndarray,
    dt_s: np.ndarray,
    episodes: tuple[DetectedEpisode, ...],
    athlete_profile: AthleteHRProfile,
    config: HRmodConfig,
) -> ConservativeRedistributionResult:
    """Move equal positive/negative HR area within each eligible episode."""

    clean_hr = np.asarray(clean_hr, dtype=float)
    raw_correction = np.asarray(raw_correction, dtype=float)
    dt_s = np.asarray(dt_s, dtype=float)
    if not (len(clean_hr) == len(raw_correction) == len(dt_s)):
        raise ValueError("redistribution inputs must have equal lengths")
    count = len(clean_hr)
    added = np.zeros(count, dtype=float)
    removed = np.zeros(count, dtype=float)
    hrmod = clean_hr.copy()
    results: list[EpisodeRedistribution] = []

    for episode in episodes:
        selection = np.arange(episode.start_index, episode.end_index + 1, dtype=int)
        valid = np.isfinite(clean_hr[selection]) & np.isfinite(raw_correction[selection])
        indices = selection[valid]
        shape_positive = np.zeros(count, dtype=float)
        shape_negative = np.zeros(count, dtype=float)
        shape_positive[indices] = np.maximum(raw_correction[indices], 0.0)
        shape_negative[indices] = np.maximum(-raw_correction[indices], 0.0)
        positive_area = float(np.sum(shape_positive[indices] * dt_s[indices]))
        negative_area = float(np.sum(shape_negative[indices] * dt_s[indices]))
        target = float(config.alpha * min(positive_area, negative_area))

        physical_add_caps = np.zeros(count, dtype=float)
        physical_remove_caps = np.zeros(count, dtype=float)
        physical_add_caps[indices] = np.maximum(
            0.0, athlete_profile.hrmax_bpm - clean_hr[indices]
        )
        physical_remove_caps[indices] = np.maximum(
            0.0, clean_hr[indices] - athlete_profile.hr_floor_bpm
        )
        add_caps = physical_add_caps.copy()
        remove_caps = physical_remove_caps.copy()
        if config.max_addition_bpm is not None:
            add_caps[indices] = np.minimum(add_caps[indices], config.max_addition_bpm)
        if config.max_removal_bpm is not None:
            remove_caps[indices] = np.minimum(remove_caps[indices], config.max_removal_bpm)
        add_caps[shape_positive <= 0.0] = 0.0
        remove_caps[shape_negative <= 0.0] = 0.0
        physical_add_caps[shape_positive <= 0.0] = 0.0
        physical_remove_caps[shape_negative <= 0.0] = 0.0

        positive_capacity = float(np.sum(add_caps[indices] * dt_s[indices]))
        negative_capacity = float(np.sum(remove_caps[indices] * dt_s[indices]))
        physical_positive_capacity = float(
            np.sum(physical_add_caps[indices] * dt_s[indices])
        )
        physical_negative_capacity = float(
            np.sum(physical_remove_caps[indices] * dt_s[indices])
        )

        policy_eligible = episode.complete or config.edge_episode_policy == "correct_if_balanced"
        moved = min(target, positive_capacity, negative_capacity) if policy_eligible else 0.0
        episode_added = _capped_water_fill(
            shape=shape_positive,
            caps=add_caps,
            dt_s=dt_s,
            target_area=moved,
        )
        episode_removed = _capped_water_fill(
            shape=shape_negative,
            caps=remove_caps,
            dt_s=dt_s,
            target_area=moved,
        )
        added[selection] += episode_added[selection]
        removed[selection] += episode_removed[selection]
        hrmod[selection] = clean_hr[selection] + added[selection] - removed[selection]

        # Numerical clipping can only remove sub-tolerance roundoff because the
        # caps above already encode the physical bounds.
        hrmod[selection] = np.minimum(hrmod[selection], athlete_profile.hrmax_bpm)
        hrmod[selection] = np.maximum(hrmod[selection], athlete_profile.hr_floor_bpm)
        added_area = float(np.sum(episode_added[indices] * dt_s[indices]))
        removed_area = float(np.sum(episode_removed[indices] * dt_s[indices]))
        balance_error = float(
            np.sum((hrmod[indices] - clean_hr[indices]) * dt_s[indices])
        )
        capacity_limited = max(0.0, target - moved) if policy_eligible else 0.0
        unpaired_positive = max(0.0, positive_area - negative_area)
        unpaired_negative = max(0.0, negative_area - positive_area)
        capacity_ratio = 1.0 if target <= 0.0 else moved / target
        flags = set(episode.flags)
        tolerance = config.area_conservation_tolerance_bpm_s
        if unpaired_positive > tolerance:
            flags.add("UNPAIRED_POSITIVE_AREA")
        if unpaired_negative > tolerance:
            flags.add("UNPAIRED_NEGATIVE_AREA")
        if capacity_limited > tolerance:
            flags.add("CAPACITY_LIMITED")
        if target > physical_positive_capacity + tolerance:
            flags.add("HRMAX_LIMITED")
        elif moved > 0.0 and np.any(
            (episode_added[indices] > 0.0)
            & np.isclose(
                episode_added[indices],
                physical_add_caps[indices],
                rtol=0.0,
                atol=tolerance,
            )
        ):
            flags.add("HRMAX_LIMITED")
        if target > physical_negative_capacity + tolerance:
            flags.add("HR_FLOOR_LIMITED")
        elif moved > 0.0 and np.any(
            (episode_removed[indices] > 0.0)
            & np.isclose(
                episode_removed[indices],
                physical_remove_caps[indices],
                rtol=0.0,
                atol=tolerance,
            )
        ):
            flags.add("HR_FLOOR_LIMITED")
        if abs(balance_error) > tolerance or abs(added_area - removed_area) > tolerance:
            flags.add("AREA_BALANCE_FAILED")

        results.append(
            EpisodeRedistribution(
                episode=episode,
                corrected=bool(policy_eligible and moved > 0.0),
                positive_area_bpm_s=positive_area,
                negative_area_bpm_s=negative_area,
                target_balanced_area_bpm_s=target,
                moved_area_bpm_s=moved,
                added_area_bpm_s=added_area,
                removed_area_bpm_s=removed_area,
                area_balance_error_bpm_s=balance_error,
                capacity_limited_area_bpm_s=capacity_limited,
                unpaired_positive_area_bpm_s=unpaired_positive,
                unpaired_negative_area_bpm_s=unpaired_negative,
                positive_capacity_bpm_s=positive_capacity,
                negative_capacity_bpm_s=negative_capacity,
                capacity_ratio=capacity_ratio,
                flags=tuple(sorted(flags)),
            )
        )

    # Missing/unusable HR rows stay missing, and alpha=0 remains bit-for-bit
    # equal to the cleaned signal (including NaNs).
    if config.alpha == 0.0:
        added.fill(0.0)
        removed.fill(0.0)
        hrmod = clean_hr.copy()

    return ConservativeRedistributionResult(
        added_correction=added,
        removed_correction=removed,
        hrmod=hrmod,
        episode_results=tuple(results),
    )


__all__ = [
    "ConservativeRedistributionResult",
    "EpisodeRedistribution",
    "redistribute_conservatively",
]
