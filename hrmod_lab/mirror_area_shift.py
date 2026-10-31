"""Lightweight HR-only mirror-area redistribution for HRmod v4.

The module deliberately accepts only cleaned HR, elapsed/sample durations,
detected HR waves and the explicit athlete HR profile.  Reference channels
cannot enter the candidate computation.  The same bounded allocator can be
reused by the post-core terrain layer with an explicit donor eligibility mask.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .schemas import AthleteHRProfile, HRmodConfig
from .wave_detection_v4 import DetectedWave


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
    receiver_mask: np.ndarray
    donor_mask: np.ndarray
    wave_results: tuple[WaveAreaShift, ...]


@dataclass(frozen=True, slots=True)
class MirrorAllocation:
    added: np.ndarray
    removed: np.ndarray
    receiver_mask: np.ndarray
    donor_mask: np.ndarray
    donor_available_area_bpm_s: float
    requested_area_bpm_s: float
    receiver_capacity_bpm_s: float
    moved_area_bpm_s: float
    capacity_limited: bool


def _bounded_weighted_allocation(
    *, weights: np.ndarray, caps: np.ndarray, dt_s: np.ndarray, target_area: float
) -> np.ndarray:
    """Water-fill ``target_area`` with fixed weights and per-sample caps."""

    result = np.zeros_like(weights, dtype=float)
    valid = (
        np.isfinite(weights)
        & (weights > 0.0)
        & np.isfinite(caps)
        & (caps > 0.0)
        & np.isfinite(dt_s)
        & (dt_s > 0.0)
    )
    if target_area <= 0.0 or not np.any(valid):
        return result
    available = float(np.sum(caps[valid] * dt_s[valid]))
    target = min(float(target_area), available)
    if target <= 0.0:
        return result

    low = 0.0
    high = 1.0

    def area(scale: float) -> float:
        return float(
            np.sum(
                np.minimum(caps[valid], scale * weights[valid]) * dt_s[valid]
            )
        )

    while area(high) < target:
        high *= 2.0
    for _ in range(80):
        middle = (low + high) / 2.0
        if area(middle) < target:
            low = middle
        else:
            high = middle
    result[valid] = np.minimum(caps[valid], high * weights[valid])

    # One deterministic correction removes the final floating summation bit.
    allocated = float(np.sum(result * dt_s))
    if allocated > 0.0 and abs(allocated - target) > 1e-12:
        free = valid & (result < caps - 1e-12)
        free_duration = float(np.sum(dt_s[free]))
        if free_duration > 0.0:
            result[free] += (target - allocated) / free_duration
    return result


def allocate_mirror_wave(
    *,
    clean_hr: np.ndarray,
    elapsed_s: np.ndarray,
    dt_s: np.ndarray,
    receiver_indices: np.ndarray,
    donor_indices: np.ndarray,
    donor_floor_bpm: float,
    hrmax_bpm: float,
    alpha: float,
    max_addition_bpm: float | None = None,
    max_removal_bpm: float | None = None,
    donor_eligible_mask: np.ndarray | None = None,
) -> MirrorAllocation:
    """Mirror post-peak excess back across the complete pre-peak rise.

    The earliest post-peak donor shape maps nearest the peak; the late donor
    tail maps toward rise start.  Durations are phase-normalised, while exact
    area conservation is enforced using the original ``dt_s`` values.
    """

    clean_hr = np.asarray(clean_hr, dtype=float)
    elapsed_s = np.asarray(elapsed_s, dtype=float)
    dt_s = np.asarray(dt_s, dtype=float)
    receiver = np.asarray(receiver_indices, dtype=int)
    donor = np.asarray(donor_indices, dtype=int)
    count = len(clean_hr)
    zero = np.zeros((count,), dtype=float)
    false = np.zeros((count,), dtype=bool)
    if len(receiver) == 0 or len(donor) == 0:
        return MirrorAllocation(zero, zero.copy(), false, false.copy(), 0, 0, 0, 0, False)

    donor_shape = np.maximum(0.0, clean_hr[donor] - float(donor_floor_bpm))
    if donor_eligible_mask is not None:
        eligible = np.asarray(donor_eligible_mask, dtype=bool)
        if len(eligible) != count:
            raise ValueError("donor_eligible_mask must match the HR series")
        donor_shape = np.where(eligible[donor], donor_shape, 0.0)
    donor_available = float(np.sum(donor_shape * dt_s[donor]))
    requested = float(alpha) * donor_available

    receiver_caps = np.maximum(0.0, float(hrmax_bpm) - clean_hr[receiver])
    if max_addition_bpm is not None:
        receiver_caps = np.minimum(receiver_caps, float(max_addition_bpm))
    removal_caps = donor_shape.copy()
    if max_removal_bpm is not None:
        removal_caps = np.minimum(removal_caps, float(max_removal_bpm))
    receiver_capacity = float(np.sum(receiver_caps * dt_s[receiver]))
    donor_capacity = float(np.sum(removal_caps * dt_s[donor]))
    moved = min(requested, receiver_capacity, donor_capacity)
    if moved <= 0.0:
        return MirrorAllocation(
            zero,
            zero.copy(),
            false,
            false.copy(),
            donor_available,
            requested,
            receiver_capacity,
            0.0,
            requested > 0.0,
        )

    donor_phase = elapsed_s[donor] - elapsed_s[donor[0]]
    receiver_phase = elapsed_s[receiver] - elapsed_s[receiver[0]]
    donor_phase = (
        donor_phase / donor_phase[-1]
        if len(donor_phase) > 1 and donor_phase[-1] > 0.0
        else np.linspace(0.0, 1.0, len(donor))
    )
    receiver_phase = (
        receiver_phase / receiver_phase[-1]
        if len(receiver_phase) > 1 and receiver_phase[-1] > 0.0
        else np.linspace(0.0, 1.0, len(receiver))
    )
    # Receiver start gets the late donor tail; points near the peak get the
    # early, high post-peak excess.  A tiny positive weight lets water-filling
    # use remaining receiver capacity when the mirrored tail reaches zero.
    mirror_weights = np.maximum(
        np.interp(1.0 - receiver_phase, donor_phase, removal_caps), 1e-12
    )
    receiver_added = _bounded_weighted_allocation(
        weights=mirror_weights,
        caps=receiver_caps,
        dt_s=dt_s[receiver],
        target_area=moved,
    )
    donor_removed = _bounded_weighted_allocation(
        weights=removal_caps,
        caps=removal_caps,
        dt_s=dt_s[donor],
        target_area=moved,
    )
    added = zero.copy()
    removed = zero.copy()
    added[receiver] = receiver_added
    removed[donor] = donor_removed
    receiver_mask = added > 0.0
    donor_mask = removed > 0.0
    return MirrorAllocation(
        added=added,
        removed=removed,
        receiver_mask=receiver_mask,
        donor_mask=donor_mask,
        donor_available_area_bpm_s=donor_available,
        requested_area_bpm_s=requested,
        receiver_capacity_bpm_s=receiver_capacity,
        moved_area_bpm_s=moved,
        capacity_limited=moved < requested - 1e-9,
    )


def shift_mirror_wave_areas(
    *,
    clean_hr: np.ndarray,
    elapsed_s: np.ndarray,
    dt_s: np.ndarray,
    waves: tuple[DetectedWave, ...],
    athlete_profile: AthleteHRProfile,
    config: HRmodConfig,
) -> WaveAreaShiftResult:
    """Apply the v4 HR-only eligibility and mirror redistribution."""

    clean_hr = np.asarray(clean_hr, dtype=float)
    elapsed_s = np.asarray(elapsed_s, dtype=float)
    dt_s = np.asarray(dt_s, dtype=float)
    count = len(clean_hr)
    added = np.zeros((count,), dtype=float)
    removed = np.zeros((count,), dtype=float)
    receiver_mask = np.zeros((count,), dtype=bool)
    donor_mask = np.zeros((count,), dtype=bool)
    results: list[WaveAreaShift] = []
    tolerance = config.area_conservation_tolerance_bpm_s

    for wave in waves:
        receiver = np.arange(wave.rise_start_index, wave.peak_index + 1, dtype=int)
        donor = np.arange(wave.peak_index + 1, wave.tail_end_index + 1, dtype=int)
        floor = (
            max(float(wave.baseline_hr_bpm), athlete_profile.hr_floor_bpm)
            if wave.baseline_hr_bpm is not None
            else None
        )
        duration = float(
            elapsed_s[wave.tail_end_index] - elapsed_s[wave.rise_start_index]
        )
        peak = float(np.nanmax(clean_hr[wave.rise_start_index : wave.tail_end_index + 1]))
        skip_reason: str | None = None
        flags: set[str] = {"V4_MIRROR_MODEL"}
        if not wave.complete:
            skip_reason = wave.incomplete_reason or "incomplete_wave"
            flags.add("INCOMPLETE_WAVE")
            if wave.end_reason == "long_gap":
                flags.add("LONG_GAP")
            if skip_reason == "insufficient_baseline_history":
                flags.add("INCOMPLETE_WAVE_START")
            if wave.end_reason == "end_of_file":
                flags.add("INCOMPLETE_WAVE_END")
        elif duration > config.mirror_max_wave_duration_s:
            skip_reason = "mirror_wave_duration_above_limit"
        elif peak < config.mirror_min_peak_fraction_hrmax * athlete_profile.hrmax_bpm:
            skip_reason = "mirror_peak_below_hrmax_fraction"
        elif float(np.sum(dt_s[receiver])) < config.min_receiver_duration_s:
            skip_reason = "receiver_too_short"
        elif float(np.sum(dt_s[donor])) < config.min_donor_duration_s:
            skip_reason = "donor_too_short"
        elif config.alpha == 0.0:
            skip_reason = "alpha_zero"
        elif floor is None:
            skip_reason = "missing_baseline"

        allocation = allocate_mirror_wave(
            clean_hr=clean_hr,
            elapsed_s=elapsed_s,
            dt_s=dt_s,
            receiver_indices=receiver,
            donor_indices=donor,
            donor_floor_bpm=floor or athlete_profile.hr_floor_bpm,
            hrmax_bpm=athlete_profile.hrmax_bpm,
            alpha=config.alpha,
            max_addition_bpm=config.max_addition_bpm,
            max_removal_bpm=config.max_removal_bpm,
        )
        if skip_reason is None and allocation.donor_available_area_bpm_s <= 0.0:
            skip_reason = "no_donor_area"
        if skip_reason is None and allocation.receiver_capacity_bpm_s <= 0.0:
            skip_reason = "no_receiver_capacity"
        if skip_reason is not None:
            wave_added = np.zeros((count,), dtype=float)
            wave_removed = np.zeros((count,), dtype=float)
            moved = 0.0
        else:
            wave_added = allocation.added
            wave_removed = allocation.removed
            moved = allocation.moved_area_bpm_s
            added += wave_added
            removed += wave_removed
            receiver_mask |= allocation.receiver_mask
            donor_mask |= allocation.donor_mask
            flags.add("MIRRORED_POST_PEAK_AREA")
            flags.add("AREA_CONSERVATION_PASSED")
            if allocation.capacity_limited:
                flags.add("CAPACITY_LIMITED")

        added_area = float(np.sum(wave_added * dt_s))
        removed_area = float(np.sum(wave_removed * dt_s))
        error = added_area - removed_area
        if abs(error) > tolerance:
            raise RuntimeError("mirror wave area conservation failed")
        results.append(
            WaveAreaShift(
                wave=wave,
                corrected=moved > 0.0,
                donor_floor_bpm=floor,
                receiver_duration_s=float(np.sum(dt_s[allocation.receiver_mask])),
                donor_duration_s=float(np.sum(dt_s[allocation.donor_mask])),
                donor_available_area_bpm_s=allocation.donor_available_area_bpm_s,
                requested_area_bpm_s=allocation.requested_area_bpm_s,
                receiver_capacity_bpm_s=allocation.receiver_capacity_bpm_s,
                moved_area_bpm_s=moved,
                added_area_bpm_s=added_area,
                removed_area_bpm_s=removed_area,
                area_balance_error_bpm_s=error,
                capacity_limited_area_bpm_s=(
                    max(0.0, allocation.requested_area_bpm_s - moved)
                    if allocation.capacity_limited and skip_reason is None
                    else 0.0
                ),
                capacity_limited=allocation.capacity_limited and skip_reason is None,
                skip_reason=skip_reason,
                flags=tuple(sorted(flags)),
            )
        )

    if np.any(receiver_mask & donor_mask):
        raise RuntimeError("mirror receiver and donor masks overlap")
    hrmod = clean_hr + added - removed
    finite = np.isfinite(hrmod)
    if np.any(hrmod[finite] > athlete_profile.hrmax_bpm + 1e-9):
        raise RuntimeError("mirror addition exceeded HRmax")
    if np.any(hrmod[finite] < athlete_profile.hr_floor_bpm - 1e-9):
        raise RuntimeError("mirror removal fell below HR floor")
    return WaveAreaShiftResult(
        hrmod=hrmod,
        added_bpm=added,
        removed_bpm=removed,
        receiver_mask=receiver_mask,
        donor_mask=donor_mask,
        wave_results=tuple(results),
    )


__all__ = [
    "MirrorAllocation",
    "WaveAreaShift",
    "WaveAreaShiftResult",
    "allocate_mirror_wave",
    "shift_mirror_wave_areas",
]

