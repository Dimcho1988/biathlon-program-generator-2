"""Curve capacity for effort-led automatic methods.

An available individual curve can supply a model estimate outside its measured
window. An independent recent short maximal test is useful provenance, not a
second permission gate. This module does not allocate Q/E or multiply capacity
by a method's interval-work budget.
"""
from copy import deepcopy
import math

from biathlon import dosing_curve


Z5_ANCHOR_REFERENCE_S = 180.  # Existing preferred measured anchor, not a test requirement.
Z5_CURVE_REFERENCE_MAX_S = 600.


def _positive(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def observed_curve_support(tests, duration_seconds):
    """Two accepted independent durations bound an observed-only dose."""
    if not _positive(duration_seconds):
        return False
    durations = {t["duration_s"] for t in tests if t.get("maximal")
                 and t.get("test_mode", "STRICT") == "STRICT"
                 and not t.get("generated") and not t.get("is_estimated")
                 and _positive(t.get("duration_s")) and _positive(t.get("speed_kmh"))}
    return (len(durations) >= 2 and min(durations)-1e-7 <= duration_seconds <= max(durations)+1e-7)


def model_interval_curve_capacity(method, settings, speed, context, *, use_model_prior=False):
    """Continuous Tmax at the method effort from the available curve.

    The caller retains whole repetitions, method ceilings and Q/E checks.
    Only race-specific methods and generic Z5 are handled here; other zones
    use their existing HR-to-time mapping. A curve estimate is never emitted
    as a measured maximal test. Model estimates are an explicit policy;
    observed-only dosing requires two independent accepted test durations
    surrounding the requested continuous capacity.
    """
    predictor, tests, reasons = context
    if (method.get("structure") != "MODEL_INTERVALS"
            or not speed or speed.get("status") not in {"CALIBRATED", "PRELIMINARY"}
            or predictor is None or not getattr(predictor, "curve", None)):
        return None
    # The context may retain a sparse measured window. Invalid/exploratory
    # inputs have no predictor, and cannot gain capacity through this helper.
    if any(reason != "INSUFFICIENT_INDEPENDENT_TEST_DURATIONS" for reason in reasons):
        return None
    curve = predictor.curve
    blended = isinstance(predictor, dosing_curve.Predictor)
    zone = method["zone"]
    band = method.get("race_specific")
    if band:
        if not _positive(band.get("speed_kmh")) or not _positive(band.get("maximum_duration_s")):
            return None
        target = float(band["speed_kmh"])
        try:
            # The band may use the test-only curve while dosing uses 30/70.
            # Re-invert its actual prescribed speed on the selected curve.
            duration = curve.inverse(target / 3.6)
        except ValueError:
            return None
        base = {"capacity_source": "RACE_SPEED_DURATION", "race_specific": deepcopy(band),
                "capacity_confidence": "INDIVIDUAL_CURVE_RACE_EFFORT_ESTIMATE",
                "capacity_reference": "CURVE_INVERSE_AT_PRESCRIBED_RACE_SPEED"}
    elif zone == "Z5":
        try:
            boundary = predictor.speed_for_hr(settings.zone_bounds_bpm[4])
        except (ValueError, IndexError):
            return None
        # Prefer an actual accepted short test when one supports this effort.
        # Age is retained in its payload; it does not disable a current model.
        candidates = [t for t in tests if t.get("maximal")
                      and t.get("test_mode", "STRICT") == "STRICT"
                      and _positive(t.get("duration_s")) and 120 <= t["duration_s"] <= 600
                      and _positive(t.get("speed_kmh")) and t["speed_kmh"] > boundary]
        anchor = min(candidates, key=lambda t: abs(t["duration_s"]-Z5_ANCHOR_REFERENCE_S)) if candidates else None
        try:
            # Z5 is above the actual Z4/Z5 curve boundary. Choose a shorter
            # continuous capacity, without treating it as a measured anchor.
            boundary_duration = predictor.duration(settings.zone_bounds_bpm[4])
        except ValueError:
            return None
        duration = float(anchor["duration_s"]) if anchor else min(Z5_CURVE_REFERENCE_MAX_S, boundary_duration*.5)
        try:
            target = curve.speed(duration) * 3.6
        except ValueError:
            return None
        if target <= boundary:
            return None
        base = {"capacity_source": "SPEED_DURATION_TEST_ANCHOR" if anchor else "SPEED_DURATION_MODEL_CURVE",
                "capacity_confidence": "ACTIVE_MAXIMAL_TEST_CURVE_REFERENCE" if anchor else "INDIVIDUAL_CURVE_ESTIMATE",
                "capacity_reference": "ACTIVE_MAXIMAL_TEST_DURATION" if anchor else "BOUNDARY_HALF_TMAX_MAX600",
                "boundary_speed_kmh": boundary,
                "boundary_capacity_seconds": boundary_duration,
                "boundary_source": predictor.metadata(settings.zone_bounds_bpm[4])["hr_prediction_source"]}
        if anchor:
            base["test_anchor"] = deepcopy(anchor)
    else:
        return None
    if not _positive(duration) or not _positive(target):
        return None
    if not use_model_prior and not observed_curve_support(tests, duration):
        return None
    if method.get("interval_template", {}).get("work_seconds", 0) >= duration:
        return None
    observed = [t["duration_s"] for t in tests if _positive(t.get("duration_s"))]
    within_window = bool(observed) and min(observed) <= duration <= max(observed)
    if blended:
        base.update(capacity_source="BLENDED_DOSING_CURVE", capacity_confidence="COACH_30_70_ESTIMATE")
    return {**base, "capacity_minutes": duration / 60, "target_hr_bpm": None,
            "target_speed_kmh": target,
            "model_version": dosing_curve.VERSION if blended else speed["model_version"],
            "fallback_reasons": list(reasons), "capacity_is_estimate": True,
            "supported_test_duration_s": [min(observed), max(observed)] if observed else None,
            "within_observed_test_window": within_window,
            "speed_role": "FLAT_EQUIVALENT_REFERENCE_NOT_TERRAIN_PACE"}
