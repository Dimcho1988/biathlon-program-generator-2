"""Display-only initial threshold from the already pinned activity catalog."""
from datetime import date, timedelta

from biathlon import initial_threshold


def _catalog_covers_processed_history(source, catalog):
    """Reconcile persisted global counts before trusting the daily calendar.

    A provider failure still produces daily buckets and increments the source
    counts, but can leave no catalog row or date to exclude. Missing HR alone
    is acceptable: its identifiable catalog row is counted normally.
    """
    quality = source.get("quality") or {}
    fields = ("processed_activities", "limited_activities", "excluded_activities")
    if not any(field in quality for field in fields):
        return True
    start, end = (initial_threshold._date(source.get(key)) for key in ("period_start", "period_end"))
    if start is None or end is None or start > end:
        return False
    records = {}
    for row in catalog:
        day = initial_threshold._date(row.get("local_date"))
        ref = row.get("activity_ref")
        if (day is not None and start <= day <= end and isinstance(ref, str) and ref.strip()
                and row.get("planned") is not True and row.get("completed") is not False):
            records[ref] = row
    counts = {"processed_activities": len(records),
              "limited_activities": sum(r.get("quality_status") == "limited" for r in records.values()),
              "excluded_activities": sum(r.get("quality_status") in {"excluded", "provider_missing"}
                                         for r in records.values())}
    return all(field not in quality or (type(quality[field]) is int and quality[field] >= 0
                                       and quality[field] == counts[field]) for field in fields)


def build(calendar, profile, today, sport, *, reliable_anchor=None, requested_days=None):
    """No additional activity reads; never make this estimate a curve or test.

    ``profile`` is the management PROFILE payload, not an athlete-settings
    default. ``reliable_anchor`` is explicitly selected by the caller after
    validating tests/index (``TESTS`` or ``INDEX``). Dates come from the pinned
    load ledger; a missing sensor does not itself make catalog coverage absent.
    """
    base = {"use_status": "UNAVAILABLE", "sport": sport, "used_for_zones": False}
    if reliable_anchor in {"TESTS", "INDEX"}:
        return {**initial_threshold._result("RELIABLE_INDIVIDUAL_ANCHOR_AVAILABLE"), **base,
                "use_status": "SUPERSEDED", "superseded_by": reliable_anchor}
    if type(today) is not date:
        return {**initial_threshold._result("INVALID_WINDOW"), **base}
    if requested_days is not None and (type(requested_days) is not int or not 7 <= requested_days <= 28):
        return {**initial_threshold._result("INVALID_WINDOW"), **base}
    years = (profile or {}).get("training_experience_years")
    if years is None:
        return {**initial_threshold._result("TRAINING_YEARS_REQUIRED"), **base}
    source = ((calendar or {}).get("snapshot_payload") or {}).get("load_history") or {}
    # Complete zero-load rows establish rest-day coverage. Missing rows do not.
    by_day = {}
    for row in source.get("daily", []):
        by_day.setdefault(row.get("date"), set()).add(row.get("zone"))
    # These buckets establish calendar coverage, not valid HR or known Q.
    # Legacy snapshots may omit the STR buckets even though the same pinned
    # catalog includes all strength sessions, whose durations are required below.
    required = {"Z1", "Z2", "Z3", "Z4", "Z5"}
    reliable = {key for key, zones in by_day.items() if required <= zones}
    catalog = [{**(row.get("catalog_payload") or {}),
                **{key: value for key, value in row.items() if key != "catalog_payload"}}
               for row in (calendar or {}).get("activities", [])]
    if not _catalog_covers_processed_history(source, catalog):
        return {**initial_threshold._result("INCOMPLETE_HISTORY_COVERAGE"), **base}
    provider_missing = [row for row in catalog if row.get("quality_status") == "provider_missing"]
    for row in provider_missing:
        reliable.discard(row.get("local_date"))
    days = 0
    for i in range(1, (requested_days or initial_threshold.PREFERRED_DAYS)+1):
        if (today-timedelta(days=i)).isoformat() not in reliable:
            break
        days = i
    if days < (requested_days or initial_threshold.MINIMUM_DAYS):
        return {**initial_threshold._result("INSUFFICIENT_HISTORY"), **base}
    start = today-timedelta(days=days)
    # Prefer an observed full recording duration; speed always uses explicit
    # raw distance and moving time from that same catalog row.
    loads = {row.get("activity_ref"): row for row in source.get("activities", [])}
    rows, duration_sources = [], set()
    for row in catalog:
        if not start.isoformat() <= str(row.get("local_date") or "") < today.isoformat():
            continue
        if row.get("planned") is True or row.get("completed") is False:
            continue
        summary = row.get("canonical_summary") or {}
        minutes = summary.get("duration_min")
        basis = "CANONICAL_DURATION"
        if not initial_threshold._number(minutes):
            minutes = (loads.get(row.get("activity_ref")) or {}).get("duration_min")
        if initial_threshold._number(minutes):
            duration = minutes*60
        else:
            duration, basis = row.get("recording_time_s"), "RECORDING_TIME"
        duration_sources.add(basis)
        rows.append({"id": row.get("activity_ref"), "sport": row.get("sport"),
                     "local_date": row.get("local_date"), "completed": True,
                     "training_duration_s": duration, "moving_time_s": row.get("moving_time_s"),
                     "distance_m": row.get("distance_m")})
    result = initial_threshold.from_history(rows, sport=sport, training_years=years,
        window_start=start, window_end=today, as_of_date=today, reliable_dates=reliable)
    return {**result, **base,
            "use_status": "THRESHOLD_ONLY" if result["status"] == "AVAILABLE" else "UNAVAILABLE",
            "duration_sources": sorted(duration_sources)}
