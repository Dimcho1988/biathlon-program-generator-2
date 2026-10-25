"""Describe recorded load provenance without changing Q, E or calibration."""
from copy import deepcopy
import math

from biathlon.speed_equivalence import supports_sport


def selection_metadata(activities):
    aerobic = [a for a in activities if supports_sport(a.get("sport")) and not a.get("strength_time_min")]
    ledgers = [a["load_source"] for a in aerobic
               if isinstance(a.get("load_source"), dict) and a["load_source"].get("version")
               and isinstance(a["load_source"].get("source_seconds"), dict)]
    if not ledgers:
        return None

    def total(key):
        return math.fsum(float(s.get(key, 0.)) for s in ledgers)

    def breakdown(key):
        if any(not isinstance(s.get(key), dict) for s in ledgers):
            return None
        names = sorted({k for s in ledgers for k in s[key]})
        return {k: math.fsum(s[key].get(k, 0.) for s in ledgers) for k in names}

    indicators = {s["leading_indicator"] for s in ledgers}
    return {"schema_version": "load-source-metadata-v1",
            "scope": "RECORDED_AEROBIC_ACTIVITIES_IN_PERIOD",
            "leading_indicator": next(iter(indicators)) if len(indicators) == 1 else "MIXED",
            "ledger_versions": sorted({s["version"] for s in ledgers}),
            "hr_coordinate_policies": sorted({s["hr_coordinate"] for s in ledgers if s.get("hr_coordinate")}),
            "activity_count": len(ledgers), "total_aerobic_activities": len(aerobic),
            **{k: total(k) for k in ("recorded_seconds", "active_seconds", "rest_seconds", "unknown_seconds", "fallback_seconds")},
            "source_seconds": breakdown("source_seconds"),
            "hr_source_seconds": breakdown("hr_source_seconds"),
            "speed_status_seconds": breakdown("speed_status_seconds"),
            "speed_unavailable_reasons_seconds": breakdown("speed_unavailable_reasons_seconds"),
            **{k: total(k) if all(k in s for s in ledgers) else None
               for k in ("unavailable_speed_hr_seconds", "estimated_descent_seconds")}}


def with_load_metadata(model, activities):
    """Keep the legacy adapter labelled honestly when no common ledger exists."""
    metadata = selection_metadata(activities)
    if metadata is None:
        return model
    out = deepcopy(model)
    out.update(effective_hr_source="unified_interval_selection",
               effective_hr_version=" / ".join(metadata["ledger_versions"]),
               load_selection=metadata)
    return out
