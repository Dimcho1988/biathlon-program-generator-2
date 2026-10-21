"""Read-only speed-equivalent plan projection with explicit comparison units.

This is built during plan generation, from the selected complete prescriptions.
It neither changes allocation nor reads sample streams. Retained execution is
accepted only with the identical speed-parameter version. Legacy HR Q targets
are deliberately not converted without a declared speed reference.
"""
from collections import defaultdict
import json
import math

from biathlon import planning_schedule, speed_equivalence

VERSION = "plan-speed-comparison-v1-2026-10-20"
SESSION_STORAGE_FORMAT = "SESSION_TOTALS_SHARED_PARAMETERS_V1"
SESSION_ZONE_VALUE_FIELDS = (
    "real_minutes", "covered_minutes", "equivalent_minutes", "baseline_equivalent_minutes",
    "normalized_q", "baseline_normalized_q", "equivalent_minutes_range", "baseline_equivalent_minutes_range")
BLOCK_ZONE_VALUE_FIELDS = ("zone", "real_seconds", "covered_seconds", "equivalent_minutes",
    "baseline_equivalent_minutes", "equivalent_minutes_range", "baseline_equivalent_minutes_range")


def _seconds(block):
    value = block.get("duration_s")
    if value is None and speed_equivalence.positive(block.get("duration_min")):
        value = block["duration_min"]*60
    return float(value) if speed_equivalence.positive(value) else 0.


def _segment(block):
    return {"seconds": _seconds(block), "speed_kmh": block.get("target_speed_kmh"),
            "start_type": block.get("start_type", "UNKNOWN")}


def _range(value, selected):
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(speed_equivalence.positive(v) for v in value) and value[0] <= value[1]):
        return tuple(float(v) for v in value)
    return (selected, selected) if speed_equivalence.positive(selected) else None


def _range_scenarios(block, parameters):
    """Only declared paired variants, never a Cartesian product of maxima.

    Source recipes couple increasing work amount to decreasing speed. Exact
    endpoints, reference crossings and stationary points bound E for that
    one-parameter family. An undeclared two-axis dependency stays unavailable.
    """
    velocity = block.get("target_speed_kmh")
    speeds = _range(block.get("target_speed_range_kmh"), velocity)
    times = _range(block.get("duration_range_seconds"), _seconds(block))
    if not speeds or not times:
        return None, "MISSING_VALID_SPEED_OR_TIME_RANGE"
    if (not speed_equivalence.positive(velocity) or velocity < speeds[0]-1e-8 or velocity > speeds[1]+1e-8
            or _seconds(block) < times[0]-1e-6 or _seconds(block) > times[1]+1e-6):
        return None, "RANGE_EXCLUDES_SELECTED_PRESCRIPTION"
    resolution = (block.get("method_library") or {}).get("resolution") or {}
    if "segments" in resolution:
        segments = resolution["segments"]
        resolution = segments[0] if len(segments) == 1 else {}
    unit = resolution.get("work_unit")
    amounts = _range(resolution.get("work_range"), None)
    if unit not in {"seconds", "meters"} or amounts is None:
        unit, amounts = "seconds", times
        if speeds[0] != speeds[1] and times[0] != times[1]:
            return None, "RANGE_DEPENDENCY_NOT_DECLARED"
    lo, hi = speeds
    # The same speed can have an approved duration range without speed coupling.
    if lo == hi:
        if unit == "meters":
            scenarios = [(amount/(lo/3.6), lo) for amount in amounts]
        else:
            scenarios = [(amount, lo) for amount in amounts]
        return scenarios + [(_seconds(block), velocity)], None
    slope = -(amounts[1]-amounts[0])/(hi-lo)
    intercept = amounts[0]-slope*hi
    velocities = {lo, hi, float(velocity)} if speed_equivalence.positive(velocity) else {lo, hi}
    for edge in parameters.get("classification_bounds_kmh") or []:
        if lo <= edge <= hi:
            velocities.add(edge)
            if edge > lo:
                velocities.add(math.nextafter(edge, -math.inf))
    for row in parameters["zones"]:
        if row["status"] != "AVAILABLE":
            continue
        ref = row["upper_weight"] if row["zone"] != "Z5" else 1.
        a = row["slope_per_kmh"]/ref
        b = (1-row["slope_per_kmh"]*row["low_kmh"])/ref
        # c=1 needs the same extrema treatment as the agreed settings.
        for aa, bb in ((a, b), (1/row["reference_speed_kmh"], 0.)):
            candidate = None
            if unit == "seconds" and slope*aa:
                candidate = -(slope*bb+intercept*aa)/(2*slope*aa)
            elif unit == "meters" and slope*aa and intercept*bb/(slope*aa) > 0:
                candidate = math.sqrt(intercept*bb/(slope*aa))
            if candidate is not None and lo < candidate < hi and row["low_kmh"] <= candidate <= row["high_kmh"]:
                velocities.add(candidate)
    result = []
    for speed in sorted(velocities):
        amount = slope*speed+intercept
        seconds = amount/(speed/3.6) if unit == "meters" else amount
        if speed_equivalence.positive(seconds):
            result.append((seconds, speed))
    result.append((_seconds(block), velocity))
    return result, None


def _block_comparison(block, parameters):
    result = speed_equivalence.integrate(parameters, [_segment(block)],
        fallback_indicator="EXISTING_PLANNED_HR_Q_RETAINED")
    selected_zones = [row["zone"] for row in result["zones"] if row["real_seconds"] > 0]
    if block.get("zone") and selected_zones and block["zone"] not in selected_zones:
        result["warnings"].append("PRESCRIBED_ZONE_DIFFERS_FROM_SPEED_ZONE")
    scenarios, reason = _range_scenarios(block, parameters)
    result["range_basis"] = "DECLARED_PAIRED_VARIANTS_AND_SELECTED_VALUE" if scenarios else None
    result["range_reason"] = reason
    for row in result["zones"]:
        row["equivalent_minutes_range"] = None
        row["baseline_equivalent_minutes_range"] = None
    if scenarios:
        alternatives = [speed_equivalence.integrate(parameters, [{"seconds": seconds, "speed_kmh": speed}])
                        for seconds, speed in scenarios]
        if any(alternative["status"] != "AVAILABLE" for alternative in alternatives):
            result["range_reason"] = "PARTIALLY_UNSUPPORTED_SPEED_RANGE"
            return result
        for i, row in enumerate(result["zones"]):
            for field in ("equivalent_minutes", "baseline_equivalent_minutes"):
                values = [alternative["zones"][i][field] for alternative in alternatives]
                # A partly unsupported range is not a numerical low of zero.
                if all(value is not None for value in values):
                    row[field+"_range"] = [min(values), max(values)]
    return result


def _aggregate(parameters, comparisons):
    comparisons = list(comparisons)
    result = speed_equivalence.aggregate(parameters, comparisons)
    result["fallback_indicator"] = "EXISTING_PLANNED_HR_Q_RETAINED"
    result["range_basis"] = "CONSERVATIVE_PER_BLOCK_ENVELOPE_NOT_JOINT_VARIANTS"
    result["range_is_jointly_executable"] = False
    if not comparisons:
        result["status"] = "NO_PLANNED_WORK"
        for row in result["zones"]:
            row["equivalent_minutes"] = row["baseline_equivalent_minutes"] = 0.
    for i, row in enumerate(result["zones"]):
        for field in ("equivalent_minutes_range", "baseline_equivalent_minutes_range"):
            ranges = [item["zones"][i].get(field) for item in comparisons]
            row[field] = ([sum(value[0] for value in ranges), sum(value[1] for value in ranges)]
                          if ranges and all(value is not None for value in ranges) else None)
    return result


def session_projection(session, parameters):
    """Expose selected work and preparation independently, preserving blocks."""
    categories = defaultdict(list)
    passive_seconds = 0.
    block_results = []
    for index, block in enumerate(session.get("blocks", [])):
        if block.get("passive_rest"):
            passive_seconds += _seconds(block)
            block_results.append({"block_index": index, "kind": block.get("kind"),
                "status": "PASSIVE_REST", "real_seconds": _seconds(block)})
            continue
        comparison = _block_comparison(block, parameters)
        categories[block.get("kind", "OTHER")].append(comparison)
        # Keep source-block evidence compact: settings and five boundary rows
        # belong to the session header, not to every repeated interval.
        block_results.append({"block_index": index, "kind": block.get("kind"),
            "prescribed_zone": block.get("zone"),
            "status": comparison["status"], "coverage_percent": comparison["coverage_percent"],
            "range_basis": comparison["range_basis"], "range_reason": comparison["range_reason"],
            "zones": [{key: row[key] for key in ("zone", "real_seconds", "covered_seconds",
                "equivalent_minutes", "baseline_equivalent_minutes", "equivalent_minutes_range",
                "baseline_equivalent_minutes_range")}
                for row in comparison["zones"] if row["real_seconds"] or
                any((row.get("equivalent_minutes_range") or [0, 0]))]})
    result = _aggregate(parameters, [item for group in categories.values() for item in group])
    # The session header owns the parameter version and complete five-zone
    # anchors. Repeating them in every preparation/work/rest subtotal makes a
    # bounded stored plan grow by hundreds of kilobytes without new evidence.
    parts = {}
    for kind, values in categories.items():
        subtotal = _aggregate(parameters, values)
        parts[kind] = {key: subtotal[key] for key in (
            "status", "recorded_minutes", "classified_minutes", "coverage_percent", "warnings")}
        parts[kind]["zones"] = [{key: row[key] for key in (
            "zone", "real_minutes", "covered_minutes", "equivalent_minutes",
            "baseline_equivalent_minutes", "equivalent_minutes_range", "baseline_equivalent_minutes_range")}
            for row in subtotal["zones"] if row["real_seconds"] or
            any((row.get("equivalent_minutes_range") or [0, 0]))]
    # Identical repeated intervals retain all source indices and one exact
    # chosen value/range. This is lossless, unlike dropping interval evidence.
    repeated_blocks = {}
    for block in block_results:
        index = block.pop("block_index")
        identity = json.dumps(block, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if identity in repeated_blocks:
            repeated_blocks[identity]["block_indices"].append(index)
        else:
            repeated_blocks[identity] = {"block_indices": [index], **block}
    result.update(projection_version=VERSION, selected_variant=True, dose_modified=False,
                  passive_rest_minutes=passive_seconds/60,
                  parts=parts, blocks=list(repeated_blocks.values()))
    return result


def _actual_projection(parameters, activities, *, source_available=True):
    if parameters.get("sport") not in speed_equivalence.RUN_SPORTS:
        return {"status": "INCOMPLETE", "comparison": None, "activity_count": len(activities),
                "available_activity_count": 0, "missing_comparison_count": 0,
                "different_version_count": 0, "reason": "RUN_ONLY"}
    matched, missing, conflicting = [], [], []
    for activity in activities:
        comparison = activity.get("speed_equivalence_comparison")
        if not comparison:
            missing.append(activity.get("activity_ref"))
        elif comparison.get("parameters_version") != parameters["parameters_version"]:
            conflicting.append(activity.get("activity_ref"))
        else:
            matched.append(comparison)
    comparison = speed_equivalence.aggregate(parameters, matched) if matched else None
    partial = comparison is not None and comparison["status"] != "AVAILABLE"
    return {"status": "INCOMPLETE" if not source_available or missing or conflicting or partial else "AVAILABLE" if activities else "NO_RECORDED_ACTIVITIES",
            "comparison": comparison, "activity_count": len(activities),
            "available_activity_count": len(matched), "missing_comparison_count": len(missing),
            "different_version_count": len(conflicting),
            "reason": "EXECUTION_SOURCE_UNAVAILABLE" if not source_available else
                      "EXECUTION_COMPARISON_RECOMPUTATION_REQUIRED" if missing or conflicting else
                      "PARTIAL_EXECUTION_SPEED_COVERAGE" if partial else None}


def _stored_session_projection(comparison, shared):
    """Store quantities once; the sport report retains common model evidence.

    The client joins only an identical sport/parameter version. Old full
    comparisons remain readable; this representation changes no arithmetic.
    """
    result = {key: comparison[key] for key in (
        "parameters_version", "sport", "status", "recorded_minutes", "classified_minutes",
        "excluded_minutes", "outside_range_minutes", "uncovered_minutes", "coverage_percent",
        "start_types", "passive_rest_minutes")}
    result["storage_format"] = SESSION_STORAGE_FORMAT
    result["warning_indices"] = [shared["warnings"].index(warning) for warning in comparison["warnings"]]
    # Five ordered rows Z1..Z5. The storage-format version fixes these columns;
    # numerical nulls and zeroes are preserved exactly, never inferred.
    result["zone_values"] = [[row[key] for key in SESSION_ZONE_VALUE_FIELDS] for row in comparison["zones"]]
    result["parts"] = {kind: {**{key: value for key, value in part.items() if key != "warnings"},
        "warning_indices": [comparison["warnings"].index(warning) for warning in part["warnings"]]}
        for kind, part in comparison["parts"].items()}
    result["blocks"] = [{**{key: value for key, value in block.items() if key != "zones"},
        **({"zone_values": [[row[key] for key in BLOCK_ZONE_VALUE_FIELDS] for row in block["zones"]]}
           if "zones" in block else {})} for block in comparison["blocks"]]
    return result


def _targets(parameters, planned, actual, targets=None):
    rows = []
    actual_rows = {r["zone"]: r for r in ((actual.get("comparison") or {}).get("zones") or [])}
    for planned_row in planned["zones"]:
        zone = planned_row["zone"]
        target = (targets or {}).get(zone) or {}
        reason = ("LEGACY_HR_TARGET_NOT_CONVERTED" if not target else
                  "TARGET_METRIC_VERSION_MISMATCH" if target.get("parameters_version") != parameters["parameters_version"] else
                  "TARGET_UNIT_MISMATCH" if target.get("unit") != "EQUIVALENT_MINUTES_AT_ZONE_REFERENCE" else
                  "INVALID_TARGET" if not isinstance(target.get("equivalent_minutes"), (int, float))
                  or isinstance(target.get("equivalent_minutes"), bool) or not math.isfinite(target["equivalent_minutes"])
                  or target["equivalent_minutes"] < 0 else None)
        goal = target.get("equivalent_minutes") if reason is None else None
        actual_row = actual_rows.get(zone, {})
        done_known = actual["status"] in {"AVAILABLE", "NO_RECORDED_ACTIVITIES"}
        done = (0. if actual["status"] == "NO_RECORDED_ACTIVITIES" else actual_row.get("equivalent_minutes")) if done_known else None
        baseline_done = (0. if actual["status"] == "NO_RECORDED_ACTIVITIES" else actual_row.get("baseline_equivalent_minutes")) if done_known else None
        chosen, baseline = planned_row["equivalent_minutes"], planned_row["baseline_equivalent_minutes"]
        plan_complete = planned["status"] in {"AVAILABLE", "NO_PLANNED_WORK"}
        enough = goal is not None and done is not None and chosen is not None and plan_complete
        baseline_enough = goal is not None and baseline_done is not None and baseline is not None and plan_complete
        rows.append({"zone": zone, "unit": "EQUIVALENT_MINUTES_AT_ZONE_REFERENCE",
            "reference_speed_kmh": planned_row["reference_speed_kmh"],
            "target_equivalent_minutes": goal, "planned_equivalent_minutes": chosen,
            "actual_equivalent_minutes": done,
            "remaining_equivalent_minutes": max(0., goal-done-chosen) if enough else None,
            "excess_equivalent_minutes": max(0., done+chosen-goal) if enough else None,
            "baseline_planned_equivalent_minutes": baseline, "baseline_actual_equivalent_minutes": baseline_done,
            "baseline_remaining_equivalent_minutes": max(0., goal-baseline_done-baseline) if baseline_enough else None,
            "target_attainment_percent": 100*(done+chosen)/goal if enough and goal > 0 else None,
            "baseline_target_attainment_percent": 100*(baseline_done+baseline)/goal if baseline_enough and goal > 0 else None,
            "reason": reason or actual.get("reason") or ("PARTIAL_SPEED_COVERAGE" if not plan_complete else None)})
    return rows


def build(days, contexts, *, source=None, start_date=None, end_date=None, speed_targets_by_sport=None):
    """Build per-sport ledgers without adding different sport/zone references."""
    source = source or {}
    execution_source_available = (isinstance(source.get("activities"), list)
                                  and all(isinstance(activity, dict) for activity in source["activities"]))
    grouped = defaultdict(list)
    for day in days:
        for session in planning_schedule.day_sessions(day):
            grouped[session.get("sport")].append(session)
    activities = [activity for activity in (source["activities"] if execution_source_available else [])
                  if (not start_date or activity.get("date", "") >= start_date)
                  and (not end_date or activity.get("date", "") <= end_date)]
    for activity in activities:
        grouped.setdefault(activity.get("sport"), [])
    reports = []
    for sport, sessions in grouped.items():
        parameters = (contexts.get(sport) or {}).get("comparison_parameters")
        if parameters is None:
            parameters = speed_equivalence.prepare(sport=sport)
        comparisons = []
        for session in sessions:
            comparison = session_projection(session, parameters)
            comparisons.append(comparison)
        planned = _aggregate(parameters, comparisons)
        for session, comparison in zip(sessions, comparisons):
            session["speed_equivalence_comparison"] = _stored_session_projection(comparison, planned)
        actual = _actual_projection(parameters, [a for a in activities if a.get("sport") == sport],
                                    source_available=execution_source_available)
        reports.append({"sport": sport, "parameters_version": parameters["parameters_version"],
                        "planned": planned, "actual": actual,
                        "targets": _targets(parameters, planned, actual, (speed_targets_by_sport or {}).get(sport))})
    return {"version": VERSION, "role": "COMPARISON_ONLY", "authoritative_load_changed": False,
            "start_date": start_date, "end_date": end_date,
            "target_policy": "EXPLICIT_SAME_SPEED_METRIC_AND_PARAMETER_VERSION_ONLY",
            "normalization_policy": "NO_SPEED_TREF_MIGRATION_NO_NORMALIZED_Q",
            "sports": reports,
            "note": "Показателят е сравнителен. Пулсовите цели и съществуващите 7/40 и Recovery остават водещи; несъпоставими цели и непокрито изпълнение не се отчитат като нула."}
