"""Transparent Vflat B65 stationary and dynamic model.

The stationary path uses a clipped grade.  The unclipped measured/derived
grade remains the sole input to the two terrain-memory states.  No value is
shifted in time.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter


MODEL_VERSION = "vflat_b65_dynamic_v2"
CONFIG_VERSION = "vflat_b65_config_v2"

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
    speed_smoothing_s: int = 15
    output_smoothing_s: int = 21
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

    acceleration_gain: float = 8.5
    deceleration_gain: float = 21.5
    descent_threshold_pct: float = -3.0
    descent_full_effect_pct: float = -8.0
    descent_memory_s: float = 18.0
    descent_memory_strength_kmh: float = 20.0
    climb_threshold_pct: float = 5.0
    climb_full_effect_pct: float = 10.0
    climb_memory_s: float = 12.0
    climb_memory_strength_kmh: float = 1.0
    transition_reference_s: int = 61
    transition_anchor_strength: float = 0.90
    transition_accel_scale_mps2: float = 0.10
    transition_decay_s: float = 18.0

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
        if self.output_smoothing_s != 21:
            raise ValueError("Vflat B65 output smoothing is locked at 21 s")
        if self.transition_decay_s <= 0.0:
            raise ValueError("Vflat B65 transition decay must be positive")

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


def _terrain_memory(
    grade: np.ndarray,
    *,
    threshold: float,
    full_effect: float,
    tau_s: float,
    direction: str,
) -> tuple[np.ndarray, np.ndarray]:
    state = np.zeros(len(grade), dtype=float)
    post = np.zeros(len(grade), dtype=float)
    decay = math.exp(-1.0 / max(float(tau_s), 1.0))
    span = max(abs(full_effect - threshold), 0.1)
    if direction == "descent":
        stimulus = np.clip((threshold - grade) / span, 0.0, 1.0)
        active = grade < threshold
    else:
        stimulus = np.clip((grade - threshold) / span, 0.0, 1.0)
        active = grade > threshold
    value = 0.0
    for index in range(len(grade)):
        if not np.isfinite(grade[index]):
            value *= decay
            state[index] = value
            continue
        value = decay * value + (1.0 - decay) * float(stimulus[index])
        state[index] = value
        post[index] = 0.0 if active[index] else value
    return state, post


def _rolling_median(values: pd.Series, blocks: pd.Series, window_s: int) -> pd.Series:
    result = pd.Series(np.nan, index=values.index, dtype=float)
    for _, index in blocks.groupby(blocks).groups.items():
        part = values.loc[index]
        window = min(max(1, int(window_s)), len(part))
        if window % 2 == 0:
            window = max(1, window - 1)
        result.loc[index] = part.rolling(
            window, center=True, min_periods=max(1, window // 3)
        ).median()
    return result


def _memory_by_block(grade: pd.Series, blocks: pd.Series, **kwargs: float | str):
    state = np.zeros(len(grade), dtype=float)
    post = np.zeros(len(grade), dtype=float)
    positions = pd.Series(np.arange(len(grade)), index=grade.index)
    for _, index in blocks.groupby(blocks).groups.items():
        block_state, block_post = _terrain_memory(
            grade.loc[index].to_numpy(dtype=float), **kwargs
        )
        target = positions.loc[index].to_numpy(dtype=int)
        state[target], post[target] = block_state, block_post
    return state, post


def _descent_transition_anchor_weight(
    grade: np.ndarray,
    accel: np.ndarray,
    *,
    threshold_pct: float,
    accel_scale_mps2: float,
    decay_s: float,
) -> np.ndarray:
    """Gate the local-speed anchor to gravity-assisted descent entries.

    A transition starts only when grade crosses from the threshold or above to
    below it while speed is increasing.  Its initial strength follows the
    measured positive acceleration and then falls linearly to zero.  The short
    tail deliberately survives the end of the descent to account for inertia.
    """
    weight = np.zeros(len(grade), dtype=float)
    remaining_weight = 0.0
    step = 1.0 / max(float(decay_s), 1.0)
    for index in range(len(grade)):
        if index > 0 and (
            np.isfinite(grade[index - 1])
            and np.isfinite(grade[index])
            and grade[index - 1] >= threshold_pct
            and grade[index] < threshold_pct
            and np.isfinite(accel[index])
            and accel[index] > 0.0
        ):
            remaining_weight = float(
                np.clip(accel[index] / max(accel_scale_mps2, 1e-9), 0.0, 1.0)
            )
        weight[index] = remaining_weight
        remaining_weight = max(0.0, remaining_weight - step)
    return weight


def _descent_transition_anchor_weight_by_block(
    grade: pd.Series,
    accel: np.ndarray,
    blocks: pd.Series,
    *,
    threshold_pct: float,
    accel_scale_mps2: float,
    decay_s: float,
) -> np.ndarray:
    weight = np.zeros(len(grade), dtype=float)
    positions = pd.Series(np.arange(len(grade)), index=grade.index)
    accel_series = pd.Series(accel, index=grade.index)
    for _, index in blocks.groupby(blocks).groups.items():
        target = positions.loc[index].to_numpy(dtype=int)
        weight[target] = _descent_transition_anchor_weight(
            grade.loc[index].to_numpy(dtype=float),
            accel_series.loc[index].to_numpy(dtype=float),
            threshold_pct=threshold_pct,
            accel_scale_mps2=accel_scale_mps2,
            decay_s=decay_s,
        )
    return weight


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
    acceleration_term = selected.acceleration_gain * np.maximum(accel, 0.0)
    deceleration_term = -selected.deceleration_gain * np.maximum(-accel, 0.0)
    descent_state, post_descent = _memory_by_block(
        out.grade_pct, out.block,
        threshold=selected.descent_threshold_pct,
        full_effect=selected.descent_full_effect_pct,
        tau_s=selected.descent_memory_s,
        direction="descent",
    )
    climb_state, post_climb = _memory_by_block(
        out.grade_pct, out.block,
        threshold=selected.climb_threshold_pct,
        full_effect=selected.climb_full_effect_pct,
        tau_s=selected.climb_memory_s,
        direction="climb",
    )
    descent_term = -selected.descent_memory_strength_kmh * post_descent
    climb_term = selected.climb_memory_strength_kmh * post_climb
    dynamic_raw = np.maximum(
        stationary + acceleration_term + deceleration_term + descent_term + climb_term,
        0.0,
    )
    local_reference = _rolling_median(
        pd.Series(dynamic_raw, index=out.index), out.block, selected.transition_reference_s
    )
    transition_weight = _descent_transition_anchor_weight_by_block(
        out.grade_pct,
        accel,
        out.block,
        threshold_pct=selected.descent_threshold_pct,
        accel_scale_mps2=selected.transition_accel_scale_mps2,
        decay_s=selected.transition_decay_s,
    )
    anchored = dynamic_raw + selected.transition_anchor_strength * transition_weight * (
        local_reference.to_numpy(dtype=float) - dynamic_raw
    )
    final = _rolling_median(
        pd.Series(anchored, index=out.index), out.block, selected.output_smoothing_s
    )
    out["speed_raw_kmh"] = speed_kmh
    out["grade_actual_pct"] = actual_grade
    out["grade_stationary_pct"] = stationary_grade
    out["stationary_multiplier_b65"] = multiplier
    out["vflat_stationary_kmh"] = stationary
    out["acceleration_term_kmh"] = acceleration_term
    out["deceleration_term_kmh"] = deceleration_term
    out["descent_memory"] = descent_state
    out["post_descent_memory"] = post_descent
    out["descent_memory_term_kmh"] = descent_term
    out["climb_memory"] = climb_state
    out["post_climb_memory"] = post_climb
    out["climb_memory_term_kmh"] = climb_term
    out["transition_weight"] = transition_weight
    out["local_reference_kmh"] = local_reference
    out["vflat_anchored_kmh"] = anchored
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
