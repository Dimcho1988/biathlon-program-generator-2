"""Deterministic HR-only rise--peak--fall wave detection.

Detection uses a lightly smoothed, robust local-linear signal solely to locate
wave boundaries.  Measured ``clean_hr`` remains the signal redistributed by the
model.  Every operation is time-based and constrained to one continuous HR
segment, so neither trend nor wave state can bridge a long data gap.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from .schemas import HRmodConfig


@dataclass(frozen=True, slots=True)
class DetectedWave:
    wave_id: int
    segment_id: int
    rise_start_index: int
    peak_index: int
    tail_end_index: int
    baseline_hr_bpm: float | None
    complete: bool
    end_reason: str
    incomplete_reason: str | None = None
    confirmed_fall_start_index: int | None = None
    morphology: str = "compact"
    morphology_reason: str | None = None
    correction_strategy: str = "v2_full_tail"
    hold_start_index: int | None = None
    hold_end_index: int | None = None
    terminal_fall_start_index: int | None = None
    terminal_fall_end_index: int | None = None
    hold_target_hr_bpm: float | None = None
    transition_weight: float = 0.0
    terminal_fall_weights: tuple[float, ...] = ()

    @property
    def receiver_indices(self) -> range:
        if self.correction_strategy == "v3_terminal_fall":
            assert self.terminal_fall_start_index is not None
            return range(self.rise_start_index, self.terminal_fall_start_index)
        if self.correction_strategy == "none":
            return range(0, 0)
        return range(self.rise_start_index, self.peak_index + 1)

    @property
    def donor_indices(self) -> range:
        if self.correction_strategy == "v3_terminal_fall":
            assert self.terminal_fall_start_index is not None
            assert self.terminal_fall_end_index is not None
            return range(
                self.terminal_fall_start_index,
                self.terminal_fall_end_index + 1,
            )
        if self.correction_strategy == "none":
            return range(0, 0)
        return range(self.peak_index + 1, self.tail_end_index + 1)


@dataclass(frozen=True, slots=True)
class WaveDetectionResult:
    h_detect: np.ndarray
    trend_bpm_per_s: np.ndarray
    trend_supported_mask: np.ndarray
    edge_affected_mask: np.ndarray
    waves: tuple[DetectedWave, ...]
    wave_ids: np.ndarray
    wave_states: tuple[str, ...]
    local_baseline_hr: np.ndarray
    receiver_mask: np.ndarray
    donor_mask: np.ndarray


def _transition_weight(duration_s: float, config: HRmodConfig) -> float:
    """Continuous v2→v3 weight with exact v2 behavior at the lower bound."""

    if duration_s <= config.compact_full_v2_s:
        return 0.0
    if duration_s >= config.sustained_full_v3_s:
        return 1.0
    return float(
        (duration_s - config.compact_full_v2_s)
        / (config.sustained_full_v3_s - config.compact_full_v2_s)
    )


def _terminal_fall_candidates(
    *,
    wave: DetectedWave,
    elapsed_s: np.ndarray,
    h_detect: np.ndarray,
    trend: np.ndarray,
    supported: np.ndarray,
    config: HRmodConfig,
) -> tuple[tuple[int, int], ...]:
    """Return non-overlapping, hysteresis-closed sharp fall intervals."""

    candidates: list[tuple[int, int]] = []
    index = wave.rise_start_index + 1
    stop = wave.tail_end_index
    while index <= stop:
        enters = bool(
            supported[index]
            and np.isfinite(trend[index])
            and trend[index] <= -config.terminal_fall_threshold_bpm_s
        )
        if not enters:
            index += 1
            continue
        start = index
        confirmed_at: int | None = None
        release_start: int | None = None
        cursor = index
        while cursor <= stop:
            duration = float(elapsed_s[cursor] - elapsed_s[start])
            drop = float(h_detect[start] - h_detect[cursor])
            if (
                duration >= config.terminal_fall_min_duration_s
                and drop >= config.terminal_fall_min_drop_bpm
            ):
                confirmed_at = cursor
            released = bool(
                supported[cursor]
                and np.isfinite(trend[cursor])
                and trend[cursor]
                >= -config.terminal_fall_release_slope_bpm_s
            )
            if confirmed_at is not None and released:
                if release_start is None:
                    release_start = cursor
                if (
                    elapsed_s[cursor] - elapsed_s[release_start]
                    >= config.terminal_fall_release_s
                ):
                    break
            else:
                release_start = None
            if duration > config.terminal_fall_max_duration_s:
                break
            cursor += 1

        if confirmed_at is not None:
            end = (release_start - 1) if release_start is not None else min(cursor, stop)
            end = max(confirmed_at, end)
            duration = float(elapsed_s[end] - elapsed_s[start])
            if duration <= config.terminal_fall_max_duration_s:
                candidates.append((start, end))
            index = max(end + 1, start + 1)
        else:
            index = start + 1
    return tuple(candidates)


def _hold_before_terminal_fall(
    *,
    fall_start: int,
    wave: DetectedWave,
    elapsed_s: np.ndarray,
    h_detect: np.ndarray,
    clean_hr: np.ndarray,
    trend: np.ndarray,
    supported: np.ndarray,
    config: HRmodConfig,
) -> tuple[int, int, float, float] | None:
    """Return hold bounds plus separate detection and allocation targets."""

    end = fall_start - 1
    if end <= wave.rise_start_index:
        return None
    earliest = int(
        np.searchsorted(
            elapsed_s,
            elapsed_s[end] - config.min_hold_duration_s,
            side="left",
        )
    )
    earliest = max(earliest, wave.rise_start_index + 1)
    if elapsed_s[end] - elapsed_s[earliest] < config.min_hold_duration_s:
        return None
    window = np.arange(earliest, end + 1, dtype=int)
    valid = supported[window] & np.isfinite(h_detect[window]) & np.isfinite(trend[window])
    if not np.all(valid):
        return None
    detect_target = float(np.median(h_detect[window]))
    if np.max(np.abs(h_detect[window] - detect_target)) > config.hold_band_bpm:
        return None
    window_duration = float(elapsed_s[end] - elapsed_s[earliest])
    net_slope = (
        float(h_detect[end] - h_detect[earliest]) / window_duration
        if window_duration > 0.0
        else np.inf
    )
    if abs(net_slope) > config.hold_slope_tolerance_bpm_s:
        return None
    if float(np.median(np.abs(trend[window]))) > config.hold_slope_tolerance_bpm_s:
        return None

    # Extend the marker backwards while the same robust hold remains valid.
    start = earliest
    while start > wave.rise_start_index + 1:
        previous = start - 1
        if not supported[previous] or not np.isfinite(trend[previous]):
            break
        if abs(h_detect[previous] - detect_target) > config.hold_band_bpm:
            break
        if abs(trend[previous]) > config.hold_slope_tolerance_bpm_s:
            break
        start = previous
    detect_target = float(np.median(h_detect[start : end + 1]))
    allocation_target = float(np.median(clean_hr[start : end + 1]))
    return start, end, detect_target, allocation_target


def _new_rise_terminal_context_is_valid(
    *,
    wave: DetectedWave,
    next_wave: DetectedWave | None,
    fall_end: int,
    hold_detect_target: float,
    elapsed_s: np.ndarray,
    h_detect: np.ndarray,
    supported: np.ndarray,
    config: HRmodConfig,
) -> bool:
    """Distinguish a terminal fall from a transient dip before a new rise.

    ``new_rise_trough`` backdates the current wave end to the trough and puts
    the confirming rise in the next wave.  A blanket rejection would therefore
    discard legitimate repeated sustained work.  Instead, require an explicit
    low-HR recovery dwell after the sharp fall and reject a rapid rebound to the
    prior hold level.  An immediate next rise remains ambiguous by design.
    """

    if wave.end_reason != "new_rise_trough":
        return True
    if (
        next_wave is None
        or next_wave.segment_id != wave.segment_id
        or next_wave.rise_start_index <= fall_end
    ):
        return False

    next_start = next_wave.rise_start_index
    recovery_duration = float(elapsed_s[next_start] - elapsed_s[fall_end])
    if recovery_duration < config.terminal_recovery_min_s:
        return False

    low_ceiling = hold_detect_target - config.hold_band_bpm
    recovery_indices = np.arange(fall_end + 1, next_start + 1, dtype=int)
    if len(recovery_indices) == 0:
        return False
    recovery_valid = (
        supported[recovery_indices]
        & np.isfinite(h_detect[recovery_indices])
        & (h_detect[recovery_indices] < low_ceiling)
    )
    if not np.all(recovery_valid):
        return False

    rebound_stop = int(
        np.searchsorted(
            elapsed_s,
            elapsed_s[next_start] + config.terminal_rebound_guard_s,
            side="right",
        )
    )
    rebound_stop = min(rebound_stop, next_wave.peak_index + 1)
    rebound_indices = np.arange(next_start, rebound_stop, dtype=int)
    if len(rebound_indices) and np.any(
        supported[rebound_indices]
        & np.isfinite(h_detect[rebound_indices])
        & (h_detect[rebound_indices] >= low_ceiling)
    ):
        return False
    return True


def _apply_v3_morphology(
    *,
    waves: tuple[DetectedWave, ...],
    elapsed_s: np.ndarray,
    h_detect: np.ndarray,
    clean_hr: np.ndarray,
    trend: np.ndarray,
    supported: np.ndarray,
    config: HRmodConfig,
) -> tuple[DetectedWave, ...]:
    """Classify long HR waves without consulting any reference channel."""

    if config.model_variant == "v2_legacy":
        return waves
    classified: list[DetectedWave] = []
    for wave_index, wave in enumerate(waves):
        next_wave = (
            waves[wave_index + 1]
            if wave_index + 1 < len(waves)
            else None
        )
        falls = _terminal_fall_candidates(
            wave=wave,
            elapsed_s=elapsed_s,
            h_detect=h_detect,
            trend=trend,
            supported=supported,
            config=config,
        )
        eligible: list[tuple[int, int, int, int, float, float]] = []
        terminal_context_ambiguous = False
        for fall_start, fall_end in falls:
            hold = _hold_before_terminal_fall(
                fall_start=fall_start,
                wave=wave,
                elapsed_s=elapsed_s,
                h_detect=h_detect,
                clean_hr=clean_hr,
                trend=trend,
                supported=supported,
                config=config,
            )
            if hold is not None:
                (
                    hold_start,
                    hold_end,
                    detect_target,
                    allocation_target,
                ) = hold
                post = h_detect[fall_end + 1 : wave.tail_end_index + 1]
                trend_post = trend[fall_end + 1 : wave.tail_end_index + 1]
                returns_to_hold = bool(
                    len(post)
                    and np.any(
                        np.isfinite(post)
                        & (post >= detect_target - config.hold_band_bpm)
                    )
                )
                confirmed_rerise = False
                rise_start: int | None = None
                for relative, slope in enumerate(trend_post):
                    absolute = fall_end + 1 + relative
                    rises = bool(
                        np.isfinite(slope)
                        and supported[absolute]
                        and slope >= config.rise_threshold_bpm_s
                    )
                    if rises and rise_start is None:
                        rise_start = absolute
                    elif not rises:
                        rise_start = None
                    if rise_start is not None and (
                        elapsed_s[absolute] - elapsed_s[rise_start]
                        >= config.min_sustained_rise_s
                        and h_detect[absolute] - h_detect[rise_start]
                        >= config.min_rise_bpm
                    ):
                        confirmed_rerise = True
                        break
                context_is_valid = _new_rise_terminal_context_is_valid(
                    wave=wave,
                    next_wave=next_wave,
                    fall_end=fall_end,
                    hold_detect_target=detect_target,
                    elapsed_s=elapsed_s,
                    h_detect=h_detect,
                    supported=supported,
                    config=config,
                )
                if wave.end_reason == "new_rise_trough" and not context_is_valid:
                    terminal_context_ambiguous = True
                if returns_to_hold or confirmed_rerise or not context_is_valid:
                    continue
                eligible.append(
                    (
                        fall_start,
                        fall_end,
                        hold_start,
                        hold_end,
                        detect_target,
                        allocation_target,
                    )
                )

        boundary_index = (
            eligible[0][0]
            if len(eligible) == 1
            else wave.confirmed_fall_start_index
        )
        if boundary_index is None:
            classified.append(
                replace(
                    wave,
                    morphology="ambiguous",
                    morphology_reason=(
                        "ambiguous_terminal_vs_transient_dip"
                        if terminal_context_ambiguous
                        else "missing_confirmed_fall_boundary"
                    ),
                    correction_strategy="none",
                    transition_weight=1.0,
                )
            )
            continue
        receiver_phase_s = float(
            elapsed_s[boundary_index] - elapsed_s[wave.rise_start_index]
        )
        weight = _transition_weight(receiver_phase_s, config)
        if weight == 0.0:
            # Do not replace the object: the compact numerical path remains
            # byte-for-byte the legacy v2 path.
            classified.append(wave)
            continue
        if len(eligible) == 1:
            (
                fall_start,
                fall_end,
                hold_start,
                hold_end,
                _,
                allocation_target,
            ) = eligible[0]
            fall_trend = np.maximum(
                0.0,
                -trend[fall_start : fall_end + 1]
                - config.terminal_fall_release_slope_bpm_s,
            )
            count = len(fall_trend)
            taper = 0.5 - 0.5 * np.cos(
                2.0 * np.pi * (np.arange(count, dtype=float) + 1.0) / (count + 1.0)
            )
            sharp_taper = fall_trend * taper
            maximum = float(np.max(sharp_taper)) if count else 0.0
            weights = (
                tuple(float(value / maximum) for value in sharp_taper)
                if maximum > 0.0
                else ()
            )
            strategy = "v3_terminal_fall" if weight == 1.0 else "v3_transition"
            classified.append(
                replace(
                    wave,
                    morphology="sustained",
                    morphology_reason="confirmed_hold_and_terminal_fall",
                    correction_strategy=strategy,
                    hold_start_index=hold_start,
                    hold_end_index=hold_end,
                    terminal_fall_start_index=fall_start,
                    terminal_fall_end_index=fall_end,
                    hold_target_hr_bpm=allocation_target,
                    transition_weight=weight,
                    terminal_fall_weights=weights,
                )
            )
        else:
            classified.append(
                replace(
                    wave,
                    morphology="ambiguous",
                    morphology_reason=(
                        "ambiguous_terminal_vs_transient_dip"
                        if terminal_context_ambiguous
                        else "multiple_or_missing_terminal_fall_candidates"
                    ),
                    correction_strategy="none" if weight == 1.0 else "v2_fade_out",
                    transition_weight=weight,
                )
            )
    return tuple(classified)


def _weighted_local_line(
    time_s: np.ndarray,
    values: np.ndarray,
    center_s: float,
    config: HRmodConfig,
) -> tuple[float, float, bool]:
    """Return a robust local fitted value, slope, and support indicator."""

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
    scaled_distance = np.minimum(np.abs(local_t) / max_distance, 1.0)
    kernel = np.maximum(np.square(1.0 - scaled_distance**3) ** 2, 1e-12)
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
        y0 = float(np.sum(weights * local_y))
        y1 = float(np.sum(weights * local_t * local_y))
        determinant = s0 * s2 - s1 * s1
        if s0 <= 0.0:
            return np.nan, np.nan, False
        if determinant <= np.finfo(float).eps * max(s0 * s2, 1.0):
            fit_value = y0 / s0
            slope = np.nan
            break
        fit_value = (y0 * s2 - y1 * s1) / determinant
        slope = (y1 * s0 - y0 * s1) / determinant
        if iteration == iterations:
            break
        residuals = local_y - (fit_value + slope * local_t)
        residual_median = float(np.median(residuals))
        mad = float(np.median(np.abs(residuals - residual_median)))
        if mad <= 1e-12:
            break
        scaled_residual = np.abs(residuals - residual_median) / (
            6.0 * 1.4826 * mad
        )
        robust = np.square(1.0 - np.minimum(scaled_residual, 1.0) ** 2)
        robust[scaled_residual >= 1.0] = 0.0

    support_span = float(np.max(local_t) - np.min(local_t))
    supported = (
        len(local_y) >= config.smoothing_min_points
        and support_span > 0.0
        and np.isfinite(slope)
    )
    return float(fit_value), float(slope), bool(supported)


def _detection_signal(
    *,
    elapsed_s: np.ndarray,
    clean_hr: np.ndarray,
    segment_ids: np.ndarray,
    config: HRmodConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    count = len(clean_hr)
    h_detect = np.full(count, np.nan, dtype=float)
    trend = np.full(count, np.nan, dtype=float)
    supported = np.zeros(count, dtype=bool)
    edge_affected = np.zeros(count, dtype=bool)
    half_window = config.smoothing_window_s / 2.0

    valid_segment_ids = sorted({int(value) for value in segment_ids if value >= 0})
    for segment_id in valid_segment_ids:
        indices = np.flatnonzero(segment_ids == segment_id)
        indices = indices[np.isfinite(clean_hr[indices])]
        if len(indices) == 0:
            continue
        segment_t = elapsed_s[indices]
        segment_hr = clean_hr[indices]
        segment_start = float(segment_t[0])
        segment_end = float(segment_t[-1])
        for index in indices:
            value, slope, has_support = _weighted_local_line(
                segment_t, segment_hr, float(elapsed_s[index]), config
            )
            h_detect[index] = value
            trend[index] = slope
            supported[index] = has_support
            if (
                not has_support
                or elapsed_s[index] - segment_start < half_window
                or segment_end - elapsed_s[index] < half_window
            ):
                edge_affected[index] = True

    return h_detect, trend, supported, edge_affected


def _last_maximum_index(values: np.ndarray, start: int, end: int) -> int:
    selection = values[start : end + 1]
    maximum = float(np.nanmax(selection))
    relative = np.flatnonzero(np.isclose(selection, maximum, rtol=0.0, atol=1e-12))
    return start + int(relative[-1])


def _local_baseline(
    *,
    rise_start_index: int,
    segment_id: int,
    elapsed_s: np.ndarray,
    clean_hr: np.ndarray,
    segment_ids: np.ndarray,
    config: HRmodConfig,
) -> float | None:
    start_time = elapsed_s[rise_start_index] - config.baseline_lookback_s
    candidates = np.flatnonzero(
        (segment_ids == segment_id)
        & (np.arange(len(clean_hr)) < rise_start_index)
        & (elapsed_s >= start_time)
        & np.isfinite(clean_hr)
    )
    if len(candidates) < config.baseline_min_points:
        return None
    return float(np.median(clean_hr[candidates]))


def _trough_before_rise(
    h_detect: np.ndarray, peak_index: int, rise_start_index: int
) -> int:
    start = peak_index + 1
    end = rise_start_index - 1
    if end < start:
        return start
    selection = h_detect[start : end + 1]
    minimum = float(np.nanmin(selection))
    # The last equal minimum is nearest the new rise and avoids assigning a
    # neutral plateau to the donor window.
    relative = np.flatnonzero(np.isclose(selection, minimum, rtol=0.0, atol=1e-12))
    return start + int(relative[-1])


def detect_hr_waves(
    *,
    elapsed_s: np.ndarray,
    dt_s: np.ndarray,
    clean_hr: np.ndarray,
    segment_ids: np.ndarray,
    config: HRmodConfig,
) -> WaveDetectionResult:
    """Detect closed and incomplete waves with a deterministic state machine."""

    elapsed_s = np.asarray(elapsed_s, dtype=float)
    dt_s = np.asarray(dt_s, dtype=float)
    clean_hr = np.asarray(clean_hr, dtype=float)
    segment_ids = np.asarray(segment_ids, dtype=int)
    if not (len(elapsed_s) == len(dt_s) == len(clean_hr) == len(segment_ids)):
        raise ValueError("wave detection inputs must have equal lengths")
    if np.any(dt_s < 0.0):
        raise ValueError("dt_s must be non-negative")

    h_detect, trend, supported, edge_affected = _detection_signal(
        elapsed_s=elapsed_s,
        clean_hr=clean_hr,
        segment_ids=segment_ids,
        config=config,
    )
    waves: list[DetectedWave] = []
    next_wave_id = 1

    valid_segment_ids = sorted({int(value) for value in segment_ids if value >= 0})
    for segment_id in valid_segment_ids:
        segment = np.flatnonzero(
            (segment_ids == segment_id)
            & np.isfinite(clean_hr)
            & np.isfinite(h_detect)
        )
        if len(segment) == 0:
            continue
        segment_start = int(segment[0])
        segment_end = int(segment[-1])

        state = "seeking_rise"
        rise_candidate: int | None = None
        active_start: int | None = None
        active_baseline: float | None = None
        fall_candidate: int | None = None
        peak_index: int | None = None
        return_candidate: int | None = None
        next_rise_candidate: int | None = None
        neutral_candidate: int | None = None
        confirmed_fall_start: int | None = None

        def start_confirmed_wave(start_index: int) -> None:
            nonlocal state, active_start, active_baseline, fall_candidate
            nonlocal peak_index, return_candidate, next_rise_candidate
            nonlocal neutral_candidate, rise_candidate
            nonlocal confirmed_fall_start
            active_start = start_index
            active_baseline = _local_baseline(
                rise_start_index=start_index,
                segment_id=segment_id,
                elapsed_s=elapsed_s,
                clean_hr=clean_hr,
                segment_ids=segment_ids,
                config=config,
            )
            state = "awaiting_fall"
            peak_index = start_index
            fall_candidate = None
            return_candidate = None
            next_rise_candidate = None
            neutral_candidate = None
            confirmed_fall_start = None
            rise_candidate = None

        def finish_wave(
            end_index: int,
            *,
            end_reason: str,
            reliable_close: bool,
            forced_peak: int | None = None,
            incomplete_reason: str | None = None,
        ) -> DetectedWave:
            nonlocal next_wave_id
            assert active_start is not None
            actual_peak = (
                forced_peak
                if forced_peak is not None
                else _last_maximum_index(h_detect, active_start, end_index)
            )
            actual_end = max(actual_peak, end_index)
            missing_baseline = active_baseline is None
            complete = reliable_close and not missing_baseline
            reason = incomplete_reason
            if missing_baseline:
                reason = "insufficient_baseline_history"
            wave = DetectedWave(
                wave_id=next_wave_id,
                segment_id=segment_id,
                rise_start_index=active_start,
                peak_index=actual_peak,
                tail_end_index=actual_end,
                baseline_hr_bpm=active_baseline,
                complete=complete,
                end_reason=end_reason,
                incomplete_reason=None if complete else reason,
                confirmed_fall_start_index=confirmed_fall_start,
            )
            next_wave_id += 1
            waves.append(wave)
            return wave

        index = segment_start
        while index <= segment_end:
            slope = trend[index]
            rise_condition = bool(
                supported[index]
                and np.isfinite(slope)
                and slope >= config.rise_threshold_bpm_s
            )

            if state == "seeking_rise":
                if rise_condition:
                    if rise_candidate is None:
                        rise_candidate = index
                    sustained = (
                        elapsed_s[index] - elapsed_s[rise_candidate]
                        >= config.min_sustained_rise_s
                    )
                    total_rise = h_detect[index] - h_detect[rise_candidate]
                    if sustained and total_rise >= config.min_rise_bpm:
                        start_confirmed_wave(rise_candidate)
                else:
                    rise_candidate = None
                index += 1
                continue

            assert active_start is not None
            assert peak_index is not None

            if (
                state == "awaiting_fall"
                and elapsed_s[index] - elapsed_s[active_start]
                >= config.max_wave_duration_s
            ):
                finish_wave(
                    index,
                    end_reason="max_wave_duration",
                    reliable_close=False,
                    forced_peak=peak_index,
                    incomplete_reason="max_wave_duration",
                )
                state = "seeking_rise"
                active_start = None
                active_baseline = None
                peak_index = None
                rise_candidate = None
                index += 1
                continue

            if state == "awaiting_fall":
                if h_detect[index] >= h_detect[peak_index]:
                    peak_index = index
                fall_condition = bool(
                    supported[index]
                    and np.isfinite(slope)
                    and slope <= -config.fall_threshold_bpm_s
                )
                if fall_condition:
                    if fall_candidate is None:
                        fall_candidate = index
                    sustained = (
                        elapsed_s[index] - elapsed_s[fall_candidate]
                        >= config.min_sustained_fall_s
                    )
                    total_fall = h_detect[peak_index] - h_detect[index]
                    if sustained and total_fall >= config.min_fall_bpm:
                        confirmed_fall_start = fall_candidate
                        peak_index = _last_maximum_index(
                            h_detect, active_start, index
                        )
                        state = "following_tail"
                        return_candidate = None
                        next_rise_candidate = None
                        neutral_candidate = None
                else:
                    fall_candidate = None
                index += 1
                continue

            # following_tail: evaluate every possible reliable end and select
            # the earliest boundary, not whichever confirmation happens first.
            assert state == "following_tail"
            closure_candidates: list[tuple[int, int, str, int | None]] = []

            if (
                active_baseline is not None
                and h_detect[index]
                <= active_baseline + config.return_tolerance_bpm
            ):
                if return_candidate is None:
                    return_candidate = index
                if (
                    elapsed_s[index] - elapsed_s[return_candidate]
                    >= config.return_sustain_s
                ):
                    closure_candidates.append(
                        (return_candidate, 0, "return_to_baseline", None)
                    )
            else:
                return_candidate = None

            if rise_condition:
                if next_rise_candidate is None:
                    next_rise_candidate = index
                sustained = (
                    elapsed_s[index] - elapsed_s[next_rise_candidate]
                    >= config.min_sustained_rise_s
                )
                total_rise = h_detect[index] - h_detect[next_rise_candidate]
                if sustained and total_rise >= config.min_rise_bpm:
                    trough = _trough_before_rise(
                        h_detect, peak_index, next_rise_candidate
                    )
                    closure_candidates.append(
                        (trough, 1, "new_rise_trough", next_rise_candidate)
                    )
            else:
                next_rise_candidate = None

            neutral_condition = bool(
                supported[index]
                and np.isfinite(slope)
                and abs(slope) <= config.neutral_slope_tolerance_bpm_s
            )
            if neutral_condition:
                if neutral_candidate is None:
                    neutral_candidate = index
                if (
                    elapsed_s[index] - elapsed_s[neutral_candidate]
                    >= config.neutral_trough_timeout_s
                ):
                    closure_candidates.append(
                        (neutral_candidate, 2, "neutral_trough", None)
                    )
            else:
                neutral_candidate = None

            if closure_candidates:
                end_index, _, end_reason, confirmed_next_start = min(
                    closure_candidates, key=lambda item: (item[0], item[1])
                )
                finish_wave(
                    end_index,
                    end_reason=end_reason,
                    reliable_close=True,
                    forced_peak=peak_index,
                )
                if confirmed_next_start is not None:
                    start_confirmed_wave(confirmed_next_start)
                    peak_index = _last_maximum_index(
                        h_detect, confirmed_next_start, index
                    )
                    index += 1
                else:
                    state = "seeking_rise"
                    active_start = None
                    active_baseline = None
                    peak_index = None
                    rise_candidate = None
                    # Revisit the samples after the backdated boundary so a
                    # later rise that began during confirmation is not lost.
                    index = max(end_index + 1, segment_start)
                continue

            if (
                elapsed_s[index] - elapsed_s[active_start]
                >= config.max_wave_duration_s
            ):
                finish_wave(
                    index,
                    end_reason="max_wave_duration",
                    reliable_close=False,
                    forced_peak=peak_index,
                    incomplete_reason="max_wave_duration",
                )
                state = "seeking_rise"
                active_start = None
                active_baseline = None
                peak_index = None
                rise_candidate = None

            index += 1

        if state != "seeking_rise" and active_start is not None:
            actual_peak = _last_maximum_index(h_detect, active_start, segment_end)
            ended_by_gap = segment_id != valid_segment_ids[-1]
            if state == "awaiting_fall":
                reason = (
                    "long_gap_before_confirmed_fall"
                    if ended_by_gap
                    else "end_of_file_before_confirmed_fall"
                )
            else:
                reason = (
                    "long_gap_before_reliable_close"
                    if ended_by_gap
                    else "end_of_file_before_reliable_close"
                )
            finish_wave(
                segment_end,
                end_reason="long_gap" if ended_by_gap else "end_of_file",
                reliable_close=False,
                forced_peak=actual_peak,
                incomplete_reason=reason,
            )

    waves_tuple = _apply_v3_morphology(
        waves=tuple(waves),
        elapsed_s=elapsed_s,
        h_detect=h_detect,
        clean_hr=clean_hr,
        trend=trend,
        supported=supported,
        config=config,
    )
    count = len(clean_hr)
    wave_ids = np.full(count, -1, dtype=int)
    states = np.full(count, "outside", dtype=object)
    baselines = np.full(count, np.nan, dtype=float)
    receiver_mask = np.zeros(count, dtype=bool)
    donor_mask = np.zeros(count, dtype=bool)
    for wave in waves_tuple:
        start = wave.rise_start_index
        peak = wave.peak_index
        end = wave.tail_end_index
        wave_ids[start : end + 1] = wave.wave_id
        baselines[start : end + 1] = (
            wave.baseline_hr_bpm
            if wave.baseline_hr_bpm is not None
            else np.nan
        )
        if wave.correction_strategy in {"v3_transition", "v3_terminal_fall"}:
            assert wave.hold_start_index is not None
            assert wave.hold_end_index is not None
            assert wave.terminal_fall_start_index is not None
            assert wave.terminal_fall_end_index is not None
            fall_start = wave.terminal_fall_start_index
            fall_end = wave.terminal_fall_end_index
            receiver_mask[start:fall_start] = True
            states[start : wave.hold_start_index] = "rise"
            states[wave.hold_start_index : fall_start] = "hold"
            donor_mask[fall_start : fall_end + 1] = True
            states[fall_start : fall_end + 1] = "terminal_fall"
            if fall_end < end:
                states[fall_end + 1 : end + 1] = "return"
        elif wave.correction_strategy == "none":
            states[start : end + 1] = "ambiguous"
        else:
            receiver_mask[start : peak + 1] = True
            states[start:peak] = "receiver"
            states[peak] = "peak"
            if end > peak:
                donor_mask[peak + 1 : end + 1] = True
                states[peak + 1 : end + 1] = "donor"

    if np.any(receiver_mask & donor_mask):
        raise RuntimeError("receiver and donor windows must never overlap")

    return WaveDetectionResult(
        h_detect=h_detect,
        trend_bpm_per_s=trend,
        trend_supported_mask=supported,
        edge_affected_mask=edge_affected,
        waves=waves_tuple,
        wave_ids=wave_ids,
        wave_states=tuple(str(value) for value in states),
        local_baseline_hr=baselines,
        receiver_mask=receiver_mask,
        donor_mask=donor_mask,
    )


__all__ = ["DetectedWave", "WaveDetectionResult", "detect_hr_waves"]
