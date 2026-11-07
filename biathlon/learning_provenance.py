"""Keep unvalidated intensity estimates out of measured learning evidence.

The activity ledger remains complete for planning and accounting. Eligibility
here concerns fitting or validating other models, not recording a workout or
retaining the athlete's original subjective report.
"""
from collections.abc import Mapping


def intensity_provenance(activity):
    evidence = activity.get("load_source") or {}
    if isinstance(evidence, Mapping) and any(key in evidence for key in
            ("initial_heuristic", "calibration_source", "speed_role")):
        return evidence
    summary = activity.get("canonical_summary") or {}
    evidence = summary.get("intensity_provenance") if isinstance(summary, Mapping) else None
    return evidence if isinstance(evidence, Mapping) else {}


def initial_heuristic_load(activity):
    """Use explicit provenance; generic calibration_eligible=False is universal."""
    evidence = intensity_provenance(activity)
    return (isinstance(evidence, Mapping) and
            (evidence.get("initial_heuristic") is True
             or evidence.get("calibration_source") == "INITIAL_HEURISTIC"))


def heuristic_load_dates(source):
    return {activity.get("date") or activity.get("local_date")
            for activity in source.get("activities", ())
            if initial_heuristic_load(activity)} - {None}


def eligible_load_rows(source, rows):
    """Exclude whole affected dates instead of inventing zero/partial daily E."""
    excluded = heuristic_load_dates(source)
    return [row for row in rows if row.get("date") not in excluded]
