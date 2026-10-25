"""Presentation-only HR zone summaries, using the common load integrator.

Re-evaluate pinned channels on read. Never alter a stored run, HRmod values,
calibration evidence or the selected Q/E ledger.
"""
from collections.abc import Mapping

from biathlon import unified_load
from biathlon.sport_heart_rate import reference_offset


def synchronize_hr_zone_times(shadow, *, sport=None, duration_s=None):
    if not isinstance(shadow, Mapping):
        return shadow
    rows = shadow.get("timeseries")
    legacy = shadow.get("zone_summary")
    if not isinstance(rows, list) or not rows or not isinstance(legacy, list) or len(legacy) != 5:
        return shadow
    sport = sport or shadow.get("sport") or (
        "Ride" if (shadow.get("trainability_index") or {}).get("hr_reference_offset_bpm") == 7 else "Unknown")
    bounds = (shadow.get("trainability_index") or {}).get("zone_bounds_bpm")
    if bounds is None:
        by_zone = {r.get("zone_name"): r for r in legacy if isinstance(r, Mapping)}
        if set(by_zone) != set(unified_load.speed_equivalence.ZONES):
            return shadow
        offset = reference_offset(sport)
        lower = by_zone["Z1"].get("lower_bpm")
        edges = [lower] + [by_zone[z].get("upper_bpm") for z in unified_load.speed_equivalence.ZONES]
        if not all(unified_load.finite(v) for v in edges):
            return shadow
        bounds = [v+offset for v in edges]
    if (not isinstance(bounds, (tuple,list)) or len(bounds) != 6
            or not all(unified_load.finite(v) and v>0 for v in bounds)
            or any(a>=b for a,b in zip(bounds,bounds[1:]))):
        return shadow
    if not unified_load.finite(duration_s) or duration_s <= 0:
        duration_s = max((r.get("elapsed_s",0.) for r in rows if unified_load.finite(r.get("elapsed_s"))), default=0.)
    comparison = unified_load.hr_zone_times(shadow, bounds=bounds, sport=sport, duration_s=duration_s)
    return {**shadow, "zone_summary": comparison["rows"],
            "hr_zone_time_method": {k:v for k,v in comparison.items() if k != "rows"}}
