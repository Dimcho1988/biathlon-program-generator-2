"""Use the planner's supported residual estimate without changing observations."""
from datetime import date, timedelta
from biathlon.planning_history import recovery_coverage
from . import planning_history_estimate
from .management_store import ManagementStore


def prepare(repository, alias, source, today, coverage):
    """Return the readiness ledger and a measured/estimated coverage contract.

    Only conserved, known recording gaps qualify. Calendar/provider omissions,
    absent recording duration and unconfigured profiles remain unavailable.
    """
    if coverage["complete"] or coverage["missing_days"] or not source.get("load_selection"):
        return source, coverage
    reader = (getattr(repository, "active_trainability_calendar", None)
              or getattr(repository, "active_activity_calendar", None))
    catalog = reader(alias, date.fromisoformat(source["period_start"]), date.fromisoformat(source["period_end"])) if callable(reader) else None
    # A repository without the pinned catalog cannot certify omitted providers.
    if catalog is None:
        return source, coverage
    calendar = catalog.get("activities", []) if isinstance(catalog, dict) else catalog
    calendar = planning_history_estimate.normalized_calendar(calendar)
    known_refs = {a.get("activity_ref") for a in source.get("activities", [])}
    omitted = [{**a, "date": a.get("local_date") or a.get("date")}
               for a in calendar if a.get("activity_ref") not in known_refs]
    if omitted:
        # Catalog identities can date an exclusion outside the recovery window.
        # This temporary coverage view never adds a measured dose or fabricates
        # a ledger for an omitted recording inside that window.
        coverage = recovery_coverage({**source, "activities": [*source.get("activities", []), *omitted]}, today)
    if (coverage["complete"] or coverage.get("unknown_seconds") is None
            or coverage["unknown_seconds"] <= 0 or coverage.get("unidentified_partial_activities")):
        return source, coverage
    if not (callable(getattr(repository, "planning_profile_rows", None))
            or callable(getattr(repository, "_request", None))):
        return source, coverage
    profile = ManagementStore(repository).profile(alias)
    if not profile["configured"]:
        return source, coverage
    estimated, evidence = planning_history_estimate.prepare(
        source, calendar, profile["profile"], today,
        required_coverage_start=today-timedelta(days=40),
        capacity_contexts_by_sport=(source.get("component_load_model") or {}).get("contexts_by_sport"))
    supported = evidence["supported"] and evidence["estimated"]
    quality = {**coverage, "readiness_available": bool(supported),
               "readiness_basis": "ESTIMATED_LOAD" if supported else "UNAVAILABLE",
               "estimated": bool(supported),
               "estimation": {k: evidence[k] for k in (
                   "version", "policy", "supported", "estimated_minutes", "estimated_activity_count",
                   "unresolved_activity_count", "missing_calendar_days")}}
    return (estimated if supported else source), quality
