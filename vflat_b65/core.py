"""Transparent Vflat B65 model with explicit descent-inertia replacement.

The trustworthy path is deliberately small: minimally denoised measured speed
is multiplied by the stationary B65 grade factor.  Samples around steep
descents, plus accelerating transitions on milder descents, are treated as
gravity/inertia contaminated and replaced by the preceding stable Vflat level.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter


MODEL_VERSION = "vflat_b65_inertia_extrapolation_v4"
CONFIG_VERSION = "vflat_b65_config_v4"

# Authoritative B65 multipliers supplied for the locked model.  Above +5%,
# these anchors define the approved literature reference curve used by the
# explicit 65% smoothstep blend.  Linear interpolation keeps the reference
# curve transparent and continuous without introducing unapproved coefficients.
_B65_GOLDEN = {
    -3.0: 0.632547476,
    -2.0: 0.795328534,
    -1.0: 1.0,
    0.0: 1.0,
    1.0: 1.0,
    5.0: 1.613135482,
    6.0: 1.700138717,
    7.0: 1.849621614,
    8.0: 2.034843677,
    10.0: 2.357542699,
    12.0: 2.710460948,
    13.0: 2.899228838,
    14.0: 3.096657638,
    15.0: 3.303062049,
}


@dataclass(frozen=True, slots=True)
class VFlatB65Config:
    config_version: str = CONFIG_VERSION

    altitude_smoothing_m: int = 75
    # Three centred samples remove isolated device spikes while retaining
    # short, intentional accelerations.
    speed_smoothing_s: int = 3
    output_smoothing_s: int = 1
    segment_s: int = 15
    max_gap_s: int = 10

    flat_band_pct: float = 1.0
    uphill_amplitude: float = 1.20
    uphill_scale_pct: float = 14.34
    uphill_shape: float = 0.53
    negative_grade_gain: float = 0.229
    steep_blend_start_pct: float = 5.0
    steep_blend_end_pct: float = 8.0
    steep_blend_weight: float = 0.65
    stationary_min_grade_pct: float = -3.0
    stationary_max_grade_pct: float = 15.0

    mild_descent_threshold_pct: float = -1.0
    steep_descent_threshold_pct: float = -3.0
    mild_inertia_accel_mps2: float = 0.05
    mild_margin_before_s: int = 5
    mild_margin_after_s: int = 15
    steep_margin_before_s: int = 15
    steep_margin_after_s: int = 15
    extrapolation_history_s: int = 10

    min_speed_kmh: float = 5.0
    turn_threshold_deg: float = 55.0
    min_segment_coverage: float = 0.60

    def __post_init__(self) -> None:
        if self.config_version != CONFIG_VERSION:
            raise ValueError("unsupported Vflat B65 config version")
        if self.stationary_min_grade_pct != -3.0:
            raise ValueError("Vflat B65 stationary minimum grade is locked at -3%")
        if self.stationary_max_grade_pct != 15.0:
            raise ValueError("Vflat B65 stationary maximum grade is locked at +15%")
        if not self.steep_blend_start_pct < self.steep_blend_end_pct:
            raise ValueError("invalid B65 blend interval")
        if not 0.0 <= self.steep_blend_weight <= 1.0:
            raise ValueError("B65 blend weight must be in [0, 1]")
        if self.output_smoothing_s != 1:
            raise ValueError("Vflat B65 v4 output smoothing is locked off")
        if self.speed_smoothing_s <= 0 or self.speed_smoothing_s % 2 == 0:
            raise ValueError("Vflat B65 speed smoothing must be a positive odd window")
        if not (
            self.steep_descent_threshold_pct
            < self.mild_descent_threshold_pct
            < 0.0
        ):
            raise ValueError("invalid Vflat descent thresholds")
        if self.mild_inertia_accel_mps2 <= 0.0:
            raise ValueError("mild-descent inertia acceleration must be positive")
        margins = (
            self.mild_margin_before_s,
            self.mild_margin_after_s,
            self.steep_margin_before_s,
            self.steep_margin_after_s,
        )
        if any(value < 0 for value in margins):
            raise ValueError("Vflat inertia margins must be non-negative")
        if self.extrapolation_history_s <= 0:
            raise ValueError("Vflat extrapolation history must be positive")

    def to_dict(self) -> dict[str, float | int | str]:
        return asdict(self)


def _smoothstep(values: np.ndarray, start: float, end: float) -> np.ndarray:
    t = np.clip((values - start) / (end - start), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def base_multiplier(grade_pct: np.ndarray, config: VFlatB65Config) -> np.ndarray:
    """Original transparent empirical multiplier before the steep blend."""
    grade = np.asarray(grade_pct, dtype=float)
    result = np.ones_like(grade)
    flat = config.flat_band_pct
    uphill = grade > flat
    if np.any(uphill):
        x = np.maximum(grade[uphill] - flat, 0.0) / config.uphill_scale_pct
        loss = config.uphill_amplitude * (1.0 - np.exp(-(x**config.uphill_shape)))
        result[uphill] = np.exp(loss)
    downhill = grade < -flat
    if np.any(downhill):
        gain = config.negative_grade_gain * np.maximum(-grade[downhill] - flat, 0.0)
        result[downhill] = np.exp(-gain)
    return result


def _approved_literature_anchors(config: VFlatB65Config) -> tuple[np.ndarray, np.ndarray]:
    grades = np.asarray([5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 13.0, 14.0, 15.0])
    base = base_multiplier(grades, config)
    smooth = _smoothstep(grades, config.steep_blend_start_pct, config.steep_blend_end_pct)
    golden = np.asarray([_B65_GOLDEN[float(value)] for value in grades])
    literature = base.copy()
    active = smooth > 0.0
    literature[active] = base[active] + (
        (golden[active] - base[active])
        / (config.steep_blend_weight * smooth[active])
    )
    return grades, literature


def literature_multiplier(grade_pct: np.ndarray, config: VFlatB65Config) -> np.ndarray:
    """Continuous approved literature-reference curve for the B65 blend."""
    values = np.asarray(grade_pct, dtype=float)
    anchor_grade, anchor_value = _approved_literature_anchors(config)
    return np.interp(values, anchor_grade, anchor_value)


def stationary_multiplier_b65(
    actual_grade_pct: np.ndarray,
    config: VFlatB65Config | None = None,
) -> np.ndarray:
    """Return the locked B65 multiplier using stationary grade clip [-3, 15]."""
    selected = config or VFlatB65Config()
    actual = np.asarray(actual_grade_pct, dtype=float)
    stationary_grade = np.clip(
        actual,
        selected.stationary_min_grade_pct,
        selected.stationary_max_grade_pct,
    )
    base = base_multiplier(stationary_grade, selected)
    literature = literature_multiplier(stationary_grade, selected)
    smooth = _smoothstep(
        stationary_grade,
        selected.steep_blend_start_pct,
        selected.steep_blend_end_pct,
    )
    return base + selected.steep_blend_weight * smooth * (literature - base)


def derive_grade_from_altitude_distance(
    altitude_m: np.ndarray,
    cumulative_distance_m: np.ndarray,
    *,
    smoothing_m: int = 75,
) -> np.ndarray:
    """Reproducibly derive spatial grade without GPS coordinates."""
    altitude = np.asarray(altitude_m, dtype=float)
    distance = np.asarray(cumulative_distance_m, dtype=float)
    result = np.full(len(altitude), np.nan, dtype=float)
    valid = np.isfinite(altitude) & np.isfinite(distance)
    if np.count_nonzero(valid) < 5:
        return result
    valid_distance = distance[valid]
    valid_altitude = altitude[valid]
    unique_distance, unique_index = np.unique(valid_distance, return_index=True)
    unique_altitude = valid_altitude[unique_index]
    if len(unique_distance) < 5 or unique_distance[-1] - unique_distance[0] < 10.0:
        return result
    grid = np.arange(math.ceil(unique_distance[0]), math.floor(unique_distance[-1]) + 1)
    if len(grid) < 5:
        return result
    altitude_grid = np.interp(grid, unique_distance, unique_altitude)
    window = min(int(smoothing_m), len(grid) if len(grid) % 2 else len(grid) - 1)
    window = max(5, window if window % 2 else window - 1)
    smooth = (
        savgol_filter(altitude_grid, window, 2, mode="interp")
        if window <= len(grid)
        else altitude_grid
    )
    grade_grid = np.gradient(smooth, grid) * 100.0
    result[valid] = np.interp(valid_distance, grid, grade_grid)
    return result


def _expand_mask(mask: np.ndarray, *, before_s: int, after_s: int) -> np.ndarray:
    """Expand true samples by explicit 1 Hz margins without crossing a block."""
    expanded = np.zeros(len(mask), dtype=bool)
    for index in np.flatnonzero(mask):
        left = max(0, int(index) - int(before_s))
        right = min(len(mask), int(index) + int(after_s) + 1)
        expanded[left:right] = True
    return expanded


def _inertia_mask_by_block(
    grade: pd.Series,
    accel: np.ndarray,
    blocks: pd.Series,
    config: VFlatB65Config,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Identify steep descents and accelerating mild-descent transitions."""
    combined = np.zeros(len(grade), dtype=bool)
    steep_result = np.zeros(len(grade), dtype=bool)
    mild_result = np.zeros(len(grade), dtype=bool)
    positions = pd.Series(np.arange(len(grade)), index=grade.index)
    accel_series = pd.Series(accel, index=grade.index)
    for _, index in blocks.groupby(blocks).groups.items():
        target = positions.loc[index].to_numpy(dtype=int)
        block_grade = grade.loc[index].to_numpy(dtype=float)
        block_accel = accel_series.loc[index].to_numpy(dtype=float)
        steep = np.isfinite(block_grade) & (
            block_grade < config.steep_descent_threshold_pct
        )
        mild_trigger = (
            np.isfinite(block_grade)
            & np.isfinite(block_accel)
            & (block_grade < config.mild_descent_threshold_pct)
            & (block_grade >= config.steep_descent_threshold_pct)
            & (block_accel >= config.mild_inertia_accel_mps2)
        )
        steep_expanded = _expand_mask(
            steep,
            before_s=config.steep_margin_before_s,
            after_s=config.steep_margin_after_s,
        )
        mild_expanded = _expand_mask(
            mild_trigger,
            before_s=config.mild_margin_before_s,
            after_s=config.mild_margin_after_s,
        )
        steep_result[target] = steep_expanded
        mild_result[target] = mild_expanded
        combined[target] = steep_expanded | mild_expanded
    return combined, steep_result, mild_result


def _extrapolate_preceding_level_by_block(
    direct_vflat: np.ndarray,
    mask: np.ndarray,
    blocks: pd.Series,
    *,
    history_s: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Replace each masked run with its preceding stable median level.

    This is a bounded zero-order extrapolation.  Unlike a linear projection it
    cannot drift to impossible speeds during a long descent.  If an activity
    block starts inside a masked region, the first subsequent valid level is
    used as a documented fallback.
    """
    result = np.asarray(direct_vflat, dtype=float).copy()
    reference = np.full(len(result), np.nan, dtype=float)
    positions = pd.Series(np.arange(len(result)), index=blocks.index)
    for _, index in blocks.groupby(blocks).groups.items():
        target = positions.loc[index].to_numpy(dtype=int)
        local_mask = mask[target]
        local_values = result[target]
        cursor = 0
        while cursor < len(target):
            if not local_mask[cursor]:
                cursor += 1
                continue
            start = cursor
            while cursor < len(target) and local_mask[cursor]:
                cursor += 1
            end = cursor
            history = local_values[max(0, start - history_s):start]
            history = history[np.isfinite(history)]
            if history.size:
                level = float(np.median(history))
            else:
                future = local_values[end:]
                future = future[np.isfinite(future)]
                level = float(future[0]) if future.size else math.nan
            local_values[start:end] = level
            reference[target[start:end]] = level
        result[target] = local_values
    return result, reference


def apply_vflat_b65(
    timeseries: pd.DataFrame,
    config: VFlatB65Config | None = None,
) -> pd.DataFrame:
    """Calculate B65 once on prepared aligned rows without mutating input."""
    selected = config or VFlatB65Config()
    required = {"grade_pct", "speed_mps", "accel_mps2", "block", "turn_flag"}
    missing = required.difference(timeseries.columns)
    if missing:
        raise ValueError(f"Missing prepared columns: {sorted(missing)}")
    out = timeseries.copy(deep=True)
    actual_grade = out.grade_pct.to_numpy(dtype=float)
    stationary_grade = np.clip(
        actual_grade,
        selected.stationary_min_grade_pct,
        selected.stationary_max_grade_pct,
    )
    multiplier = stationary_multiplier_b65(actual_grade, selected)
    speed_kmh = out.speed_mps.to_numpy(dtype=float) * 3.6
    stationary = speed_kmh * multiplier
    accel = out.accel_mps2.to_numpy(dtype=float)
    inertia_mask, steep_mask, mild_mask = _inertia_mask_by_block(
        out.grade_pct,
        accel,
        out.block,
        selected,
    )
    extrapolated, inertia_reference = _extrapolate_preceding_level_by_block(
        stationary,
        inertia_mask,
        out.block,
        history_s=selected.extrapolation_history_s,
    )
    final = extrapolated
    out["speed_raw_kmh"] = speed_kmh
    out["grade_actual_pct"] = actual_grade
    out["grade_stationary_pct"] = stationary_grade
    out["stationary_multiplier_b65"] = multiplier
    out["vflat_stationary_kmh"] = stationary
    out["vflat_direct_kmh"] = stationary
    out["inertia_extrapolated"] = inertia_mask
    out["steep_descent_inertia"] = steep_mask
    out["mild_descent_inertia"] = mild_mask & ~steep_mask
    out["inertia_reference_kmh"] = inertia_reference
    out["vflat_b65_kmh"] = final
    out["vflat_delta_kmh"] = final - speed_kmh
    out["vflat_model_version"] = MODEL_VERSION
    out["vflat_config_version"] = CONFIG_VERSION
    out["valid"] = (
        np.isfinite(actual_grade)
        & np.isfinite(final)
        & (speed_kmh >= selected.min_speed_kmh)
        & (~out.turn_flag.astype(bool))
    )
    return out
