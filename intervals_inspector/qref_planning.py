"""Pure diagnostic planning helpers based on Qref and the single Tref.

These helpers do not generate or publish a training plan.  They expose the
explicit multipliers requested for shadow review while the protected adaptive
planner remains unchanged.
"""

from __future__ import annotations

import math
from numbers import Real
from typing import Any, Mapping


WEEKLY_PHASE_MULTIPLIERS = {
    "recovery": 0.80,
    "maintenance": 1.10,
    "accent": 2.00,
}
MAX_WEEKLY_MULTIPLIER = 2.50
SESSION_DOSE_RANGES = {
    "building": (0.60, 0.70),
    "maintenance": (0.30, 0.40),
}
SECONDARY_DIRECT_RATIO_THRESHOLD = 0.50
DEFAULT_PLANNING_SETTINGS = {
    "weekly_recovery": 0.80,
    "weekly_maintenance": 1.10,
    "weekly_accent": 2.00,
    "weekly_max": 2.50,
    "building_low": 0.60,
    "building_high": 0.70,
    "maintenance_low": 0.30,
    "maintenance_high": 0.40,
}


def _non_negative(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} must be a finite non-negative number")
    rendered = float(value)
    if not math.isfinite(rendered) or rendered < 0.0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return rendered


def validate_planning_settings(value: Mapping[str, float]) -> dict[str, float]:
    """Validate the adjustable diagnostic defaults without persistence."""

    if not isinstance(value, Mapping):
        raise ValueError("planning settings must be a mapping")
    rendered = {
        key: _non_negative(value.get(key), key)
        for key in DEFAULT_PLANNING_SETTINGS
    }
    if rendered["weekly_max"] <= 0.0:
        raise ValueError("weekly_max must be positive")
    for phase in ("recovery", "maintenance", "accent"):
        if rendered[f"weekly_{phase}"] > rendered["weekly_max"]:
            raise ValueError(f"weekly_{phase} must not exceed weekly_max")
    for kind in ("building", "maintenance"):
        if rendered[f"{kind}_low"] > rendered[f"{kind}_high"]:
            raise ValueError(f"{kind}_low must not exceed {kind}_high")
    return rendered


def weekly_target(
    tref: float,
    multiplier: float,
    *,
    maximum_multiplier: float = MAX_WEEKLY_MULTIPLIER,
) -> float:
    """Return ``M_phase × Tref`` within the diagnostic 2.5 upper range."""

    capacity = _non_negative(tref, "tref")
    factor = _non_negative(multiplier, "multiplier")
    maximum = _non_negative(maximum_multiplier, "maximum_multiplier")
    if maximum <= 0.0 or factor > maximum:
        raise ValueError("weekly multiplier must not exceed the configured maximum")
    return capacity * factor


def session_dose_range(
    tref: float,
    session_kind: str,
    *,
    dose_ranges: Mapping[str, tuple[float, float]] = SESSION_DOSE_RANGES,
) -> tuple[float, float]:
    """Return the requested main-zone range for one diagnostic session."""

    capacity = _non_negative(tref, "tref")
    if session_kind not in dose_ranges:
        raise ValueError("session kind must be building or maintenance")
    raw_lower, raw_upper = dose_ranges[session_kind]
    lower = _non_negative(raw_lower, f"{session_kind}_low")
    upper = _non_negative(raw_upper, f"{session_kind}_high")
    if lower > upper:
        raise ValueError("session dose lower bound must not exceed upper bound")
    return capacity * lower, capacity * upper


def adjust_target_for_recovery(
    base_target: float,
    recovery_fraction: float,
) -> float:
    """Apply the zone recovery percentage exactly once by multiplication."""

    target = _non_negative(base_target, "base_target")
    recovery = _non_negative(recovery_fraction, "recovery_fraction")
    if recovery > 1.0:
        raise ValueError("recovery_fraction must be between 0 and 1")
    return target * recovery


def direct_ratio(qref: float, tref: float) -> float:
    """Return direct ``Qref/Tref`` without cascade or spillover."""

    direct = _non_negative(qref, "qref")
    capacity = _non_negative(tref, "tref")
    if capacity <= 0.0:
        raise ValueError("tref must be positive")
    return direct / capacity


def limiting_secondary_zones(
    qref_by_zone: Mapping[str, float],
    tref_by_zone: Mapping[str, float],
    *,
    primary_zone: str,
) -> tuple[str, ...]:
    """Identify secondary zones meeting the direct 0.50 Tref threshold."""

    limited = []
    for zone, qref in qref_by_zone.items():
        if zone == primary_zone or zone not in tref_by_zone:
            continue
        if direct_ratio(qref, tref_by_zone[zone]) >= SECONDARY_DIRECT_RATIO_THRESHOLD:
            limited.append(str(zone))
    return tuple(sorted(limited))


__all__ = [
    "MAX_WEEKLY_MULTIPLIER",
    "DEFAULT_PLANNING_SETTINGS",
    "SECONDARY_DIRECT_RATIO_THRESHOLD",
    "SESSION_DOSE_RANGES",
    "WEEKLY_PHASE_MULTIPLIERS",
    "adjust_target_for_recovery",
    "direct_ratio",
    "limiting_secondary_zones",
    "session_dose_range",
    "weekly_target",
    "validate_planning_settings",
]
