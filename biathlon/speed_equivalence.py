"""Shared multisport HR/Vflat equivalent-time kernel; not measured energy.

The settings are expert weights at each zone's upper edge. They are not the
legacy HR-equivalence slopes. All speeds are km/h, all internal time is seconds.
Only dimensionless E/Tref can be aggregated across different zone references.
"""
from bisect import bisect_right
from copy import deepcopy
from hashlib import sha256
import json
import math

VERSION = "common-zone-equivalence-v2-2026-10-24"
INTEGRATION_VERSION = "common-zone-integration-v3-relative-rest"
SETTINGS_VERSION = "expert-multisport-weights-2026-10-24-v2"
ZONES = tuple(f"Z{i}" for i in range(1, 6))
COEFFICIENTS = dict(zip(ZONES, (1., 1., 1., 1.2, 1.5)))
RUN_SPORTS = {"Run", "VirtualRun"}


def supports_sport(sport):
    from .sport_heart_rate import is_cycling, sport_key
    return is_cycling(sport) or sport_key(sport) in {
        "run", "running", "trailrun", "trailrunning", "virtualrun", "trackrun",
        "trackrunning", "roadrun", "roadrunning", "crosscountryrun", "crosscountryrunning",
        "treadmillrun", "treadmillrunning", "jogging", "nordicski", "rollerski",
        "rollerskiing", "crosscountryskiing", "crosscountryski", "skating"}


def relative_coefficient(value, low, high, zone, *, boundary_ratio=None, expert_coefficient=None):
    """One kernel for either coordinate, with a common speed-boundary ratio.

    HR-only cold starts use their own boundary ratio explicitly; no absolute
    speed is invented. When a speed profile exists, HR receives its ratio.
    """
    if not all(positive(v) for v in (value, low, high)) or high <= low or zone not in COEFFICIENTS:
        return None
    if value < low or value > high:
        return None
    ratio = boundary_ratio if positive(boundary_ratio) else high/low
    upper_weight = (expert_coefficient if positive(expert_coefficient) else COEFFICIENTS[zone])*ratio
    fraction = (value-low)/(high-low)
    weight = 1.+fraction*(upper_weight-1.)
    return weight if zone == "Z5" else weight/upper_weight


def evaluate_hr(parameters, hr_bpm, *, bounds=None, offset_bpm=0., zone=None):
    """Evaluate measured HR against the same profile and kernel as Vflat."""
    edges = bounds
    if edges is None:
        return {"status": "UNAVAILABLE", "reason": "HR_PROFILE_REQUIRED"}
    value = hr_bpm+offset_bpm if positive(hr_bpm) else None
    if value is None:
        return {"status": "UNAVAILABLE", "reason": "INVALID_HR"}
    if value < edges[0]:
        return {"status": "REST", "coefficient": 0., "zone": None}
    index = ZONES.index(zone) if zone in ZONES else min(4, bisect_right(edges[1:5], value))
    zone = ZONES[index]
    value = min(value, edges[-1])
    row = next((r for r in (parameters or {}).get("zones", ()) if r["zone"] == zone), {})
    ratio = row.get("boundary_ratio") if row.get("status") == "AVAILABLE" else None
    coefficient = relative_coefficient(value, edges[index], edges[index+1], zone, boundary_ratio=ratio,
        expert_coefficient=row.get("coefficient") if ratio is not None else None)
    return {"status": "AVAILABLE" if coefficient is not None else "UNAVAILABLE",
            "zone": zone, "coefficient": coefficient,
            "basis": "COMMON_SPEED_PROFILE" if ratio is not None else "HR_ONLY_RELATIVE_BOUNDS"}


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _safe_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _safe_json(v) for k,v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe_json(v) for v in value]
    return value


def prepare(profile=None, *, sport="Run", tref_minutes=None, tref_version=None, tref_reference_speeds=None, curve_version=None, settings=None):
    """Prepare five small affine coefficients once per boundary/settings version.

    Existing zone edges are retained. A zero Z1 lower edge stays unsupported;
    a missing V(10 s) is never replaced with a GPS peak or a longer-duration test.
    Tref must be an explicitly migrated normalization reference in these units;
    the existing continuous Tmax or HR-history Tref is NOT silently substituted.
    """
    profile = profile or {}
    if tref_minutes is not None and (not tref_version or not tref_reference_speeds):
        raise ValueError("Speed-compatible Tref version and reference speeds are required")
    chosen = deepcopy(settings) if settings else {
        "version": SETTINGS_VERSION, "origin": "EXPERT_DEFINED", "coefficients": COEFFICIENTS.copy()}
    if not chosen.get("version") or chosen.get("origin") != "EXPERT_DEFINED":
        raise ValueError("Versioned expert-origin coefficient settings are required")
    coefficients = chosen.get("coefficients") or {}
    if set(coefficients) != set(ZONES) or not all(positive(v) for v in coefficients.values()):
        raise ValueError("Exactly five positive finite coefficients are required")
    rows = {row.get("zone"): row for row in profile.get("zones", ())}
    output = []
    warnings = ["EXPERT_WEIGHTS_NOT_MEASURED_ENERGY", "LOCAL_REFERENCES_NOT_CONTINUOUS_ENERGY_CURVE",
                "SHARED_WITH_SELECTED_LOAD_LEDGER"]
    if not tref_minutes:
        warnings.append("SPEED_TREF_NOT_MIGRATED")
    v10_evidence = profile.get("v10") or {}
    if v10_evidence.get("extrapolated") is True:
        warnings.append("V10_EXTRAPOLATED_NOT_MEASURED")
    elif v10_evidence.get("status") != "AVAILABLE":
        warnings.append(v10_evidence.get("reason") or "V10_REQUIRED")
    for zone in ZONES:
        row = rows.get(zone, {})
        low, high = row.get("low_kmh"), row.get("high_kmh")
        v10 = profile.get("v10") or {}
        if zone == "Z5":
            high = v10.get("speed_kmh") if v10.get("status") == "AVAILABLE" else None
        # Keep invalid inputs out of both JSON and arithmetic; the explicit
        # reason below preserves why the zone cannot be scored.
        low = float(low) if isinstance(low, (int, float)) and not isinstance(low, bool) and math.isfinite(low) and low >= 0 else None
        high = float(high) if isinstance(high, (int, float)) and not isinstance(high, bool) and math.isfinite(high) and high >= 0 else None
        c = float(coefficients[zone])
        reason = ("UNSUPPORTED_SPORT" if not supports_sport(sport) else
                  "SPEED_ZONE_PROFILE_UNAVAILABLE" if profile.get("status") != "AVAILABLE" else
                  "NONPOSITIVE_LOWER_BOUND" if not positive(low) else
                  "V10_REQUIRED" if zone == "Z5" and not positive(high) else
                  "UPPER_BOUND_REQUIRED" if not positive(high) else
                  "NONINCREASING_BOUNDS" if high <= low else None)
        upper_weight = c * high / low if reason is None else None
        if reason is None and upper_weight < 1:
            reason = "NONMONOTONE_WEIGHT_SETTINGS"
        width = high-low if reason is None else None
        if width is not None and width <= max(abs(low), abs(high))*1e-12:
            reason = "NUMERICALLY_DEGENERATE_RANGE"
        if reason is None and width / low < .01:
            warnings.append(f"NARROW_RANGE_SENSITIVITY_{zone}")
        tref = (tref_minutes or {}).get(zone)
        if not positive(tref):
            tref = None
        reference = low if zone == "Z5" else high
        if tref is not None and (not positive(reference) or
                not positive((tref_reference_speeds or {}).get(zone)) or
                not math.isclose(reference, tref_reference_speeds[zone], rel_tol=1e-10)):
            raise ValueError("Tref reference speed does not match the zone-equivalent time")
        output.append({"zone": zone, "status": "AVAILABLE" if reason is None else "UNAVAILABLE",
                       "reason": reason, "low_kmh": low, "high_kmh": high,
                       "coefficient": c, "boundary_ratio": high/low if reason is None else None,
                       "upper_weight": upper_weight if reason is None else None,
                       "slope_per_kmh": (upper_weight-1)/width if reason is None else None,
                       "reference_speed_kmh": reference if positive(reference) else None,
                       "reference_edge": "LOWER" if zone == "Z5" else "UPPER",
                       "tref_minutes": tref,
                       "boundary_source": row.get("boundary_source", row.get("source")),
                       "v10_provenance": _safe_json(v10) if zone == "Z5" else None})
    boundaries = [row["high_kmh"] for row in output[:4]]
    classification_valid = all(positive(v) for v in boundaries) and all(a < b for a,b in zip(boundaries,boundaries[1:]))
    coherent = all(a["high_kmh"] is not None and b["low_kmh"] is not None and
                   math.isclose(a["high_kmh"], b["low_kmh"], rel_tol=1e-10, abs_tol=1e-12)
                   for a,b in zip(output, output[1:]))
    if not coherent:
        classification_valid = False
        warnings.append("INCOHERENT_ZONE_BOUNDARIES")
    if not classification_valid:
        warnings.append("NONMONOTONE_OR_MISSING_ZONE_BOUNDARIES")
    initial_heuristic = bool(profile.get("initial_heuristic") or
        str(profile.get("source", "")).startswith("INITIAL_HEURISTIC"))
    result = {"version": VERSION, "role": "COMPARISON_ONLY", "sport": sport,
              "speed_role": profile.get("speed_role") or "FLAT_EQUIVALENT",
              "initial_heuristic": initial_heuristic,
              "calibration_source": "INITIAL_HEURISTIC" if initial_heuristic else profile.get("source"),
              "settings": chosen, "curve_version": curve_version, "tref_version": tref_version, "zones": output,
              "classification_bounds_kmh": boundaries if classification_valid else None,
              "v10": _safe_json(profile.get("v10")), "warnings": warnings,
              "equivalent_time_unit": "SECONDS_AT_EACH_ZONE_REFERENCE",
              "normalized_q_unit": "DIMENSIONLESS_EQUIVALENT_TIME_DIVIDED_BY_TREF",
              "reference_policy": "CURRENT_INDIVIDUAL_CURVE_RETROSPECTIVE_COMPARISON"}
    result["parameters_version"] = sha256(json.dumps(result, sort_keys=True, allow_nan=False,
                                                    separators=(",", ":")).encode()).hexdigest()
    return result


def evaluate(parameters, speed_kmh, *, zone=None):
    """Return k and the c=1 baseline; shared edges belong to the higher zone.

    An explicit zone is for within-zone comparisons at both endpoints. Actual
    samples omit it, ensuring that a boundary sample is integrated once only.
    """
    if not positive(speed_kmh):
        return {"status": "UNAVAILABLE", "reason": "INVALID_SPEED", "zone": zone}
    if not supports_sport(parameters.get("sport")):
        return {"status": "UNAVAILABLE", "reason": "UNSUPPORTED_SPORT", "zone": zone}
    if zone is None:
        boundaries = parameters.get("classification_bounds_kmh")
        if not boundaries:
            return {"status": "UNAVAILABLE", "reason": "INVALID_ZONE_BOUNDARIES", "zone": None}
        zone = ZONES[bisect_right(boundaries, speed_kmh)]
        first = parameters["zones"][0].get("low_kmh")
        if positive(first) and speed_kmh < first:
            return {"status": "REST", "reason": "BELOW_RELATIVE_SPEED_FLOOR", "zone": None, "coefficient": 0.}
    row = next((r for r in parameters["zones"] if r["zone"] == zone), None)
    if row is None:
        return {"status": "UNAVAILABLE", "reason": "INVALID_ZONE", "zone": None}
    if row["status"] != "AVAILABLE":
        return {"status": "UNAVAILABLE", "reason": row["reason"], "zone": zone}
    low, high = row["low_kmh"], row["high_kmh"]
    if not low <= speed_kmh <= high:
        return {"status": "UNAVAILABLE", "reason": "ABOVE_V10_UPDATE_ANCHOR" if zone == "Z5" and speed_kmh > high
                else "OUTSIDE_ZONE_RANGE", "zone": zone}
    weight = 1 + (speed_kmh-low)*row["slope_per_kmh"]
    k = weight if zone == "Z5" else weight/row["upper_weight"]
    baseline = speed_kmh/(low if zone == "Z5" else high)
    return {"status": "AVAILABLE", "reason": None, "zone": zone, "weight": weight,
            "coefficient": k, "baseline_coefficient": baseline,
            "reference_speed_kmh": row["reference_speed_kmh"]}


def equivalent_seconds(parameters, seconds, speed_kmh, reference_speed_kmh, *, zone):
    """t * W(v)/W(v_reference), strictly within the same valid zone."""
    if not positive(seconds):
        raise ValueError("Positive work duration required")
    actual, reference = evaluate(parameters, speed_kmh, zone=zone), evaluate(parameters, reference_speed_kmh, zone=zone)
    if actual["status"] != "AVAILABLE" or reference["status"] != "AVAILABLE":
        return None
    return seconds*actual["weight"]/reference["weight"]


def integrate(parameters, segments, *, recorded_seconds=None, warnings=(), fallback_indicator="CANONICAL_HR_Q_RETAINED"):
    """Integrate valid, nonoverlapping segments for plan or measured execution.

    Each segment supplies seconds, speed_kmh, optional exclusion_reason and
    optional known start_type (STANDING/FLYING/UNKNOWN). Callers resolve any
    overlapping timestamps before calling. Invalid work is disclosed, not zero.
    """
    totals = {z: {"real_seconds": 0., "covered_seconds": 0., "equivalent_seconds": 0.,
                  "baseline_equivalent_seconds": 0.} for z in ZONES}
    excluded = outside = supplied = rest = 0.
    reasons = set()
    starts = set()
    for segment in segments:
        seconds = segment.get("seconds")
        if not positive(seconds):
            reasons.add("INVALID_SEGMENT_DURATION")
            continue
        supplied += seconds
        if segment.get("start_type"):
            starts.add(segment["start_type"])
        if segment.get("exclusion_reason") == "REST":
            rest += seconds
            continue
        estimate = evaluate(parameters, segment.get("speed_kmh"))
        if estimate["status"] == "REST":
            rest += seconds
            continue
        zone = estimate.get("zone")
        if zone in totals:
            totals[zone]["real_seconds"] += seconds
        if segment.get("exclusion_reason"):
            excluded += seconds
            reasons.add(str(segment["exclusion_reason"]))
            continue
        if estimate["status"] != "AVAILABLE":
            outside += seconds
            reasons.add(estimate["reason"])
            continue
        row = totals[zone]
        row["covered_seconds"] += seconds
        row["equivalent_seconds"] += seconds*estimate["coefficient"]
        row["baseline_equivalent_seconds"] += seconds*estimate["baseline_coefficient"]
    recorded = max(float(recorded_seconds) if positive(recorded_seconds) else 0., supplied)
    covered = sum(r["covered_seconds"] for r in totals.values())
    rows = []
    for prepared in parameters["zones"]:
        total = totals[prepared["zone"]]
        valid = total["covered_seconds"] > 0 or total["real_seconds"] == 0 and prepared["status"] == "AVAILABLE"
        eq = total["equivalent_seconds"] if valid else None
        baseline = total["baseline_equivalent_seconds"] if valid else None
        tref = prepared["tref_minutes"]
        rows.append({**prepared, **total, "equivalent_seconds": eq, "baseline_equivalent_seconds": baseline,
                     "real_minutes": total["real_seconds"]/60,
                     "covered_minutes": total["covered_seconds"]/60,
                     "equivalent_minutes": eq/60 if eq is not None else None,
                     "baseline_equivalent_minutes": baseline/60 if baseline is not None else None,
                     "normalized_q": eq/(60*tref) if eq is not None and tref else None,
                     "baseline_normalized_q": baseline/(60*tref) if baseline is not None and tref else None})
    return {"version": VERSION, "integration_version": INTEGRATION_VERSION,
            "role": "COMPARISON_ONLY", "parameters_version": parameters["parameters_version"],
            "speed_role": parameters.get("speed_role", "FLAT_EQUIVALENT"),
            "initial_heuristic": parameters.get("initial_heuristic", False),
            "calibration_source": parameters.get("calibration_source"),
            "settings": deepcopy(parameters["settings"]), "sport": parameters["sport"],
            "reference_policy": parameters["reference_policy"],
            "status": "AVAILABLE" if recorded and covered+rest >= recorded-1e-8 else "PARTIAL" if covered+rest else "UNAVAILABLE",
            "recorded_minutes": recorded/60, "classified_minutes": covered/60,
            "passive_rest_minutes": rest/60, "accounted_minutes": (covered+rest)/60,
            "excluded_minutes": excluded/60, "outside_range_minutes": outside/60,
            "uncovered_minutes": max(0., recorded-covered-rest)/60,
            "coverage_percent": 100*covered/recorded if recorded else 0., "zones": rows,
            "equivalent_time_unit": parameters["equivalent_time_unit"], "normalized_q_unit": parameters["normalized_q_unit"],
            "fallback_indicator": fallback_indicator, "start_types": sorted(starts) or ["UNKNOWN"],
            "warnings": sorted(set(parameters["warnings"]) | set(warnings) | reasons)}


def aggregate(parameters, comparisons):
    """Combine same-version zone ledgers; never add zone-equivalent minutes."""
    comparisons = list(comparisons)
    if any(c["parameters_version"] != parameters["parameters_version"] for c in comparisons):
        raise ValueError("Cannot combine different speed-equivalence parameter versions")
    result = integrate(parameters, [], recorded_seconds=0)
    for field in ("recorded_minutes", "classified_minutes", "excluded_minutes", "outside_range_minutes", "uncovered_minutes",
                  "passive_rest_minutes"):
        result[field] = sum(c.get(field, 0.) for c in comparisons)
    # Planned passive rests are outside the plan's recorded work subtotal;
    # execution rests are inside recorded activity time. Retain that distinction.
    result["accounted_minutes"] = sum(c.get("accounted_minutes", c["classified_minutes"]) for c in comparisons)
    total, covered = result["recorded_minutes"], result["classified_minutes"]
    result["coverage_percent"] = 100*covered/total if total else 0.
    accounted = result["accounted_minutes"]
    result["status"] = "AVAILABLE" if total and accounted >= total-1e-8 else "PARTIAL" if accounted else "UNAVAILABLE"
    for i,row in enumerate(result["zones"]):
        for field in ("real_seconds", "covered_seconds", "real_minutes", "covered_minutes"):
            row[field] = sum(c["zones"][i][field] for c in comparisons)
        for field in ("equivalent_seconds", "baseline_equivalent_seconds", "equivalent_minutes", "baseline_equivalent_minutes", "normalized_q", "baseline_normalized_q"):
            values = [c["zones"][i][field] for c in comparisons]
            row[field] = sum(v for v in values if v is not None) if any(v is not None for v in values) else None
    result["warnings"] = sorted(set(result["warnings"]) | {w for c in comparisons for w in c["warnings"]})
    result["start_types"] = sorted({s for c in comparisons for s in c["start_types"]}) or ["UNKNOWN"]
    return result
