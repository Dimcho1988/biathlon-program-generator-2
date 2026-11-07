"""Descriptive speed-endurance profile with explicit evidence provenance.

Overall performance and curve shape are separate quantities. Only accepted,
real maximal tests establish a measured shape comparison; extrapolated tails,
the five-percent corridor and volume-derived priors cannot establish strengths.
This module makes no genetic, muscle-fibre or causal training inference.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

from .speed_duration import Curve, REFERENCE_SPEEDS, REFERENCE_TIMES, positive


VERSION = "athlete-functional-profile-v1"
PROFILE_DURATIONS_S = (60.0, 180.0, 720.0, 1200.0, 3600.0, 7200.0)
_GENERATED_SOURCES = {"MODEL", "MODEL_GENERATED", "GENERATED", "ESTIMATED", "REFERENCE"}
_WARNINGS = [
    "EXPERT_REFERENCE_NOT_POPULATION_VALIDATED",
    "FUNCTIONAL_PROFILE_NOT_GENOTYPE_OR_FIBRE_COMPOSITION",
    "TRAINING_ASSOCIATION_NOT_CAUSATION",
    "EXTRAPOLATED_SHAPE_NOT_MEASURED",
]


def _real_tests(tests: Sequence[Mapping]) -> list[dict]:
    """Caller selects current, comparable tests; never promote generated data."""
    accepted: dict[float, dict] = {}
    for test in tests:
        if (test.get("enabled") is False
                or test.get("test_mode", "STRICT") != "STRICT"
                or str(test.get("source", "")).upper() in _GENERATED_SOURCES
                or str(test.get("kind", "")).upper() in {"ESTIMATE", "MODEL_POINT"}
                or test.get("generated") or test.get("is_estimated")
                or test.get("is_maximal") is False or test.get("is_maximal_test") is False):
            continue
        duration = positive(test["duration_s"])
        speed = positive(test["speed_kmh"])
        if duration in accepted and not math.isclose(accepted[duration]["speed_kmh"], speed, rel_tol=1e-10):
            raise ValueError("Conflicting accepted tests at the same duration")
        accepted[duration] = {"duration_s": duration, "speed_kmh": speed}
    return sorted(accepted.values(), key=lambda test: test["duration_s"])


def _training_context(zone_weekly_min: Mapping | None, source: str) -> dict:
    zones = []
    for zone in ("Z1", "Z2", "Z3", "Z4", "Z5"):
        value = (zone_weekly_min or {}).get(zone)
        # Unknown exposure remains unknown; missing zones are never zero load.
        minutes = float(value) if (isinstance(value, (int, float)) and not isinstance(value, bool)
                                  and math.isfinite(value) and value >= 0) else None
        zones.append({"zone": zone, "weekly_minutes": minutes})
    complete = all(row["weekly_minutes"] is not None for row in zones)
    total = math.fsum(row["weekly_minutes"] or 0 for row in zones)
    for row in zones:
        row["share_percent"] = 100 * row["weekly_minutes"] / total if complete and total else None
    return {"source": source, "unit": "EQUIVALENT_MINUTES", "zones": zones, "interpretation": "ASSOCIATION_ONLY",
            "hypotheses": ["COMPARE_REPEATED_TESTS_AFTER_COMPARABLE_TRAINING"]}


def build_functional_profile(
    curve,
    accepted_tests: Sequence[Mapping],
    *,
    zone_weekly_min: Mapping | None = None,
    exposure_source: str = "UNKNOWN",
    point_metadata: Callable[[float], Mapping] | None = None,
) -> dict:
    """Return JSON-ready descriptive values in km/h, seconds and percentages.

``overall_level`` is the geometric individual/reference ratio at actual test
durations, or (with no tests) at the fixed descriptive durations. The latter
is explicitly estimated. ``shape`` compares the longest and shortest *real*
test ratios and never includes generated or extrapolated points. All accepted
test points remain visible, including intermediate tests in multipoint mode.

The caller is responsible for sport, date and measurement compatibility; an
unavailable absolute-speed model must be passed as ``None``.
    """
    result = {
        "schema_version": VERSION,
        "status": "UNAVAILABLE",
        "overall_level": None,
        "shape": None,
        "points": [],
        "test_points": [],
        "training_context": _training_context(zone_weekly_min, exposure_source),
        "warnings": list(_WARNINGS),
    }
    if curve is None:
        result["warnings"].append("ABSOLUTE_SPEED_ANCHOR_REQUIRED")
        return result

    reference = Curve(REFERENCE_TIMES, REFERENCE_SPEEDS)
    tests = _real_tests(accepted_tests)
    durations = [test["duration_s"] for test in tests]
    # Evaluating the reference also verifies all accepted anchors are in range.
    measured_logs = [math.log(test["speed_kmh"] / (reference.speed(test["duration_s"]) * 3.6))
                     for test in tests]
    metadata = point_metadata or getattr(curve, "point_metadata", None)
    raw_points = []
    for duration in PROFILE_DURATIONS_S:
        try:
            speed = positive(curve.speed(duration)) * 3.6
            reference_speed = reference.speed(duration) * 3.6
        except ValueError:
            # A bounded curve need not cover every display duration.
            continue
        observed = next((test for test in tests
                         if math.isclose(duration, test["duration_s"], rel_tol=1e-10)), None)
        if observed is not None:
            speed = observed["speed_kmh"]
        raw_points.append((duration, speed, reference_speed))

    if measured_logs:
        mean_log = math.fsum(measured_logs) / len(measured_logs)
        basis = "REAL_TESTS"
        level_range = [durations[0], durations[-1]]
    elif raw_points:
        mean_log = math.fsum(math.log(speed / normative) for _, speed, normative in raw_points) / len(raw_points)
        basis = "ESTIMATED_FIXED_DURATIONS"
        level_range = [raw_points[0][0], raw_points[-1][0]]
    else:
        result["warnings"].append("NO_COMPARABLE_DURATION_RANGE")
        return result
    overall_ratio = math.exp(mean_log)
    result["overall_level"] = {
        "ratio_to_reference": overall_ratio,
        "difference_percent": math.expm1(mean_log) * 100,
        "basis": basis,
        "duration_range_s": level_range,
    }

    def point(duration: float, speed: float, normative: float, *, measured=False) -> dict:
        meta = metadata(duration) if metadata else {}
        exact = measured or any(math.isclose(duration, value, rel_tol=1e-10) for value in durations)
        within = len(tests) >= 2 and durations[0] <= duration <= durations[-1]
        capped = bool(meta.get("extrapolation_capped", False))
        evidence = ("MEASURED_TEST" if exact else "TEST_SUPPORTED_ESTIMATE" if within
                    else "EXTRAPOLATED" if tests else "ESTIMATED")
        ratio_log = math.log(speed / normative)
        return {
            "duration_s": duration,
            "speed_kmh": speed,
            "reference_speed_kmh": normative,
            "difference_percent": math.expm1(ratio_log) * 100,
            "shape_difference_percent": math.expm1(ratio_log - mean_log) * 100,
            "evidence": evidence,
            "extrapolation_capped": capped,
            # Shape classification uses only observed endpoints, never a grid.
            "used_for_shape": measured and len(tests) >= 2 and duration in (durations[0], durations[-1]),
        }

    result["points"] = [point(*values) for values in raw_points]
    result["test_points"] = [point(test["duration_s"], test["speed_kmh"],
                                   reference.speed(test["duration_s"]) * 3.6, measured=True)
                             for test in tests]
    result["status"] = "TEST_SUPPORTED" if len(tests) >= 2 else "SINGLE_TEST" if tests else "ESTIMATED"
    result["shape"] = {
        "status": "INSUFFICIENT_REAL_TESTS",
        "orientation": None,
        "endurance_contrast_percent": None,
        "test_duration_range_s": [durations[0], durations[-1]] if tests else None,
        "accepted_test_count": len(tests),
        "uncertainty": "NOT_QUANTIFIED",
    }
    if len(tests) >= 2:
        contrast = measured_logs[-1] - measured_logs[0]
        orientation = ("NO_RELATIVE_DIFFERENCE" if abs(contrast) < 1e-10 else
                       "LONGER_DURATION_ADVANTAGE" if contrast > 0 else "SHORTER_DURATION_ADVANTAGE")
        result["shape"].update({
            "status": "TEST_SUPPORTED_WINDOW",
            "orientation": orientation,
            "endurance_contrast_percent": math.expm1(contrast) * 100,
        })
        result["warnings"].append("ORIENTATION_APPLIES_ONLY_TO_TESTED_DURATIONS")
        if len(tests) > 2:
            result["warnings"].append("ENDPOINT_CONTRAST_RETAINS_INTERMEDIATE_TEST_DETAILS")
    else:
        result["warnings"].append("AT_LEAST_TWO_REAL_MAXIMAL_TESTS_FOR_SHAPE")
    if any(p["extrapolation_capped"] for p in result["points"]):
        result["warnings"].append("CORRIDOR_LIMIT_IS_MODEL_CONSTRAINT_NOT_ATHLETE_TRAIT")
    return result
