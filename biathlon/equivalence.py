"""Versioned coach coefficients shared by actual and planned load."""
DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM = 3.0
Z5_EQUIVALENCE_SLOPE_PP_PER_BPM = 5.0
EQUIVALENCE_VERSION = "intra_zone_linear_v2_z5_5pp"


def equivalence_slope(zone: str) -> float:
    return Z5_EQUIVALENCE_SLOPE_PP_PER_BPM if zone == "Z5" else DEFAULT_EQUIVALENCE_SLOPE_PP_PER_BPM
