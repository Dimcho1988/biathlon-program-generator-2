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
    receiver_mask: np.ndarray
    donor_mask: np.ndarray
    wave_results: tuple[WaveAreaShift, ...]


@dataclass(frozen=True, slots=True)
class _BranchAllocation:
    added: np.ndarray
    removed: np.ndarray
    donor_available: float
    requested: float
    receiver_capacity: float
    moved: float
    effective_donor_capacity: float
    effective_receiver_capacity: float


def _allocate_branch(
    *,
    clean_hr: np.ndarray,
    dt_s: np.ndarray,
    receiver_indices: np.ndarray,
    donor_indices: np.ndarray,
    receiver_shape: np.ndarray,
    donor_shape: np.ndarray,
    config: HRmodConfig,
) -> _BranchAllocation:
    """Allocate one independently valid and exactly conserved branch."""

    donor_available = float(np.sum(donor_shape[donor_indices] * dt_s[donor_indices]))
    requested = float(config.alpha * donor_available)
    receiver_capacity = float(
        np.sum(receiver_shape[receiver_indices] * dt_s[receiver_indices])
    )
    donor_allocation_shape = donor_shape.copy()
    receiver_allocation_shape = receiver_shape.copy()
    if config.max_removal_bpm is not None:
        donor_allocation_shape[donor_indices] = np.minimum(
            donor_allocation_shape[donor_indices], config.max_removal_bpm
        )
    if config.max_addition_bpm is not None:
        receiver_allocation_shape[receiver_indices] = np.minimum(
            receiver_allocation_shape[receiver_indices], config.max_addition_bpm
        )
    effective_donor = float(
        np.sum(donor_allocation_shape[donor_indices] * dt_s[donor_indices])
    )
    effective_receiver = float(
        np.sum(receiver_allocation_shape[receiver_indices] * dt_s[receiver_indices])
    )
    moved = min(requested, effective_donor, effective_receiver)
    if moved <= 0.0:
        moved = 0.0
    added = _proportional_allocation(
        shape=receiver_allocation_shape, dt_s=dt_s, target_area=moved
    )
    removed = _proportional_allocation(
        shape=donor_allocation_shape, dt_s=dt_s, target_area=moved
    )
    return _BranchAllocation(
        added=added,
        removed=removed,
        donor_available=donor_available,
        requested=requested,
        receiver_capacity=receiver_capacity,
        moved=moved,
        effective_donor_capacity=effective_donor,
        effective_receiver_capacity=effective_receiver,
    )


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
    """Move HR area with exact-v2 compact and conservative v3 long paths.

    In the 30--45 s transition band, the final delta is a convex combination
    of two independently bounded, area-conserving deltas.  This avoids a hard
    duration cliff and also avoids receiver/donor overlap after cancellation.
    """

    clean_hr = np.asarray(clean_hr, dtype=float)
    dt_s = np.asarray(dt_s, dtype=float)
    if len(clean_hr) != len(dt_s):
        raise ValueError("area-shift inputs must have equal lengths")
    if np.any(dt_s < 0.0):
        raise ValueError("dt_s must be non-negative")

    count = len(clean_hr)
    added = np.zeros(count, dtype=float)
    removed = np.zeros(count, dtype=float)
    receiver_mask = np.zeros(count, dtype=bool)
    donor_mask = np.zeros(count, dtype=bool)
    occupied = np.zeros(count, dtype=bool)
    results: list[WaveAreaShift] = []
    tolerance = config.area_conservation_tolerance_bpm_s

    for wave in waves:
        legacy_receiver = np.arange(wave.rise_start_index, wave.peak_index + 1, dtype=int)
        legacy_donor = np.arange(wave.peak_index + 1, wave.tail_end_index + 1, dtype=int)
        full_window = np.arange(wave.rise_start_index, wave.tail_end_index + 1, dtype=int)
        if np.any(occupied[full_window]):
            raise RuntimeError("detected wave windows overlap")
        occupied[full_window] = True

        donor_floor = (
            max(float(wave.baseline_hr_bpm), athlete_profile.hr_floor_bpm)
            if wave.baseline_hr_bpm is not None
            else None
        )
        legacy_receiver_shape = np.zeros(count, dtype=float)
        legacy_donor_shape = np.zeros(count, dtype=float)
        legacy_receiver_shape[legacy_receiver] = np.maximum(
            0.0, athlete_profile.hrmax_bpm - clean_hr[legacy_receiver]
        )
        if donor_floor is not None:
            legacy_donor_shape[legacy_donor] = np.maximum(
                0.0, clean_hr[legacy_donor] - donor_floor
            )
        legacy = _allocate_branch(
            clean_hr=clean_hr,
            dt_s=dt_s,
            receiver_indices=legacy_receiver,
            donor_indices=legacy_donor,
            receiver_shape=legacy_receiver_shape,
            donor_shape=legacy_donor_shape,
            config=config,
        )

        legacy_receiver_duration = float(np.sum(dt_s[legacy_receiver]))
        legacy_donor_duration = float(np.sum(dt_s[legacy_donor]))
        flags: set[str] = set()
        if config.max_removal_bpm is not None:
            flags.add("PER_SAMPLE_REMOVAL_LIMIT_ENABLED")
        if config.max_addition_bpm is not None:
            flags.add("PER_SAMPLE_ADDITION_LIMIT_ENABLED")

        skip_reason: str | None = None
        if not wave.complete:
            skip_reason = wave.incomplete_reason or "incomplete_wave"
            flags.add("INCOMPLETE_WAVE")
            if wave.incomplete_reason == "insufficient_baseline_history":
                flags.add("INCOMPLETE_WAVE_START")
            else:
                flags.add("INCOMPLETE_WAVE_END")
            if wave.end_reason == "long_gap":
                flags.add("LONG_GAP")
        elif legacy_receiver_duration < config.min_receiver_duration_s:
            skip_reason = "receiver_too_short"
        elif legacy_donor_duration < config.min_donor_duration_s:
            skip_reason = "donor_too_short"
        elif config.alpha == 0.0:
            skip_reason = "alpha_zero"
        elif legacy.donor_available <= 0.0:
            skip_reason = "no_donor_area"
        elif legacy.receiver_capacity <= 0.0:
            skip_reason = "no_receiver_capacity"

        weight = float(wave.transition_weight)
        if weight == 0.0:
            # Legacy flags describe the nominal windows, including skipped
            # waves; this preserves the complete v2 timeseries contract.
            receiver_mask[legacy_receiver] = True
            donor_mask[legacy_donor] = True
        final_delta = np.zeros(count, dtype=float)
        donor_available = legacy.donor_available
        requested = legacy.requested
        capacity = legacy.receiver_capacity
        branch_moved = legacy.moved
        effective_donor = legacy.effective_donor_capacity
        effective_receiver = legacy.effective_receiver_capacity
        receiver_duration = legacy_receiver_duration
        donor_duration = legacy_donor_duration

        if skip_reason is None and weight == 0.0:
            # Intentionally no blending or extra arithmetic here: compact v3
            # and v2_legacy use the exact legacy arrays and allocation path.
            wave_added = legacy.added
            wave_removed = legacy.removed
        elif skip_reason is None and wave.correction_strategy in {
            "v3_transition",
            "v3_terminal_fall",
        }:
            assert wave.terminal_fall_start_index is not None
            assert wave.terminal_fall_end_index is not None
            assert wave.hold_target_hr_bpm is not None
            v3_receiver = np.arange(
                wave.rise_start_index, wave.terminal_fall_start_index, dtype=int
            )
            v3_donor = np.arange(
                wave.terminal_fall_start_index,
                wave.terminal_fall_end_index + 1,
                dtype=int,
            )
            v3_receiver_shape = np.zeros(count, dtype=float)
            v3_donor_shape = np.zeros(count, dtype=float)
            v3_receiver_shape[v3_receiver] = np.maximum(
                0.0, wave.hold_target_hr_bpm - clean_hr[v3_receiver]
            )
            sharp_taper = np.asarray(wave.terminal_fall_weights, dtype=float)
            if len(sharp_taper) == len(v3_donor) and donor_floor is not None:
                v3_donor_shape[v3_donor] = np.maximum(
                    0.0, clean_hr[v3_donor] - donor_floor
                ) * sharp_taper
            v3 = _allocate_branch(
                clean_hr=clean_hr,
                dt_s=dt_s,
                receiver_indices=v3_receiver,
                donor_indices=v3_donor,
                receiver_shape=v3_receiver_shape,
                donor_shape=v3_donor_shape,
                config=config,
            )
            v3_duration_ok = bool(
                float(np.sum(dt_s[v3_receiver])) >= config.min_receiver_duration_s
                and float(np.sum(dt_s[v3_donor])) >= config.min_donor_duration_s
            )
            if not v3_duration_ok or v3.moved <= 0.0:
                # A purported long morphology that cannot support its own
                # conservative allocation fails closed at the v3 endpoint.
                v3_delta = np.zeros(count, dtype=float)
                flags.add("V3_BRANCH_INELIGIBLE")
            else:
                v3_delta = v3.added - v3.removed
            legacy_delta = legacy.added - legacy.removed
            final_delta = (1.0 - weight) * legacy_delta + weight * v3_delta
            wave_added = np.maximum(final_delta, 0.0)
            wave_removed = np.maximum(-final_delta, 0.0)
            receiver_mask |= wave_added > 0.0
            donor_mask |= wave_removed > 0.0
            donor_available = (
                (1.0 - weight) * legacy.donor_available
                + weight * v3.donor_available
            )
            requested = (1.0 - weight) * legacy.requested + weight * v3.requested
            capacity = (
                (1.0 - weight) * legacy.receiver_capacity
                + weight * v3.receiver_capacity
            )
            effective_donor = (
                (1.0 - weight) * legacy.effective_donor_capacity
                + weight * v3.effective_donor_capacity
            )
            effective_receiver = (
                (1.0 - weight) * legacy.effective_receiver_capacity
                + weight * v3.effective_receiver_capacity
            )
            receiver_duration = float(np.sum(dt_s[wave_added > 0.0]))
            donor_duration = float(np.sum(dt_s[wave_removed > 0.0]))
            flags.add("V3_SUSTAINED_MORPHOLOGY")
            if weight < 1.0:
                flags.add("V3_TRANSITION_BLEND")
                gross_moved = (1.0 - weight) * legacy.moved + weight * v3.moved
                if float(np.sum(np.maximum(final_delta, 0.0) * dt_s)) < gross_moved - tolerance:
                    flags.add("TRANSITION_CANCELLATION")
        elif skip_reason is None and wave.correction_strategy == "v2_fade_out":
            final_delta = (1.0 - weight) * (legacy.added - legacy.removed)
            wave_added = np.maximum(final_delta, 0.0)
            wave_removed = np.maximum(-final_delta, 0.0)
            receiver_mask |= wave_added > 0.0
            donor_mask |= wave_removed > 0.0
            donor_available = (1.0 - weight) * legacy.donor_available
            requested = (1.0 - weight) * legacy.requested
            capacity = (1.0 - weight) * legacy.receiver_capacity
            effective_donor = (1.0 - weight) * legacy.effective_donor_capacity
            effective_receiver = (1.0 - weight) * legacy.effective_receiver_capacity
            receiver_duration = float(np.sum(dt_s[wave_added > 0.0]))
            donor_duration = float(np.sum(dt_s[wave_removed > 0.0]))
            flags.add("AMBIGUOUS_TRANSITION_FADE")
        else:
            wave_added = np.zeros(count, dtype=float)
            wave_removed = np.zeros(count, dtype=float)
            if skip_reason is None:
                skip_reason = "ambiguous_long_wave"
                flags.add("AMBIGUOUS_LONG_WAVE")

        added_area = float(np.sum(wave_added * dt_s))
        removed_area = float(np.sum(wave_removed * dt_s))
        moved = added_area
        # Preserve the legacy allocation target exactly.  The proportional
        # arrays can accumulate a final-bit summation difference, but v2
        # reported the pre-allocation target rather than the recomputed sum.
        branch_moved = (
            legacy.moved
            if skip_reason is None and weight == 0.0
            else moved
        )
        balance_error = added_area - removed_area
        if moved <= 0.0 and skip_reason is None:
            skip_reason = "zero_moved_area"
        capacity_evaluated = bool(
            skip_reason in {None, "no_receiver_capacity"}
            and "V3_BRANCH_INELIGIBLE" not in flags
            and "TRANSITION_CANCELLATION" not in flags
        )
        capacity_limited = bool(
            capacity_evaluated
            and requested > 0.0
            and branch_moved < requested - tolerance
        )
        capacity_limited_area = (
            max(0.0, requested - branch_moved) if capacity_limited else 0.0
        )
        if capacity_limited:
            flags.add("CAPACITY_LIMITED")

        if np.any((wave_added > 0.0) & (wave_removed > 0.0)):
            raise RuntimeError("effective receiver and donor supports overlap")
        if donor_floor is not None and np.any(
            clean_hr[wave_removed > 0.0] - wave_removed[wave_removed > 0.0]
            < donor_floor - 1e-9
        ):
            raise RuntimeError("wave removal exceeded excess above donor floor")
        ceiling = (
            float(wave.hold_target_hr_bpm)
            if weight == 1.0
            and wave.correction_strategy == "v3_terminal_fall"
            and wave.hold_target_hr_bpm is not None
            else athlete_profile.hrmax_bpm
        )
        if np.any(
            clean_hr[wave_added > 0.0] + wave_added[wave_added > 0.0]
            > ceiling + 1e-9
        ):
            raise RuntimeError("wave addition exceeded receiver ceiling")
        if abs(balance_error) > tolerance:
            raise RuntimeError(
                "wave area conservation failed: "
                f"wave={wave.wave_id}, error={balance_error:.12g} bpm*s"
            )
        if moved > 0.0:
            flags.add("AREA_CONSERVATION_PASSED")
            added += wave_added
            removed += wave_removed

        results.append(
            WaveAreaShift(
                wave=wave,
                corrected=moved > 0.0,
                donor_floor_bpm=donor_floor,
                receiver_duration_s=receiver_duration,
                donor_duration_s=donor_duration,
                donor_available_area_bpm_s=donor_available,
                requested_area_bpm_s=requested,
                receiver_capacity_bpm_s=capacity,
                moved_area_bpm_s=branch_moved,
                added_area_bpm_s=added_area,
                removed_area_bpm_s=removed_area,
                area_balance_error_bpm_s=balance_error,
                capacity_limited_area_bpm_s=capacity_limited_area,
                capacity_limited=capacity_limited,
                skip_reason=skip_reason,
                flags=tuple(sorted(flags)),
            )
        )

    if np.any(receiver_mask & donor_mask):
        raise RuntimeError("effective receiver and donor masks overlap")
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
        receiver_mask=receiver_mask,
        donor_mask=donor_mask,
        wave_results=tuple(results),
    )


__all__ = ["WaveAreaShift", "WaveAreaShiftResult", "shift_wave_areas"]
