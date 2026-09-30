"""Separate coaching curve: 30% index and 70% tests at the same duration.

The component curves and measured tests are immutable. The weighted mean
is evaluated directly, never refitted through chart samples. Positive mixtures
preserve decreasing speed, increasing distance and C1 continuity. HR keeps the
index model's time coordinates; reinverting the index against the mixture
would silently cancel the requested blend.
"""
from copy import deepcopy
import math

from . import hr_speed, preliminary_capacity, speed_duration, speed_zones

VERSION = "dosing-index-test-30-70-v1"
INDEX_WEIGHT, TEST_WEIGHT = .3, .7


class Curve:
    model_version = VERSION
    calibration_mode = "COACH_INDEX_TEST_BLEND"

    def __init__(self, index_curve, test_curve):
        self.index_curve, self.test_curve = index_curve, test_curve
        low = max(index_curve.times[0], test_curve.times[0])
        high = min(index_curve.times[-1], test_curve.times[-1])
        if low >= high:
            raise ValueError("Dosing curves require a common duration domain")
        self.times = tuple(sorted({low, high} | {t for c in (index_curve, test_curve)
                                               for t in c.times if low <= t <= high}))
        self._x = tuple(math.log(t) for t in self.times)

    def speed(self, t):
        return INDEX_WEIGHT*self.index_curve.speed(t) + TEST_WEIGHT*self.test_curve.speed(t)

    def distance(self, t):
        return t*self.speed(t)

    def log_slope(self, t):
        a, b = INDEX_WEIGHT*self.index_curve.speed(t), TEST_WEIGHT*self.test_curve.speed(t)
        return (a*self.index_curve.log_slope(t) + b*self.test_curve.log_slope(t))/(a+b)

    def inverse(self, value, *, distance=False):
        return speed_duration.Curve.inverse(self, value, distance=distance)


class Predictor(hr_speed.Predictor):
    def __init__(self, curve, bounds, hrmax, indices, *, expert_durations=None):
        super().__init__(curve.index_curve, bounds, hrmax, indices, expert_durations=expert_durations)
        self.curve = curve
        for anchor in self.anchors:
            anchor["index_hr_source"] = anchor["source"]
            anchor["source"] = "COACH_INDEX_TEST_BLEND"
            anchor["index_speed_kmh"] = curve.index_curve.speed(anchor["duration_s"])*3.6
            anchor["test_speed_kmh"] = curve.test_curve.speed(anchor["duration_s"])*3.6
            anchor["speed_kmh"] = curve.speed(anchor["duration_s"])*3.6
        self.speed_range = (self.speed_for_hr(self.min_hr), self.speed_for_hr(self.max_hr))

    def summary(self):
        return {**super().summary(), "model_version": VERSION, "hr_is_estimate": True}


def from_view(view):
    """Reconstruct only an explicitly available, versioned dosing model."""
    evidence = (view or {}).get("dosing_model") or {}
    if (evidence.get("status") != "AVAILABLE" or evidence.get("model_version") != VERSION
            or evidence.get("weights") != {"index": INDEX_WEIGHT, "tests": TEST_WEIGHT}):
        return None
    prior = preliminary_capacity.curve_from_summary(view.get("preliminary_capacity"))
    keys = set(view.get("active_test_keys", []))
    tests = [e["payload"] for e in view.get("tests", []) if e.get("entry_key") in keys]
    if prior is None or not tests or any(not t.get("maximal") or t.get("test_mode", "STRICT") != "STRICT"
                                       or t.get("generated") or t.get("is_estimated") for t in tests):
        return None
    return Curve(prior, speed_duration.calibrated(tests))


def describe(index_curve, test_curve, tests, bounds, hrmax, indices, anchors):
    result = {"model_version": VERSION, "status": "UNAVAILABLE", "points": [], "hr_model": None,
              "speed_zones": None, "weights": {"index": INDEX_WEIGHT, "tests": TEST_WEIGHT},
              "weight_basis": "COACH_POLICY_NOT_VALIDATED_RELIABILITY", "is_maximal_test": False,
              "use": "METHOD_DOSING", "reason": None}
    if index_curve is None or test_curve is None or not tests:
        return {**result, "reason": "BOTH_INDEX_AND_MAXIMAL_TEST_CURVES_REQUIRED"}
    try:
        curve = Curve(index_curve, test_curve)
        predictor = Predictor(curve, bounds, hrmax, indices,
                              expert_durations={a["zone"]: a["duration_s"] for a in anchors})
    except ValueError:
        return {**result, "reason": "INCOMPATIBLE_DURATION_OR_HR_DOMAIN"}
    fixed = {curve.times[0], curve.times[-1]} | {a["duration_s"] for a in predictor.anchors} | {t["duration_s"] for t in tests}
    grid = [math.exp(curve._x[0]+(curve._x[-1]-curve._x[0])*i/180) for i in range(1, 180)]
    times = sorted(fixed | {t for t in grid if all(abs(math.log(t/f)) > 1e-10 for f in fixed)})
    points = []
    for t in times:
        try:
            hr = predictor.hr_for_duration(t)
        except ValueError:
            hr = None
        points.append({"duration_s": t, "speed_kmh": curve.speed(t)*3.6,
                       "index_speed_kmh": index_curve.speed(t)*3.6,
                       "test_speed_kmh": test_curve.speed(t)*3.6,
                       "distance_m": curve.distance(t), "estimated_hr_bpm": hr,
                       "evidence": "COACH_BLEND_ESTIMATE"})
    zones = speed_zones.build(curve, predictor, [])
    zones.update(basis="COACH_INDEX_TEST_BLEND", model_version=VERSION,
                 note="Работни скоростни зони от общата крива 30/70 за дозиране на методите.")
    for z in zones["zones"]:
        z["source"] = "COACH_INDEX_TEST_BLEND"
    return {**result, "status": "AVAILABLE", "points": points, "hr_model": predictor.summary(),
            "speed_zones": zones, "duration_range_s": [curve.times[0], curve.times[-1]],
            "component_versions": {"index": index_curve.model_version, "tests": test_curve.model_version},
            "zone_comparison": deepcopy(predictor.anchors), "accepted_test_count": len(tests)}
