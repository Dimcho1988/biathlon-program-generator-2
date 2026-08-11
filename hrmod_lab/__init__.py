"""Experimental, offline HR-only HRmod Lab package.

The public core entry point accepts only timestamped HR samples, an individual
HR profile, and HR-model configuration.  Reference-channel validation lives in
a separate module and cannot enter this call boundary.
"""

from .hrmod_core import compute_hrmod_hr_only
from .schemas import (
    CONFIG_VERSION,
    MODEL_VERSION,
    AthleteHRProfile,
    HRInputSample,
    HRmodConfig,
    HRmodDiagnostics,
    HRmodResult,
    HRmodTimeseriesPoint,
    HRSample,
    HRZone,
    WaveSummary,
    ZoneSummary,
)

__version__ = MODEL_VERSION

__all__ = [
    "AthleteHRProfile",
    "CONFIG_VERSION",
    "HRInputSample",
    "HRmodConfig",
    "HRmodDiagnostics",
    "HRmodResult",
    "HRmodTimeseriesPoint",
    "HRSample",
    "HRZone",
    "MODEL_VERSION",
    "WaveSummary",
    "ZoneSummary",
    "compute_hrmod_hr_only",
]
