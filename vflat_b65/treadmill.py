"""Recorded treadmill speed, with recorded incline or an explicit flat assumption."""
from dataclasses import dataclass

import numpy as np

from .running import RunningGradeConfig, apply_running_grade

MODEL_VERSION = "vflat_treadmill_recorded_speed_v1"
CONFIG_VERSION = "vflat_treadmill_recorded_incline_or_flat_v1"
ASSUMED_FLAT_FLAG = "TREADMILL_GRADE_ASSUMED_FLAT"


@dataclass(frozen=True)
class TreadmillConfig(RunningGradeConfig):
    def to_dict(self):
        return {**super().to_dict(), "model_version": MODEL_VERSION,
                "config_version": CONFIG_VERSION,
                "missing_incline_policy": "ASSUME_ZERO_PERCENT_WITH_DISCLOSURE"}


def apply_treadmill_speed(timeseries, config=None):
    frame = timeseries.copy(deep=True)
    missing = ~np.isfinite(frame.grade_pct.to_numpy(dtype=float))
    frame.loc[missing, "grade_pct"] = 0.
    result = apply_running_grade(frame, config or TreadmillConfig())
    result["grade_assumed_flat"] = missing
    result["quality_flags"] = [
        tuple(sorted(set(flags or ()) | ({ASSUMED_FLAT_FLAG} if assumed else set())))
        for flags, assumed in zip(frame.get("quality_flags", [()] * len(frame)), missing)
    ]
    result["vflat_model_version"] = MODEL_VERSION
    result["vflat_config_version"] = CONFIG_VERSION
    result.attrs["terrain_correction"] = {
        "factor": 1., "applied": False, "reason": "TREADMILL_RECORDED_SPEED",
        "assumed_flat_samples": int(missing.sum()),
    }
    return result
