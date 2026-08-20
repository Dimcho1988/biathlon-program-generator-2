"""Experimental terrain-normalised speed laboratory."""

from .core import VFlatConfig, apply_vflat_model, grade_speed_ratio
from .diagnostics import activity_summary, recommended_work_laps, segment_timeseries
from .tcx import ParsedTCX, parse_tcx, prepare_activity

__all__ = [
    "ParsedTCX",
    "VFlatConfig",
    "activity_summary",
    "apply_vflat_model",
    "grade_speed_ratio",
    "parse_tcx",
    "prepare_activity",
    "recommended_work_laps",
    "segment_timeseries",
]
