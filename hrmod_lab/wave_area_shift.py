"""Exact, capacity-aware HR area shift for detected HR waves."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .schemas import AthleteHRProfile, HRmodConfig
from .wave_detection import DetectedWave


@dataclass(frozen=True, slots=True)
class WaveAreaShift:
    wave: DetectedWave
    corrected: bool
    donor_floor_bpm: float | None
    receiver_duration_s: float
    donor_duration_s: float
    donor_available_area_bpm_s: float
    requested_area_bpm_s: float
    receiver_capacity_bpm_s: float
    moved_area_bpm_s: float
    added_area_bpm_s: float
    removed_area_bpm_s: float
    area_balance_error_bpm_s: float
    capacity_limited_area_bpm_s: float
    capacity_limited: bool
    skip_reason: str | None
    flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WaveAreaShiftResult:
    hrmod: np.ndarray
    added_bpm: np.ndarray
    removed_bpm: np.ndarray
    wave_results: tuple[WaveAreaShift, ...]


def _proportional_allocation(
    *, shape: np.ndarray, dt_s: np.ndarray, target_area: float
) -> np.ndarray:
    """Allocate ``target_area`` proportionally to non-negative ``shape``."""

    result = np.zeros_like(shape, dtype=float)
    if target_area <= 0.0:
        return result
    valid = np.isfinite(shape) & (shape > 0.0) & np.isfinite(dt_s) & (dt_s > 0.0)
    available = float(np.sum(shape[valid] * dt_s[valid]))
    if available <= 0.0:
        return result
    if target_area > available + 1e-9:
        raise ValueError("target area exceeds allocation capacity")
    result[valid] = shape[valid] * min(1.0, target_area / available)

    # One scaling pass removes accumulated summation error while preserving the
    # same proportional shape.  The ratio cannot exceed one beyond round-off
    # because target_area is capped by available above.
    allocated = float(np.sum(result[valid] * dt_s[valid]))
    if allocated > 0.0 and allocated != target_area:
        result[valid] *= target_area / allocated
    return result


def shift_wave_areas(
    *,
    clean_hr: np.ndarray,
    dt_s: np.ndarray,
    waves: tuple[DetectedWave, ...],
    athlete_profile: AthleteHRProfile,
    config: HRmodConfig,
) -> WaveAreaShiftResult:
    """Move donor HR area into each wave's earlier receiver window.

    With optional per-sample safeguards disabled, removal is exactly
    proportional to donor excess above ``max(B, HR_floor)`` and addition is
    exactly proportional to receiver capacity below HRmax.
    """

    clean_hr = np.asarray(clean_hr, dtype=float)
    dt_s = np.asarray(dt_s, dtype=float)
    if len(clean_hr) != len(dt_s):
        raise ValueError("area-shift inputs must have equal lengths")
    if np.any(dt_s < 0.0):
        raise ValueError("dt_s must be non-negative")

    added = np.zeros(len(clean_hr), dtype=float)
    removed = np.zeros(len(clean_hr), dtype=float)
    occupied = np.zeros(len(clean_hr), dtype=bool)
    results: list[WaveAreaShift] = []
    tolerance = config.area_conservation_tolerance_bpm_s

    for wave in waves:
        receiver_indices = np.arange(
            wave.rise_start_index, wave.peak_index + 1, dtype=int
        )
        donor_indices = np.arange(
            wave.peak_index + 1, wave.tail_end_index + 1, dtype=int
        )
        if np.intersect1d(receiver_indices, donor_indices).size:
            raise RuntimeError("receiver and donor windows overlap")
        all_indices = np.concatenate((receiver_indices, donor_indices))
        if np.any(occupied[all_indices]):
            raise RuntimeError("detected wave windows overlap")
        occupied[all_indices] = True

        receiver_duration = float(np.sum(dt_s[receiver_indices]))
        donor_duration = float(np.sum(dt_s[donor_indices]))
        donor_floor = (
            max(float(wave.baseline_hr_bpm), athlete_profile.hr_floor_bpm)
            if wave.baseline_hr_bpm is not None
            else None
        )

        donor_shape = np.zeros(len(clean_hr), dtype=float)
        receiver_shape = np.zeros(len(clean_hr), dtype=float)
        if donor_floor is not None:
            donor_shape[donor_indices] = np.maximum(
                0.0, clean_hr[donor_indices] - donor_floor
            )
        receiver_shape[receiver_indices] = np.maximum(
            0.0, athlete_profile.hrmax_bpm - clean_hr[receiver_indices]
        )
        donor_available = float(
            np.sum(donor_shape[donor_indices] * dt_s[donor_indices])
        )
        requested = float(config.alpha * donor_available)
        receiver_capacity = float(
            np.sum(receiver_shape[receiver_indices] * dt_s[receiver_indices])
        )

        donor_allocation_shape = donor_shape.copy()
        receiver_allocation_shape = receiver_shape.copy()
        flags: set[str] = set()
        if config.max_removal_bpm is not None:
            donor_allocation_shape[donor_indices] = np.minimum(
                donor_allocation_shape[donor_indices], config.max_removal_bpm
            )
            flags.add("PER_SAMPLE_REMOVAL_LIMIT_ENABLED")
        if config.max_addition_bpm is not None:
            receiver_allocation_shape[receiver_indices] = np.minimum(
                receiver_allocation_shape[receiver_indices], config.max_addition_bpm
            )
            flags.add("PER_SAMPLE_ADDITION_LIMIT_ENABLED")
        effective_donor_capacity = float(
            np.sum(donor_allocation_shape[donor_indices] * dt_s[donor_indices])
        )
        effective_receiver_capacity = float(
            np.sum(
                receiver_allocation_shape[receiver_indices]
                * dt_s[receiver_indices]
            )
        )

        skip_reason: str | None = None
        policy_eligible = wave.complete
        if not policy_eligible:
            skip_reason = wave.incomplete_reason or "incomplete_wave"
            flags.add("INCOMPLETE_WAVE")
            if wave.incomplete_reason == "insufficient_baseline_history":
                flags.add("INCOMPLETE_WAVE_START")
            else:
                flags.add("INCOMPLETE_WAVE_END")
            if wave.end_reason == "long_gap":
                flags.add("LONG_GAP")
        elif receiver_duration < config.min_receiver_duration_s:
            skip_reason = "receiver_too_short"
        elif donor_duration < config.min_donor_duration_s:
            skip_reason = "donor_too_short"
        elif config.alpha == 0.0:
            skip_reason = "alpha_zero"
        elif donor_available <= 0.0:
            skip_reason = "no_donor_area"
        elif receiver_capacity <= 0.0:
            skip_reason = "no_receiver_capacity"

        moved = 0.0
        allocation_eligible = skip_reason is None
        if allocation_eligible:
            moved = min(
                requested, effective_donor_capacity, effective_receiver_capacity
            )
            if moved <= 0.0:
                moved = 0.0
                skip_reason = "zero_moved_area"

        capacity_evaluated = allocation_eligible or skip_reason == "no_receiver_capacity"
        capacity_limited = bool(
            capacity_evaluated
            and requested > 0.0
            and moved < requested - tolerance
        )
        capacity_limited_area = (
            max(0.0, requested - moved) if capacity_limited else 0.0
        )
        if capacity_limited:
            flags.add("CAPACITY_LIMITED")

        wave_added = np.zeros(len(clean_hr), dtype=float)
        wave_removed = np.zeros(len(clean_hr), dtype=float)
        if moved > 0.0:
            wave_added = _proportional_allocation(
                shape=receiver_allocation_shape,
                dt_s=dt_s,
                target_area=moved,
            )
            wave_removed = _proportional_allocation(
                shape=donor_allocation_shape,
                dt_s=dt_s,
                target_area=moved,
            )
            added += wave_added
            removed += wave_removed

        added_area = float(np.sum(wave_added * dt_s))
        removed_area = float(np.sum(wave_removed * dt_s))
        balance_error = added_area - removed_area
        if donor_floor is not None and len(donor_indices):
            # A measured donor sample may already have fallen below the local
            # baseline F.  Its q_i is then zero and it must remain untouched;
            # the invariant is that model *removal* never exceeds q_i, not
            # that the observed clean signal itself is clipped up to F.
            if np.any(
                wave_removed[donor_indices]
                > donor_shape[donor_indices] + 1e-9
            ):
                raise RuntimeError("wave removal exceeded excess above donor floor")
        if len(receiver_indices):
            receiver_after = clean_hr[receiver_indices] + wave_added[receiver_indices]
            if np.any(receiver_after > athlete_profile.hrmax_bpm + 1e-9):
                raise RuntimeError("wave addition exceeded HRmax")
        if abs(balance_error) > tolerance:
            raise RuntimeError(
                "wave area conservation failed: "
                f"wave={wave.wave_id}, error={balance_error:.12g} bpm*s"
            )
        if moved > 0.0:
            flags.add("AREA_CONSERVATION_PASSED")

        results.append(
            WaveAreaShift(
                wave=wave,
                corrected=moved > 0.0,
                donor_floor_bpm=donor_floor,
                receiver_duration_s=receiver_duration,
                donor_duration_s=donor_duration,
                donor_available_area_bpm_s=donor_available,
                requested_area_bpm_s=requested,
                receiver_capacity_bpm_s=receiver_capacity,
                moved_area_bpm_s=moved,
                added_area_bpm_s=added_area,
                removed_area_bpm_s=removed_area,
                area_balance_error_bpm_s=balance_error,
                capacity_limited_area_bpm_s=capacity_limited_area,
                capacity_limited=capacity_limited,
                skip_reason=skip_reason,
                flags=tuple(sorted(flags)),
            )
        )

    hrmod = clean_hr.copy()
    finite = np.isfinite(clean_hr)
    hrmod[finite] = clean_hr[finite] + added[finite] - removed[finite]
    if np.any(hrmod[finite] > athlete_profile.hrmax_bpm + 1e-9):
        raise RuntimeError("area addition exceeded HRmax")
    if np.any(hrmod[finite] < athlete_profile.hr_floor_bpm - 1e-9):
        raise RuntimeError("area removal fell below HR_floor")

    return WaveAreaShiftResult(
        hrmod=hrmod,
        added_bpm=added,
        removed_bpm=removed,
        wave_results=tuple(results),
    )


__all__ = ["WaveAreaShift", "WaveAreaShiftResult", "shift_wave_areas"]
