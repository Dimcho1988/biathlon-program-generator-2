"""One direct-to-effective load rule for recorded and planned training.

Q and Tmax are both expressed in equivalent minutes at the zone reference.
Tmax is continuous capacity from the speed–time curve, or an expert continuous
capacity when no usable curve is available. Historical weekly Tref is never a
capacity input. Received load cannot itself spill into another component.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .constants import AEROBIC_COMPONENTS, COMPONENTS
from .hr_speed import TMAX_RANGES_S

VERSION = "component-load-adjacent-tmax-v1"
EXPERT_CAPACITY_SOURCE = "EXPERT_CONTINUOUS_TMAX"
CURVE_CAPACITY_SOURCE = "SPEED_TIME_CURVE"


def expert_zone_tmax_minutes() -> dict[str, float]:
    """Existing expert upper-zone-edge midpoints, not historical load priors.

    Z5 equivalent minutes use the common Z4/Z5 boundary as their reference;
    therefore Z5 shares that boundary capacity, not an invented weekly limit.
    """
    values = {zone: sum(bounds) / 120.0 for zone, bounds in TMAX_RANGES_S.items()}
    values["Z5"] = values["Z4"]
    return values


def resolve_zone_tmax(
    zone_tmax_minutes: Mapping[str, float] | None = None,
    capacity_sources: Mapping[str, str] | None = None,
) -> tuple[dict[str, float], dict[str, str]]:
    """Use expert capacity only for unspecified zones; reject corrupt inputs.

    An explicit invalid curve value is a model/data error. Replacing it with an
    expert value would disguise a failed individual capacity calculation.
    """
    capacities = expert_zone_tmax_minutes()
    sources = {zone: EXPERT_CAPACITY_SOURCE for zone in AEROBIC_COMPONENTS}
    for zone in AEROBIC_COMPONENTS:
        if zone_tmax_minutes is None or zone not in zone_tmax_minutes:
            continue
        raw = zone_tmax_minutes[zone]
        if isinstance(raw, bool):
            raise ValueError(f"{zone} Tmax must be finite and positive")
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{zone} Tmax must be finite and positive") from exc
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{zone} Tmax must be finite and positive")
        capacities[zone] = value
        sources[zone] = str((capacity_sources or {}).get(zone) or CURVE_CAPACITY_SOURCE)
    return capacities, sources


def calculate_component_load(
    direct_q: Mapping[str, float],
    zone_tmax_minutes: Mapping[str, float] | None = None,
    *,
    capacity_sources: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Apply approved neighboring-zone percentages to the entire direct Q.

    Below 50% of capacity: none; 50–80% inclusive: up 20%, down 10%;
    above 80%: up 30%, down 20%. All donors are evaluated from the original
    direct vector. STR neither donates nor receives aerobic spillover.
    """
    capacities, sources = resolve_zone_tmax(zone_tmax_minutes, capacity_sources)
    direct: dict[str, float] = {}
    for component in COMPONENTS:
        value = float(direct_q.get(component, 0.0))
        if not math.isfinite(value):
            raise ValueError(f"{component} direct load must be finite")
        direct[component] = max(0.0, value)
    ratios = {zone: direct[zone] / capacities[zone] for zone in AEROBIC_COMPONENTS}
    up_out = {component: 0.0 for component in COMPONENTS}
    down_out = dict(up_out)
    received = dict(up_out)
    up_fraction = dict(up_out)
    down_fraction = dict(up_out)
    for index, zone in enumerate(AEROBIC_COMPONENTS):
        ratio = ratios[zone]
        if ratio < 0.50:
            continue
        up, down = (0.30, 0.20) if ratio > 0.80 else (0.20, 0.10)
        up_fraction[zone], down_fraction[zone] = up, down
        if index + 1 < len(AEROBIC_COMPONENTS):
            up_out[zone] = up * direct[zone]
            received[AEROBIC_COMPONENTS[index + 1]] += up_out[zone]
        if index:
            down_out[zone] = down * direct[zone]
            received[AEROBIC_COMPONENTS[index - 1]] += down_out[zone]
    return {
        "version": VERSION,
        "direct": direct,
        "effective": {component: direct[component] + received[component] for component in COMPONENTS},
        "capacity_minutes": capacities,
        "capacity_sources": sources,
        "direct_ratio": ratios,
        "spill_up_out": up_out,
        "spill_down_out": down_out,
        "spill_received": received,
        "spill_up_fraction": up_fraction,
        "spill_down_fraction": down_fraction,
        "spill_basis": "ENTIRE_DIRECT_EQUIVALENT_LOAD",
        "capacity_basis": "CONTINUOUS_TMAX_AT_ZONE_EQUIVALENCE_REFERENCE",
    }
