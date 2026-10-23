"""Expert running grade table, independent of the ski/roller B65 dynamics.

Input is the existing aligned, block-smoothed speed/grade view. The table maps
observed speed to equivalent flat speed; grades are percentages, not fractions.
No extrapolation beyond the supplied grade range is treated as measured speed.
"""
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .core import _rolling_median

MODEL_VERSION = "vflat_run_grade_table_v1"
CONFIG_VERSION = "vflat_run_grade_config_v1"
GRADE_TABLE = (
    (-32., 1.6), (-26., 1.4), (-24., 1.2), (-20., 1.1), (-18., 1.),
    (-14., .9), (-10., .87), (-4., .9), (0., 1.), (4., 1.12),
    (10., 1.5), (20., 2.3), (30., 3.4),
)


@dataclass(frozen=True)
class RunningGradeConfig:
    min_speed_kmh: float = 5.
    output_smoothing_s: int = 21

    def to_dict(self):
        return {**asdict(self), "model_version": MODEL_VERSION,
                "config_version": CONFIG_VERSION, "interpolation": "LINEAR",
                "grade_table": [list(point) for point in GRADE_TABLE]}


def running_grade_multiplier(grade_pct):
    grades, factors = zip(*GRADE_TABLE)
    return np.interp(np.asarray(grade_pct, dtype=float), grades, factors,
                     left=np.nan, right=np.nan)


def apply_running_grade(timeseries: pd.DataFrame, config: RunningGradeConfig | None = None):
    selected = config or RunningGradeConfig()
    required = {"grade_pct", "speed_mps", "block", "turn_flag"}
    if not required.issubset(timeseries):
        raise ValueError(f"Missing prepared columns: {sorted(required.difference(timeseries.columns))}")
    out = timeseries.copy(deep=True)
    grade = out.grade_pct.to_numpy(dtype=float)
    speed = out.speed_mps.to_numpy(dtype=float)*3.6
    multiplier = running_grade_multiplier(grade)
    corrected = pd.Series(speed*multiplier, index=out.index)
    final = _rolling_median(corrected, out.block, selected.output_smoothing_s)
    # Smoothing must not turn an unsupported grade or absent speed into evidence.
    supported = np.isfinite(grade) & np.isfinite(multiplier) & np.isfinite(speed) & (out.block >= 0)
    final = final.where(supported)
    out["speed_raw_kmh"] = speed
    out["grade_actual_pct"] = grade
    out["grade_effective_pct"] = grade
    out["grade_stationary_pct"] = grade
    out["running_grade_multiplier"] = multiplier
    # Retain the transport field for existing graphs/consumers; versions identify
    # the actual model. No B65 acceleration, inertia or terrain regression applies.
    out["vflat_b65_kmh"] = final
    out["vflat_before_terrain_kmh"] = final
    out["vflat_delta_kmh"] = final-speed
    out["vflat_model_version"] = MODEL_VERSION
    out["vflat_config_version"] = CONFIG_VERSION
    out["valid"] = supported & np.isfinite(final) & (speed >= selected.min_speed_kmh) & ~out.turn_flag.astype(bool)
    out.attrs["terrain_correction"] = {"factor": 1., "applied": False, "reason": "RUNNING_GRADE_TABLE"}
    return out
