"""Versioned Vflat B65 dynamic shadow model."""

from .core import (
    CONFIG_VERSION,
    MODEL_VERSION,
    VFlatB65Config,
    apply_vflat_b65,
    base_multiplier,
    derive_grade_from_altitude_distance,
    literature_multiplier,
    stationary_multiplier_b65,
)
from .sprint_str import (
    SPRINT_STR_CONFIG_VERSION,
    SPRINT_STR_MODEL_VERSION,
    SprintSTRConfig,
    SprintSTRDetection,
    detect_sprint_str,
)

__all__ = [
    "CONFIG_VERSION",
    "MODEL_VERSION",
    "SPRINT_STR_CONFIG_VERSION",
    "SPRINT_STR_MODEL_VERSION",
    "SprintSTRConfig",
    "SprintSTRDetection",
    "VFlatB65Config",
    "apply_vflat_b65",
    "base_multiplier",
    "derive_grade_from_altitude_distance",
    "detect_sprint_str",
    "literature_multiplier",
    "stationary_multiplier_b65",
]
