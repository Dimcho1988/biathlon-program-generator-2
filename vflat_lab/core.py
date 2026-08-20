"""Transparent empirical Vflat model.

The lab deliberately keeps the production candidate small and interpretable.
It is not a mechanical power model.  Every term can be inspected and tuned by
the coach against controlled, repeated field sessions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VFlatConfig:
    """Complete, serialisable configuration for one reproducible lab run."""

    # Pre-processing
    altitude_smoothing_m: int = 75
    speed_smoothing_s: int = 15
    output_smoothing_s: int = 21
    segment_s: int = 15
    max_gap_s: int = 10

    # Stationary grade curve
    flat_band_pct: float = 1.0
    uphill_amplitude: float = 1.20
    uphill_scale_pct: float = 14.34
    uphill_shape: float = 0.53
    negative_grade_gain: float = 0.229

    # Instantaneous acceleration/deceleration correction (km/h per m/s²)
    acceleration_gain: float = 8.5
    deceleration_gain: float = 21.5

    # Terrain memory
    descent_threshold_pct: float = -3.0
    descent_full_effect_pct: float = -8.0
    descent_memory_s: float = 18.0
    descent_memory_strength_kmh: float = 20.0
    climb_threshold_pct: float = 5.0
    climb_full_effect_pct: float = 10.0
    climb_memory_s: float = 12.0
    climb_memory_strength_kmh: float = 1.0

    # Empirical transition anchoring.  Active only when recent terrain memory
    # or acceleration indicates a transition.
    transition_reference_s: int = 61
    transition_anchor_strength: float = 0.75
    transition_accel_scale_mps2: float = 0.25

    # Operational validity filters
    min_grade_pct: float = -3.0
    max_grade_pct: float = 12.0
    min_speed_kmh: float = 5.0
    turn_threshold_deg: float = 55.0
    min_segment_coverage: float = 0.60

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


def odd_window(requested: int, length: int, minimum: int = 5) -> int:
    """Return a valid odd Savitzky/rolling window bounded by series length."""

    if length < minimum:
        return max(1, length if length % 2 else length - 1)
    value = min(max(minimum, int(requested)), length if length % 2 else length - 1)
    return value if value % 2 else value - 1


def grade_speed_ratio(grade_pct: np.ndarray, config: VFlatConfig) -> np.ndarray:
    """Expected observed/flat speed ratio for a given grade.

    The uphill branch is saturating: it cannot continue increasing the
    correction without bound on steep grades.  The mild negative-grade branch
    covers only the operational -3% to -1% band; steeper descents are excluded.
    """

    grade = np.asarray(grade_pct, dtype=float)
    ratio = np.ones_like(grade, dtype=float)
    flat = float(config.flat_band_pct)

    uphill = grade > flat
    if uphill.any():
        x = np.maximum(grade[uphill] - flat, 0.0) / max(config.uphill_scale_pct, 0.1)
        loss = config.uphill_amplitude * (1.0 - np.exp(-(x**config.uphill_shape)))
        ratio[uphill] = np.exp(-loss)

    mild_downhill = grade < -flat
    if mild_downhill.any():
        gain = config.negative_grade_gain * np.maximum(-grade[mild_downhill] - flat, 0.0)
        ratio[mild_downhill] = np.exp(gain)

    return np.clip(ratio, 0.25, 2.0)


def _terrain_memory(
    grade: np.ndarray,
    *,
    threshold: float,
    full_effect: float,
    tau_s: float,
    direction: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Return bounded recent-terrain state and post-terrain contribution gate."""

    state = np.zeros(len(grade), dtype=float)
    post = np.zeros(len(grade), dtype=float)
    decay = math.exp(-1.0 / max(float(tau_s), 1.0))
    if direction == "descent":
        span = max(abs(full_effect - threshold), 0.1)
        stimulus = np.clip((threshold - grade) / span, 0.0, 1.0)
        active = grade < threshold
    else:
        span = max(abs(full_effect - threshold), 0.1)
        stimulus = np.clip((grade - threshold) / span, 0.0, 1.0)
        active = grade > threshold

    value = 0.0
    for index in range(len(grade)):
        if not np.isfinite(grade[index]):
            value *= decay
            state[index] = value
            continue
        # EWMA while in the source terrain, pure exponential decay afterwards.
        value = decay * value + (1.0 - decay) * float(stimulus[index])
        state[index] = value
        post[index] = 0.0 if active[index] else value
    return state, post


def _rolling_median_by_block(
    values: pd.Series,
    blocks: pd.Series,
    window_s: int,
) -> pd.Series:
    result = pd.Series(np.nan, index=values.index, dtype=float)
    for _, index in blocks.groupby(blocks).groups.items():
        part = values.loc[index]
        window = odd_window(window_s, len(part), minimum=3)
        result.loc[index] = part.rolling(
            window,
            center=True,
            min_periods=max(2, window // 3),
        ).median()
    return result


def _terrain_memory_by_block(
    grade: pd.Series,
    blocks: pd.Series,
    *,
    threshold: float,
    full_effect: float,
    tau_s: float,
    direction: str,
) -> tuple[np.ndarray, np.ndarray]:
    state = np.zeros(len(grade), dtype=float)
    post = np.zeros(len(grade), dtype=float)
    positions = pd.Series(np.arange(len(grade)), index=grade.index)
    for _, index in blocks.groupby(blocks).groups.items():
        block_state, block_post = _terrain_memory(
            grade.loc[index].to_numpy(dtype=float),
            threshold=threshold,
            full_effect=full_effect,
            tau_s=tau_s,
            direction=direction,
        )
        block_positions = positions.loc[index].to_numpy(dtype=int)
        state[block_positions] = block_state
        post[block_positions] = block_post
    return state, post


def apply_vflat_model(timeseries: pd.DataFrame, config: VFlatConfig) -> pd.DataFrame:
    """Apply all stationary, dynamic and memory terms to prepared 1 Hz rows."""

    required = {"grade_pct", "speed_mps", "accel_mps2", "block", "turn_flag"}
    missing = required.difference(timeseries.columns)
    if missing:
        raise ValueError(f"Missing prepared columns: {sorted(missing)}")

    out = timeseries.copy()
    ratio = grade_speed_ratio(out.grade_pct.to_numpy(), config)
    observed_kmh = out.speed_mps.to_numpy(dtype=float) * 3.6
    stationary = observed_kmh / ratio

    accel = out.accel_mps2.to_numpy(dtype=float)
    acceleration_term = config.acceleration_gain * np.maximum(accel, 0.0)
    deceleration_term = -config.deceleration_gain * np.maximum(-accel, 0.0)

    descent_state, post_descent = _terrain_memory_by_block(
        out.grade_pct,
        out.block,
        threshold=config.descent_threshold_pct,
        full_effect=config.descent_full_effect_pct,
        tau_s=config.descent_memory_s,
        direction="descent",
    )
    climb_state, post_climb = _terrain_memory_by_block(
        out.grade_pct,
        out.block,
        threshold=config.climb_threshold_pct,
        full_effect=config.climb_full_effect_pct,
        tau_s=config.climb_memory_s,
        direction="climb",
    )
    descent_term = -config.descent_memory_strength_kmh * post_descent
    # The climb correction is used only after the grade has returned below the
    # climb threshold.  It compensates a temporary entry-speed deficit.
    climb_term = config.climb_memory_strength_kmh * post_climb

    empirical_raw = stationary + acceleration_term + deceleration_term + descent_term + climb_term
    empirical_raw = np.maximum(empirical_raw, 0.0)
    local_reference = _rolling_median_by_block(
        pd.Series(empirical_raw, index=out.index),
        out.block,
        config.transition_reference_s,
    )
    transition_weight = np.maximum.reduce(
        [
            post_descent,
            post_climb,
            np.clip(np.abs(accel) / max(config.transition_accel_scale_mps2, 0.01), 0.0, 1.0),
        ]
    )
    anchored = empirical_raw + (
        config.transition_anchor_strength
        * transition_weight
        * (local_reference.to_numpy(dtype=float) - empirical_raw)
    )
    final = _rolling_median_by_block(
        pd.Series(anchored, index=out.index),
        out.block,
        config.output_smoothing_s,
    )

    out["speed_kmh"] = observed_kmh
    out["grade_ratio"] = ratio
    out["vflat_stationary_kmh"] = stationary
    out["acceleration_term_kmh"] = acceleration_term
    out["deceleration_term_kmh"] = deceleration_term
    out["descent_memory"] = descent_state
    out["post_descent_memory"] = post_descent
    out["descent_memory_term_kmh"] = descent_term
    out["climb_memory"] = climb_state
    out["post_climb_memory"] = post_climb
    out["climb_memory_term_kmh"] = climb_term
    out["vflat_empirical_raw_kmh"] = empirical_raw
    out["transition_weight"] = transition_weight
    out["local_reference_kmh"] = local_reference
    out["vflat_anchored_kmh"] = anchored
    out["vflat_final_kmh"] = final

    out["valid"] = (
        out.grade_pct.between(config.min_grade_pct, config.max_grade_pct)
        & (out.speed_kmh >= config.min_speed_kmh)
        & (~out.turn_flag.astype(bool))
        & np.isfinite(out.vflat_final_kmh)
    )
    return out
