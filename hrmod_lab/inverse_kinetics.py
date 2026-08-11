"""Robust offline first-order inverse HR kinetics.

The implementation uses short HR-only look-ahead and therefore is explicitly
offline.  Local linear fits operate on real timestamps and never cross a signal
segment boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .schemas import HRmodConfig


@dataclass(frozen=True, slots=True)
class InverseKineticsResult:
    smoothed_hr: np.ndarray
    derivative_bpm_per_s: np.ndarray
    lookahead_hr: np.ndarray
    lookahead_derivative_bpm_per_s: np.ndarray
    provisional_demand: np.ndarray
    raw_correction: np.ndarray
    derivative_supported_mask: np.ndarray
    edge_affected_mask: np.ndarray


def _weighted_local_line(
    time_s: np.ndarray,
    values: np.ndarray,
    center_s: float,
    config: HRmodConfig,
) -> tuple[float, float, bool]:
    """Return local fitted value, slope, and whether derivative support exists."""

    size = len(time_s)
    if size == 0:
        return np.nan, np.nan, False
    half_window = config.smoothing_window_s / 2.0
    left = int(np.searchsorted(time_s, center_s - half_window, side="left"))
    right = int(np.searchsorted(time_s, center_s + half_window, side="right"))
    required = min(config.smoothing_min_points, size)
    while right - left < required:
        can_left = left > 0
        can_right = right < size
        if not can_left and not can_right:
            break
        left_distance = center_s - time_s[left - 1] if can_left else np.inf
        right_distance = time_s[right] - center_s if can_right else np.inf
        if left_distance <= right_distance:
            left -= 1
        else:
            right += 1

    local_t = time_s[left:right] - center_s
    local_y = values[left:right]
    finite = np.isfinite(local_t) & np.isfinite(local_y)
    local_t = local_t[finite]
    local_y = local_y[finite]
    if len(local_y) == 0:
        return np.nan, np.nan, False
    if len(local_y) == 1:
        return float(local_y[0]), np.nan, False

    max_distance = max(float(np.max(np.abs(local_t))), half_window, 1e-12)
    scaled = np.minimum(np.abs(local_t) / max_distance, 1.0)
    kernel = np.square(np.square(1.0 - scaled**3))
    kernel = np.maximum(kernel, 1e-12)
    robust = np.ones_like(kernel)
    fit_value = float(np.median(local_y))
    slope = np.nan

    iterations = (
        config.smoothing_robust_iterations
        if config.smoothing_method == "robust_local_linear"
        else 0
    )
    for iteration in range(iterations + 1):
        weights = kernel * robust
        s0 = float(np.sum(weights))
        s1 = float(np.sum(weights * local_t))
        s2 = float(np.sum(weights * local_t * local_t))
        t0 = float(np.sum(weights * local_y))
        t1 = float(np.sum(weights * local_t * local_y))
        determinant = s0 * s2 - s1 * s1
        if s0 <= 0.0:
            return np.nan, np.nan, False
        if determinant <= np.finfo(float).eps * max(s0 * s2, 1.0):
            fit_value = t0 / s0
            slope = np.nan
            break
        fit_value = (t0 * s2 - t1 * s1) / determinant
        slope = (t1 * s0 - t0 * s1) / determinant
        if iteration == iterations:
            break
        residuals = local_y - (fit_value + slope * local_t)
        median_residual = float(np.median(residuals))
        mad = float(np.median(np.abs(residuals - median_residual)))
        if mad <= 1e-12:
            break
        scaled_residual = np.abs(residuals - median_residual) / (6.0 * 1.4826 * mad)
        robust = np.square(1.0 - np.minimum(scaled_residual, 1.0) ** 2)
        robust[scaled_residual >= 1.0] = 0.0

    support_span = float(np.max(local_t) - np.min(local_t))
    supported = (
        len(local_y) >= config.smoothing_min_points
        and support_span > 0.0
        and np.isfinite(slope)
    )
    return float(fit_value), float(slope), bool(supported)


def compute_inverse_kinetics(
    *,
    elapsed_s: np.ndarray,
    clean_hr: np.ndarray,
    segment_ids: np.ndarray,
    config: HRmodConfig,
) -> InverseKineticsResult:
    """Compute smoothed HR, look-ahead derivative, demand, and raw correction."""

    elapsed_s = np.asarray(elapsed_s, dtype=float)
    clean_hr = np.asarray(clean_hr, dtype=float)
    segment_ids = np.asarray(segment_ids, dtype=int)
    if not (len(elapsed_s) == len(clean_hr) == len(segment_ids)):
        raise ValueError("inverse kinetics inputs must have equal lengths")

    count = len(clean_hr)
    smoothed = np.full(count, np.nan, dtype=float)
    derivative = np.full(count, np.nan, dtype=float)
    lookahead = np.full(count, np.nan, dtype=float)
    lookahead_derivative = np.full(count, np.nan, dtype=float)
    provisional = np.full(count, np.nan, dtype=float)
    raw_correction = np.full(count, np.nan, dtype=float)
    supported = np.zeros(count, dtype=bool)
    edge_affected = np.zeros(count, dtype=bool)

    valid_segment_ids = sorted({int(value) for value in segment_ids if value >= 0})
    for segment_id in valid_segment_ids:
        indices = np.flatnonzero(segment_ids == segment_id)
        indices = indices[np.isfinite(clean_hr[indices])]
        if len(indices) == 0:
            continue
        segment_t = elapsed_s[indices]
        segment_hr = clean_hr[indices]

        for global_index in indices:
            value, slope, has_support = _weighted_local_line(
                segment_t, segment_hr, elapsed_s[global_index], config
            )
            smoothed[global_index] = value
            derivative[global_index] = slope
            if not has_support:
                edge_affected[global_index] = True

        segment_start = float(segment_t[0])
        segment_end = float(segment_t[-1])
        for global_index in indices:
            target_s = float(elapsed_s[global_index] + config.delay_s)
            if target_s < segment_start or target_s > segment_end:
                edge_affected[global_index] = True
                continue
            value, slope, has_support = _weighted_local_line(
                segment_t, segment_hr, target_s, config
            )
            if not has_support or not np.isfinite(value) or not np.isfinite(slope):
                edge_affected[global_index] = True
                continue
            lookahead[global_index] = value
            lookahead_derivative[global_index] = slope
            tau = config.tau_on_s if slope >= 0.0 else config.tau_off_s
            provisional[global_index] = value + tau * slope
            if np.isfinite(smoothed[global_index]):
                raw_correction[global_index] = (
                    provisional[global_index] - smoothed[global_index]
                )
                supported[global_index] = True

    return InverseKineticsResult(
        smoothed_hr=smoothed,
        derivative_bpm_per_s=derivative,
        lookahead_hr=lookahead,
        lookahead_derivative_bpm_per_s=lookahead_derivative,
        provisional_demand=provisional,
        raw_correction=raw_correction,
        derivative_supported_mask=supported,
        edge_affected_mask=edge_affected,
    )


__all__ = ["InverseKineticsResult", "compute_inverse_kinetics"]
