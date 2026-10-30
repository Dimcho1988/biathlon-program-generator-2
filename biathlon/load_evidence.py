"""One activity-level contract for recorded load, estimates and missing data.

An HR-only sport can use the same conserved ledger as a speed-capable sport.
Old Q in a new snapshot is not silently relabelled: it must be reconstructed
from pinned samples before it can certify the common reference convention.
"""
from . import speed_equivalence
from .unified_load import finite, recorded_coverage

VERSION = "activity-load-evidence-v1"


def is_strength(activity):
    minutes = activity.get("strength_time_min")
    return activity.get("sport") == "WeightTraining" or (finite(minutes) and minutes > 0)


def supports_recording(sport):
    return isinstance(sport, str) and bool(sport.strip()) and sport not in {
        "WeightTraining", "Unknown", "Активност"}


def requires_recording(activity, *, unified=False):
    source = activity.get("load_source") or {}
    return unified or not isinstance(source, dict) or any(k in source for k in (
        "recorded_seconds", "active_seconds", "rest_seconds", "unknown_seconds", "equivalence_version"))


def assess(activity, *, unified=False):
    """Validate the original recording, never the separately estimated Q."""
    status = activity.get("quality_status", "valid")
    result = {"basis": "UNAVAILABLE", "usable": False, "complete": False, "recording_complete": False,
              "ledger": None, "reason": None}
    if status == "provider_missing":
        return {**result, "reason": "PROVIDER_RECORD_MISSING"}
    if is_strength(activity):
        return {**result, "basis": "STRENGTH", "usable": status == "valid",
                "recording_complete": status == "valid",
                "complete": status == "valid", "reason": None if status == "valid" else "STRENGTH_LOAD_INCOMPLETE"}
    original = {**activity, "zones": [{**r, "raw_time_min": r.get("planning_measured_time_min")}
                for r in activity.get("zones", [])]} if activity.get("planning_estimated") else activity
    source = activity.get("load_source") or {}
    if not isinstance(source, dict):
        return {**result, "reason": "INVALID_RECORDING_LEDGER"}
    has_ledger = requires_recording(activity, unified=unified)
    zones = original.get("zones", [])
    q_complete = all(finite(r.get("equivalent_time_min")) and r["equivalent_time_min"] >= 0 for r in zones)
    ledger = recorded_coverage(original)
    if ledger is not None:
        if source.get("equivalence_version", speed_equivalence.VERSION) != speed_equivalence.VERSION:
            return {**result, "reason": "Q_REANALYSIS_REQUIRED"}
        complete = q_complete and ledger["unknown_seconds"] <= 1e-6
        return {**result, "basis": "RECORDED_HR_SPEED", "usable": True, "complete": complete,
                "recording_complete": ledger["unknown_seconds"] <= 1e-6,
                "ledger": ledger, "reason": "ACTIVITY_Q_INCOMPLETE" if not q_complete else
                None if complete else "UNKNOWN_INTENSITY_INTERVALS"}
    if has_ledger:
        return {**result, "reason": "INVALID_RECORDING_LEDGER" if source else "Q_REANALYSIS_REQUIRED"}
    # The explicitly legacy path remains available to old adapters. It cannot
    # certify Q inside a common-reference snapshot or repair a broken ledger.
    if status != "valid" or activity.get("planning_estimated"):
        return {**result, "reason": "LEGACY_HR_INCOMPLETE"}
    if (len(zones) != len(speed_equivalence.ZONES)
            or {r.get("zone") for r in zones} != set(speed_equivalence.ZONES)
            or any(not finite(r.get("raw_time_min")) or r["raw_time_min"] < 0 for r in zones)):
        return {**result, "reason": "ACTIVITY_Q_INCOMPLETE"}
    # A missing component Q must not erase valid observations for other
    # components. Whole-activity consumers still require complete evidence.
    return {**result, "basis": "LEGACY_HR", "usable": True, "complete": q_complete,
            "recording_complete": True,
            "reason": None if q_complete else "ACTIVITY_Q_INCOMPLETE"}


def issue(activity, reason):
    return {"activity_ref": activity.get("activity_ref"), "date": activity.get("date") or activity.get("local_date"),
            "sport": activity.get("sport"), "reason": reason}
