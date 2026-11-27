"""Planning identity excludes only the audited, unused daily step count.

Keep the stored snapshot intact for display and energy analysis. Unknown fields
remain inputs, so adding a new scientific observation cannot silently reuse a
plan. Mirror wellness_for_planning in the private database queue predicate.
"""

VERSION = "planning-inputs-v1-without-step-count"


def wellness_for_planning(calendar):
    if not isinstance(calendar, list):
        return calendar
    result = []
    for row in calendar:
        if isinstance(row, dict) and isinstance(row.get("metrics"), dict):
            metrics = {k: v for k, v in row["metrics"].items() if k != "steps"}
            # The importer emits date/metrics rows only when a value exists.
            # A newly added steps-only day carries no planning observation.
            if not metrics and set(row) == {"date", "metrics"}:
                continue
            row = {**row, "metrics": metrics}
        result.append(row)
    return result


def for_planning(snapshot):
    if not isinstance(snapshot, dict) or "wellness_calendar" not in snapshot:
        return snapshot
    return {**snapshot, "wellness_calendar": wellness_for_planning(snapshot["wellness_calendar"])}
