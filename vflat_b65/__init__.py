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

__all__ = [
    "CONFIG_VERSION",
    "MODEL_VERSION",
    "VFlatB65Config",
    "apply_vflat_b65",
    "base_multiplier",
    "derive_grade_from_altitude_distance",
    "literature_multiplier",
    "stationary_multiplier_b65",
]
