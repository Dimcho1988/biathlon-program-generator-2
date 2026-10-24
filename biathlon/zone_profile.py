"""One versioned HR/Vflat accounting profile, never calibration evidence.

Observed TI determines the upper Z1-Z4 edges. A test-derived curve supplies
explicit initial estimates only where paired observations are unavailable.
45% HRmax <-> 20% V6 is a coaching accounting convention, not a physiological
identity. Cycling HR is already expressed in the common +7 bpm frame here.
"""
from bisect import bisect_right
from hashlib import sha256
import json
import math

VERSION = "common-zone-profile-v1-2026-10-24"
HR_PERCENTAGES = (45., 72., 79., 89., 94., 100.)
REST_V6_FRACTION = .20
MIN_SECONDS = {"Z1": 420., "Z2": 420., "Z3": 420., "Z4": 420., "Z5": 300., "GENERAL": 420.}
ZONES = tuple(f"Z{i}" for i in range(1, 6))


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


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
    bounds = tuple(float(v) for v in bounds)
    result = {"version": VERSION, "status": "UNAVAILABLE", "sport": sport,
              "zones": [], "basis": "MEASURED_TI_WITH_EXPLICIT_INITIAL_FALLBACK",
              "reference_bounds_bpm": list(bounds), "reference_hrmax_bpm": hrmax,
              "speed_role": "FLAT_EQUIVALENT", "is_measured_hr": False,
              "calibration_eligible": False, "hr_policy": policy(sport, bounds, hrmax),
              "rest_v6_fraction": REST_V6_FRACTION, "v10": v10_anchor(curve, tests)}
    if len(bounds) != 6 or not positive(hrmax) or bounds[-1] > hrmax or not all(positive(v) for v in bounds) or any(a >= b for a,b in zip(bounds,bounds[1:])):
        return {**result, "reason": "HR_PROFILE_REQUIRED"}
    if curve is None:
        return {**result, "reason": "ABSOLUTE_SPEED_EVIDENCE_REQUIRED"}
    try:
        v6, v50 = curve.speed(360.)*3.6, curve.speed(3000.)*3.6
    except (ValueError, ArithmeticError):
        return {**result, "reason": "INITIAL_ANCHORS_OUTSIDE_CURVE"}
    v10 = result["v10"].get("speed_kmh")
    if not positive(v10) or not 0 < v50 < v6 < v10:
        return {**result, "reason": "CONFLICTING_SPEED_ANCHORS"}
    indices = indices or {}
    def usable(key):
        row = indices.get(key) or {}
        return (positive(row.get("index")) and positive(row.get("count")) and row["count"] >= 1
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
    if any(a >= b for a,b in zip(speeds,speeds[1:])):
        # One observed GENERAL relationship is preferable to sorting independent
        # zonal estimates. Never change an accepted pair to fit an expert Tmax.
        if usable("GENERAL"):
            speeds = [REST_V6_FRACTION*v6] + [100*h/hrmax/indices["GENERAL"]["index"] for h in bounds[1:5]] + [float(v10)]
            sources = ["GENERAL_CONFLICT_FALLBACK"]*4
            conflict = "CONFLICTING_ZONAL_INDICES"
        if any(a >= b for a,b in zip(speeds,speeds[1:])):
            return {**result, "reason": "NONMONOTONE_INDEX_BOUNDARIES", "candidate_bounds_kmh": speeds}
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
