"""Separate coaching curve: 30% index and 70% tests at the same duration.

The component curves and measured tests are immutable. The weighted mean
is evaluated directly, never refitted through chart samples. Positive mixtures
preserve decreasing speed, increasing distance and C1 continuity. Measured
HR/speed zone pairs stay fixed; capacity is inverted on the selected mixture.
"""
from copy import deepcopy
import math

from . import hr_speed, preliminary_capacity, speed_duration, speed_zones

VERSION = "dosing-index-test-30-70-v3-shared-selection"
SELECTION_VERSION = "working-speed-curve-v1"
INDEX_WEIGHT, TEST_WEIGHT = .3, .7


class Curve:
    model_version = VERSION
    calibration_mode = "COACH_INDEX_TEST_BLEND"

    def __init__(self, index_curve, test_curve):
        index_curve = speed_duration.with_ten_second_tail(index_curve)
        test_curve = speed_duration.with_ten_second_tail(test_curve)
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


class Predictor(hr_speed.ZonalPredictor):
    def __init__(self, curve, bounds, hrmax, indices, *, expert_durations=None,normalization_offset_bpm=0., sport="Run", profile=None, tests=()):
        super().__init__(curve, bounds, hrmax, indices, expert_durations=expert_durations,
                         normalization_offset_bpm=normalization_offset_bpm, sport=sport, profile=profile, tests=tests)
        for anchor in self.anchors:
            anchor["index_hr_source"] = anchor["source"]
            duration = anchor["duration_s"]
            anchor["index_speed_kmh"] = curve.index_curve.speed(duration)*3.6 if duration is not None else None
            anchor["test_speed_kmh"] = curve.test_curve.speed(duration)*3.6 if duration is not None else None
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


def selected(view):
    """One continuous working curve for recipes, planning and load capacity.

    Only the admitted, sport-specific inputs can select a curve. Missing
    components never become a fabricated 30/70 blend or a normative athlete.
    Recorded tests and the separate performance curve remain unchanged.
    """
    view = view or {}
    if view.get("status") == "INITIAL_HEURISTIC":
        from .initial_speed_profile import curve_from_view
        curve = curve_from_view(view)
        if curve is None:
            raise ValueError("INVALID_INITIAL_HEURISTIC_PROFILE")
        return curve, [], "INITIAL_HEURISTIC"
    if view.get("status") not in {"CALIBRATED", "PRELIMINARY"}:
        return None, [], None
    keys = set(view.get("active_test_keys") or ())
    tests = [row["payload"] for row in view.get("tests", ()) if row.get("entry_key") in keys]
    generated = {"MODEL", "MODEL_GENERATED", "GENERATED", "ESTIMATED", "REFERENCE"}
    if any(t.get("maximal") is not True or t.get("test_mode", "STRICT") != "STRICT"
           or t.get("comparable") is False or t.get("enabled") is False
           or t.get("sport", view.get("sport")) != view.get("sport")
           or t.get("generated") or t.get("is_estimated") or t.get("is_maximal_test") is False
           or str(t.get("source", "")).upper() in generated
           or str(t.get("kind", "")).upper() in {"MODEL_POINT", "ESTIMATE"} for t in tests):
        raise ValueError("EXPLORATORY_OR_NONMAXIMAL_TESTS")
    blend = from_view(view)
    if blend is not None:
        return blend, tests, "BLENDED_DOSING_CURVE"
    if (view.get("dosing_model") or {}).get("status") == "AVAILABLE":
        raise ValueError("INVALID_WORKING_CURVE_PROVENANCE")
    prior = preliminary_capacity.curve_from_summary(view.get("preliminary_capacity"))
    if not tests and prior is None:
        raise ValueError("NO_INDIVIDUAL_SPEED_CURVE")
    curve = speed_duration.with_ten_second_tail(speed_duration.calibrated(tests, prior=prior))
    return curve, tests, "INDIVIDUAL_TEST_CURVE" if tests else "INDIVIDUAL_PRIOR_CURVE"


def selected_zones(view, curve, tests, *, sport=None):
    """Reuse the published profile, including the legacy compact-view form."""
    view = view or {}
    dosing = view.get("dosing_model") or {}
    profile = dosing.get("speed_zones") or view.get("speed_zones")
    if profile is not None:
        return profile
    model = dosing.get("hr_model") or view.get("hr_model") or {}
    rows, limits = model.get("zones", [])[:4], model.get("hr_range_bpm", [])
    if len(rows) != 4 or len(limits) != 2:
        return None
    from . import zone_profile
    offset = model.get("hr_reference_offset_bpm", 0.)
    bounds = [limits[0], *[r["hr_bpm"] for r in rows], limits[-1]]
    return zone_profile.build(curve, tuple(h+offset for h in bounds), limits[-1]+offset,
        view.get("index_summary"), sport=sport or view.get("sport") or "Run", tests=tests)


def describe(index_curve, test_curve, tests, bounds, hrmax, indices, anchors, *, normalization_offset_bpm=0., include_points=True, sport="Run"):
    result = {"model_version": VERSION, "status": "UNAVAILABLE", "points": [], "hr_model": None,
              "speed_zones": None, "weights": {"index": INDEX_WEIGHT, "tests": TEST_WEIGHT},
              "weight_basis": "COACH_POLICY_NOT_VALIDATED_RELIABILITY", "is_maximal_test": False,
              "use": "METHOD_DOSING", "reason": None}
    if index_curve is None or test_curve is None or not tests:
        return {**result, "reason": "BOTH_INDEX_AND_MAXIMAL_TEST_CURVES_REQUIRED"}
    try:
        curve = Curve(index_curve, test_curve)
        predictor = Predictor(curve, bounds, hrmax, indices,
                              expert_durations={a["zone"]: a["duration_s"] for a in anchors},
                              normalization_offset_bpm=normalization_offset_bpm, sport=sport, tests=tests)
    except ValueError:
        return {**result, "reason": "INCOMPATIBLE_DURATION_OR_HR_DOMAIN"}
    fixed = {curve.times[0], curve.times[-1]} | {a["duration_s"] for a in predictor.anchors if a["duration_s"] is not None and curve.times[0] <= a["duration_s"] <= curve.times[-1]} | {t["duration_s"] for t in tests}
    grid = [math.exp(curve._x[0]+(curve._x[-1]-curve._x[0])*i/180) for i in range(1, 180)]
    times = sorted(fixed | {t for t in grid if all(abs(math.log(t/f)) > 1e-10 for f in fixed)})
    points = []
    for t in times if include_points else ():
        try:
            hr = predictor.hr_for_duration(t)
        except ValueError:
            hr = None
        points.append({"duration_s": t, "speed_kmh": curve.speed(t)*3.6,
                       "index_speed_kmh": curve.index_curve.speed(t)*3.6,
                       "test_speed_kmh": curve.test_curve.speed(t)*3.6,
                       "distance_m": curve.distance(t), "estimated_hr_bpm": hr,
                       "evidence": "COACH_BLEND_ESTIMATE"})
    zones = speed_zones.build(curve, predictor, tests)
    if zones.get("basis") == "TEST_CURVE_AFTER_PAIRED_PROFILE_CONFLICT":
        zones.update(model_version=VERSION, capacity_basis="COACH_INDEX_TEST_BLEND",
                     note="Границите използват тестовата крива поради несъвместими индексни граници; дозировката използва кривата 30/70.")
    else:
        zones.update(basis="COACH_INDEX_TEST_BLEND", model_version=VERSION,
                     note="Работни скоростни зони от общата крива 30/70 за дозиране на методите.")
    return {**result, "status": "AVAILABLE", "points": points, "hr_model": predictor.summary(),
            "speed_zones": zones, "duration_range_s": [curve.times[0], curve.times[-1]],
            "component_versions": {"index": index_curve.model_version, "tests": test_curve.model_version},
            "zone_comparison": deepcopy(predictor.anchors), "accepted_test_count": len(tests)}
