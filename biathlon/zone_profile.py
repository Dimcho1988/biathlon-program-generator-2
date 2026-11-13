"""One versioned HR/Vflat accounting profile, never calibration evidence.

Observed TI determines the upper Z1-Z4 edges. A test-derived curve supplies
explicit initial estimates only where paired observations are unavailable.
45% HRmax <-> 20% V6 is a coaching accounting convention, not a physiological
identity. Cycling HR is already expressed in the common +7 bpm frame here.
"""
from bisect import bisect_right
from collections.abc import Mapping
from hashlib import sha256
import json
import math

VERSION = "common-zone-profile-v2-test-conflict-fallback"
HR_PERCENTAGES = (45., 72., 79., 89., 94., 100.)
REST_V6_FRACTION = .20
MIN_SECONDS = {"Z1": 420., "Z2": 420., "Z3": 420., "Z4": 420., "Z5": 300., "GENERAL": 420.}
ZONES = tuple(f"Z{i}" for i in range(1, 6))


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _accepted_tests(tests, sport):
    """Retain real, comparable measurements; declarations alone are not evidence."""
    generated_sources = {"MODEL", "MODEL_GENERATED", "GENERATED", "ESTIMATED", "REFERENCE"}
    return tuple(t for t in (tests or ()) if isinstance(t, Mapping)
                 and t.get("maximal") is True and t.get("comparable") is True
                 and t.get("sport", sport) == sport
                 and t.get("enabled") is not False and t.get("is_maximal_test") is not False
                 and t.get("test_mode", "STRICT") == "STRICT"
                 and str(t.get("source", "")).upper() not in generated_sources
                 and str(t.get("kind", "")).upper() not in {"ESTIMATE", "MODEL_POINT"}
                 and not t.get("generated") and not t.get("is_estimated")
                 and positive(t.get("duration_s")) and positive(t.get("speed_kmh")))


def _ordered(speeds):
    return all(positive(v) for v in speeds) and all(a < b for a, b in zip(speeds, speeds[1:]))


def interpolate(x, xs, ys):
    if not math.isfinite(x) or x < xs[0] or x > xs[-1]:
        raise ValueError("Outside paired profile")
    i = min(len(xs)-2, max(0, bisect_right(xs, x)-1))
    return ys[i] + (x-xs[i])/(xs[i+1]-xs[i])*(ys[i+1]-ys[i])


def mechanical_power(speed_kmh):
    v = speed_kmh / 3.6
    return 3.924*v + .196*v**3


def speed_from_power(power):
    lo, hi = 0., max(1., (power/.196)**(1/3))
    for _ in range(60):
        mid = (lo+hi)/2
        if 3.924*mid + .196*mid**3 < power:
            lo = mid
        else:
            hi = mid
    return (lo+hi)/2*3.6


def initial_speed(percent, v50, v6, *, cycling=False):
    """Initial common-reference function; coefficients are explicit priors.

    The lower pair is in speed coordinates for every sport. For cycling, the
    intermediate workload interpolation is in mechanical power, not speed.
    The common-frame 86/98 anchors avoid applying a second cycling HR offset.
    """
    if not 0 < v50 < v6:
        raise ValueError("V50 must be below V6")
    transform = mechanical_power if cycling else float
    inverse = speed_from_power if cycling else float
    a, b = transform(v50), transform(v6)
    xs = (45., 72., 79., 86., 98.)
    ys = (transform(REST_V6_FRACTION*v6), .78*a, .87*a, a, b)
    if any(left >= right for left, right in zip(ys, ys[1:])):
        raise ValueError("Conflicting initial anchors")
    return inverse(interpolate(percent, xs, ys))


def build(curve, bounds, hrmax, indices=None, *, sport="Run", tests=()):
    from .sport_heart_rate import is_cycling, policy
    from .speed_zones import v10_anchor
    try:
        raw_bounds = tuple(bounds)
        bounds = tuple(float(v) for v in raw_bounds)
    except (ValueError, TypeError, OverflowError):
        bounds, raw_bounds = (), ()
    valid_hr = (len(bounds) == 6 and positive(hrmax) and bounds[-1] <= hrmax
                and all(positive(v) for v in raw_bounds) and _ordered(bounds))
    tests = _accepted_tests(tests, sport) if isinstance(tests, (list, tuple)) else ()
    result = {"version": VERSION, "status": "UNAVAILABLE", "sport": sport,
              "zones": [], "basis": "MEASURED_TI_WITH_EXPLICIT_INITIAL_FALLBACK",
              "reference_bounds_bpm": list(bounds) if valid_hr else [], "reference_hrmax_bpm": hrmax if positive(hrmax) else None,
              "speed_role": "FLAT_EQUIVALENT", "is_measured_hr": False,
              "calibration_eligible": False, "hr_policy": policy(sport, bounds if valid_hr else (), hrmax if positive(hrmax) else None),
              "rest_v6_fraction": REST_V6_FRACTION, "v10": None}
    if not valid_hr:
        return {**result, "reason": "HR_PROFILE_REQUIRED"}
    result["v10"] = v10_anchor(curve, tests)
    if curve is None:
        return {**result, "reason": "ABSOLUTE_SPEED_EVIDENCE_REQUIRED"}
    try:
        v6, v50 = curve.speed(360.)*3.6, curve.speed(3000.)*3.6
    except (ValueError, ArithmeticError):
        return {**result, "reason": "INITIAL_ANCHORS_OUTSIDE_CURVE"}
    v10 = result["v10"].get("speed_kmh")
    if not positive(v10) or not 0 < v50 < v6 < v10:
        return {**result, "reason": "CONFLICTING_SPEED_ANCHORS"}
    indices = indices if isinstance(indices, Mapping) else {}
    def usable(key):
        row = indices.get(key) or {}
        return (isinstance(row, Mapping) and positive(row.get("index")) and positive(row.get("count")) and row["count"] >= 1
                and positive(row.get("seconds")) and row["seconds"] >= MIN_SECONDS[key]
                and not row.get("is_estimated") and not row.get("generated") and row.get("valid") is not False)
    speeds, sources = [REST_V6_FRACTION*v6], []
    for i, zone in enumerate(ZONES[:4]):
        key = zone if usable(zone) else "GENERAL" if usable("GENERAL") else None
        if key:
            value = 100*bounds[i+1]/hrmax/indices[key]["index"]
            source = "PAIRED_INDEX" if key == zone else "GENERAL_PAIRED_INDEX"
        else:
            try:
                value = initial_speed(100*bounds[i+1]/hrmax, v50, v6, cycling=is_cycling(sport))
            except ValueError:
                return {**result, "reason": "INITIAL_HR_EDGE_OUTSIDE_SUPPORTED_RANGE"}
            source = "INITIAL_V50_V6_ESTIMATE"
        speeds.append(value)
        sources.append(source)
    speeds.append(float(v10))
    conflict = None
    if not _ordered(speeds):
        rejected = [{"source": "ZONAL_OR_GENERAL_PAIRED_PROFILE",
                     "bounds_kmh": [v if positive(v) else None for v in speeds]}]
        # One observed GENERAL relationship is preferable to sorting independent
        # zonal estimates. Never change an accepted pair to fit an expert Tmax.
        if usable("GENERAL"):
            speeds = [REST_V6_FRACTION*v6] + [100*h/hrmax/indices["GENERAL"]["index"] for h in bounds[1:5]] + [float(v10)]
            sources = ["GENERAL_CONFLICT_FALLBACK"]*4
            conflict = "CONFLICTING_ZONAL_INDICES"
            if not _ordered(speeds):
                rejected.append({"source": "GENERAL_PAIRED_PROFILE",
                                 "bounds_kmh": [v if positive(v) else None for v in speeds]})
        if not _ordered(speeds):
            unavailable = {**result, "reason": "NONMONOTONE_INDEX_BOUNDARIES",
                           "candidate_bounds_kmh": rejected[-1]["bounds_kmh"],
                           "excluded_profiles": rejected}
            # A sprint boundary alone cannot establish V50/V6. The caller has
            # already selected current, sport-compatible curve measurements.
            curve_tests = [t for t in tests if t["duration_s"] > 10.]
            if not curve_tests:
                return unavailable
            from .speed_duration import calibrated, with_ten_second_tail
            try:
                # The supplied curve may be a dosing mixture. Reconstruct the
                # test-only curve to keep boundary provenance independent.
                fallback_curve = with_ten_second_tail(calibrated(curve_tests))
            except (ValueError, ArithmeticError):
                return {**unavailable, "fallback_reason": "INVALID_ACCEPTED_TEST_CURVE"}
            try:
                v6, v50 = fallback_curve.speed(360.)*3.6, fallback_curve.speed(3000.)*3.6
                fallback_v10 = v10_anchor(fallback_curve, tests)
                v10 = fallback_v10.get("speed_kmh")
                if not positive(v10) or not 0 < v50 < v6 < v10:
                    return {**unavailable, "fallback_reason": "CONFLICTING_SPEED_ANCHORS"}
                speeds = [REST_V6_FRACTION*v6] + [
                    initial_speed(100*h/hrmax, v50, v6, cycling=is_cycling(sport))
                    for h in bounds[1:5]] + [float(v10)]
            except (ValueError, ArithmeticError):
                return {**unavailable, "fallback_reason": "INITIAL_ANCHORS_OUTSIDE_CURVE"}
            if not _ordered(speeds):
                return {**unavailable, "fallback_reason": "CONFLICTING_SPEED_ANCHORS"}
            sources = ["TEST_CURVE_CONFLICT_FALLBACK"]*4
            conflict = "CONFLICTING_PAIRED_BOUNDARIES_TEST_FALLBACK"
            result.update(basis="TEST_CURVE_AFTER_PAIRED_PROFILE_CONFLICT",
                          v10=fallback_v10, excluded_profiles=rejected,
                          boundary_curve_model_version=fallback_curve.model_version)
    rows = []
    for i, zone in enumerate(ZONES):
        source = sources[i] if i < 4 else "V10_BOUNDARY"
        rows.append({"zone": zone, "low_kmh": speeds[i], "high_kmh": speeds[i+1],
                     "source": source, "boundary_source": source,
                     "low_reference_hr_bpm": bounds[i], "high_reference_hr_bpm": bounds[i+1],
                     "upper_reference_duration_s": 10. if i == 4 else None})
    result.update(status="AVAILABLE", reason=conflict, zones=rows, bounds_kmh=speeds,
                  v6_kmh=v6, v50_kmh=v50, rest_threshold_kmh=speeds[0],
                  supported_low_kmh=speeds[0], supported_z5_high_kmh=speeds[-1],
                  supported_z5_high_basis="V10", accepted_test_count=len(tests),
                  note="Началните оценки и наблюдаваният ИТ имат отделен произход.")
    result["parameters_version"] = sha256(json.dumps(result, sort_keys=True, allow_nan=False,
                                                      separators=(",", ":")).encode()).hexdigest()
    return result


def speed_for_hr(profile, reference_hr):
    if profile.get("status") != "AVAILABLE":
        raise ValueError("Common profile unavailable")
    return interpolate(reference_hr, profile["reference_bounds_bpm"], profile["bounds_kmh"])


def hr_for_speed(profile, speed):
    if profile.get("status") != "AVAILABLE":
        raise ValueError("Common profile unavailable")
    return interpolate(speed, profile["bounds_kmh"], profile["reference_bounds_bpm"])
