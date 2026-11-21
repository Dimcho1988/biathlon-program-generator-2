"""Bounded activity discovery; detailed data stays available through scoped reads."""
from copy import deepcopy


def summarize(payload):
    fields = {"activity_ref", "local_date", "local_time", "name", "sport", "duration_min",
              "distance_m", "elevation_gain_m", "average_hr_bpm", "max_hr_bpm",
              "canonical_training_load", "quality_status", "quality_reason", "hr_coverage_percent",
              "zones", "hrmod_zones", "zone_visualization_source"}
    result = {key: deepcopy(payload[key]) for key in
              ("schema_version", "period_start", "period_end", "revision", "generation_id",
               "analysis_as_of", "activated_at", "includes_timeseries") if key in payload}
    result["activities"] = [{key: deepcopy(value) for key, value in row.items() if key in fields}
                            for row in payload.get("activities", [])]
    result["activity_count"] = len(result["activities"])
    result["detail_query"] = ("This is activity discovery, not the whole calendar. Use activity with activity_ref "
                              "for detail; activities view=calendar or section=wellness_days/weeks for other data. "
                              "Use history view=summary for period totals. No activity does not prove rest or no race.")
    return result


def compact_context(context):
    """Only reduce read results; never alter input contracts or write state."""
    if context.get("query_kind") != "activities" or not isinstance(context.get("data"), dict):
        return context
    data = context["data"]
    if "activities" not in data or context.get("section"):
        return context
    reduced = summarize(data)
    reduced["activity_count"] = data.get("activity_count", len(data["activities"]))
    # Omitted rows remain reachable by their original pagination selector.
    limit = 5
    rows = reduced["activities"]
    pages = deepcopy(context.get("pagination", []))
    if len(rows) > limit:
        previous = next((p for p in pages if p.get("section") == "activities"), {})
        offset = previous.get("offset", 0)
        pages = [p for p in pages if p.get("section") != "activities"]
        pages.append({"section": "activities", "total": previous.get("total", len(rows)),
                      "offset": offset, "returned": limit, "next_offset": offset + limit})
        reduced["activities"] = rows[:limit]
    return {**context, "data": reduced, "pagination": pages,
            "context_summary": "ACTIVITY_DISCOVERY; omitted details remain available through scoped reads"}
