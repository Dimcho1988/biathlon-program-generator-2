"""Estimated speed-duration capacity before maximal-test calibration.

Observed zone Q, or an expert position informed by total training time, locates
each zone inside the existing expert *duration* bounds. The two quantities are
kept separate: weekly Q is not a maximum duration or a prescribed session.
Only a compatible paired HR-speed index supplies an absolute speed. Neither
these estimated anchors nor points on the resulting curve are maximal tests.
"""
from __future__ import annotations

from bisect import bisect_right
import math

from .hr_speed import TMAX_RANGES_S
from .load_progression import WEEKLY_Q_BOUNDS, total_volume_position
from .speed_duration import Curve, REFERENCE_TIMES, REFERENCE_SPEEDS, smoothstep

VERSION = "preliminary-capacity-expert-time-paired-v2"


def _number(value, *, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (value > 0 if positive else value >= 0))


def _position(value):
    if not _number(value) or value > 1:
        raise ValueError("Expert duration positions must lie between zero and one")
    return float(value)


def _hr_profile(bounds, hrmax):
    return (_number(hrmax, positive=True) and isinstance(bounds, (list, tuple))
            and len(bounds) == 6 and all(_number(v) for v in bounds)
            and all(a < b for a, b in zip(bounds, bounds[1:])))


def _paired_index(band):
    """Require actual support; the caller also checks sport/configuration/date.

    ``index_summary`` from model_service has count and paired seconds. Explicit
    model-derived sources are rejected even if they imitate those fields.
    """
    return (isinstance(band, dict) and band.get("valid") is not False
            and band.get("source") in (None, "PAIRED_RAW_HR_VFLAT", "PAIRED_HR_VFLAT")
            and not band.get("is_estimated") and not band.get("generated")
            and _number(band.get("index"), positive=True)
            and _number(band.get("count"), positive=True)
            and _number(band.get("seconds"), positive=True))


def _curve(anchors):
    reference = Curve(REFERENCE_TIMES, REFERENCE_SPEEDS)
    points = sorted((a["duration_s"], a["speed_kmh"] / 3.6) for a in anchors)
    if any(t1 >= t2 or not v1 > v2 or not t1*v1 < t2*v2
           for (t1, v1), (t2, v2) in zip(points, points[1:])):
        raise ValueError("Estimated anchors conflict")
    log_times = [math.log(t) for t, _ in points]
    log_ratios = [math.log(v / reference.speed(t)) for t, v in points]
    if max(log_ratios) - min(log_ratios) < 1e-12:
        # One anchor, or a uniform scale, preserves the normative interpolation.
        ratio = math.exp(log_ratios[0])
        result = Curve(REFERENCE_TIMES, tuple(v*ratio for v in REFERENCE_SPEEDS))
    else:
        def ratio_at(t):
            x = math.log(t)
            if x <= log_times[0]:
                return math.exp(log_ratios[0])
            if x >= log_times[-1]:
                return math.exp(log_ratios[-1])
            i = bisect_right(log_times, x) - 1
            w = smoothstep((x-log_times[i]) / (log_times[i+1]-log_times[i]))
            return math.exp((1-w)*log_ratios[i] + w*log_ratios[i+1])

        # Individualize the normative knots, retaining every estimated anchor.
        # Curve validates -1 < d(log v)/d(log t) < 0 over every interval, not
        # just sampled chart points, and supplies C1 joins and both inverses.
        estimated = dict(points)
        times = tuple(sorted(set(REFERENCE_TIMES) | set(estimated)))
        speeds = tuple(estimated[t] if t in estimated else reference.speed(t)*ratio_at(t) for t in times)
        result = Curve(times, speeds)
    object.__setattr__(result, "calibration_mode", "PRELIMINARY_HR_HISTORY")
    object.__setattr__(result, "model_version", VERSION)
    return result


def build(bounds, hrmax, indices, *, zone_weekly_q=None, positions=None,
          position_basis=None, position_overrides=None, total_weekly_minutes=None):
    """Return duration estimates, diagnostics, and an optional absolute curve.

    ``zone_weekly_q`` contains *measured* equivalent minutes per week only;
    absent/None means unknown, whereas zero means observed zero exposure.
    ``positions`` supplies explicit fallback positions. Otherwise actual total
    ``total_weekly_minutes`` uses the existing 4–20 h coaching placement, with
    expert minimum times when no volume is known. This estimates capacity;
    the planner independently applies its conservative experience/dose rules.
    Explicit coach ``position_overrides`` take precedence over both sources.
    ``indices`` must already be restricted to compatible paired observations.

    Conflicting estimates remain visible and produce no absolute curve. We do
    not repair them by reordering zones, inventing test results, or averaging
    with hard anchors. Real-test calibration belongs to speed_duration.
    """
    zone_weekly_q, positions = zone_weekly_q or {}, positions or {}
    position_overrides, indices = position_overrides or {}, indices or {}
    total_position = total_volume_position(total_weekly_minutes) if total_weekly_minutes is not None else None
    valid_hr = _hr_profile(bounds, hrmax)
    anchors = []
    warnings = [] if valid_hr else ["HR_PROFILE_REQUIRED_FOR_ABSOLUTE_SPEED"]
    for i, (zone, (minimum, maximum)) in enumerate(TMAX_RANGES_S.items()):
        weekly_q = zone_weekly_q.get(zone)
        if weekly_q is not None and not _number(weekly_q):
            raise ValueError("Measured zone Q must be finite and nonnegative")
        if zone in position_overrides:
            position = _position(position_overrides[zone])
            duration_source = "COACH_POSITION"
        elif weekly_q is not None:
            q_low, q_high = WEEKLY_Q_BOUNDS[zone]
            position = min(1., max(0., (weekly_q-q_low)/(q_high-q_low)))
            duration_source = "MEASURED_ZONE_Q_POSITION"
        elif zone in positions:
            position = _position(positions[zone])
            duration_source = "EXPERT_POSITION"
        elif total_position is not None:
            position = total_position
            duration_source = "TOTAL_VOLUME_ESTIMATE"
        else:
            position = 0.
            duration_source = "EXPERT_MINIMUM"
        duration = minimum + (maximum-minimum)*position
        band = indices.get(zone) or {}
        valid_index = valid_hr and _paired_index(band)
        speed = (100*bounds[i+1]/hrmax)/band["index"] if valid_index else None
        if speed is not None and not _number(speed, positive=True):
            valid_index, speed = False, None
        anchors.append({"zone": zone, "kind": "ESTIMATE", "is_maximal_test": False,
                        "duration_s": duration, "duration_min_s": minimum, "duration_max_s": maximum,
                        "duration_position": position, "duration_source": duration_source,
                        "measured_weekly_q": weekly_q,
                        "hr_bpm": bounds[i+1] if valid_hr else None,
                        "speed_kmh": speed, "speed_source": "PAIRED_INDEX" if valid_index else None,
                        "index": band["index"] if valid_index else None,
                        "count": band["count"] if valid_index else 0,
                        "seconds": band["seconds"] if valid_index else 0,
                        "reason": None if valid_index else "NO_VALID_PAIRED_INDEX"})

    conflicting_zones = [[a["zone"], b["zone"]] for a, b in zip(anchors, anchors[1:])
                         if a["duration_s"] <= b["duration_s"]]
    usable = [a for a in anchors if a["speed_kmh"] is not None]
    curve = None
    if conflicting_zones:
        warnings.append("CONFLICTING_EXPERT_DURATION_ESTIMATES")
    elif usable:
        try:
            curve = _curve(usable)
        except ValueError:
            warnings.append("CONFLICTING_ESTIMATED_SPEED_DURATION_ANCHORS")
    else:
        warnings.append("ABSOLUTE_SPEED_EVIDENCE_REQUIRED")
    return {"version": VERSION, "status": "PRELIMINARY" if curve else "DURATION_ONLY",
            "curve": curve, "anchors": anchors, "warnings": warnings,
            "position_basis": position_basis, "conflicting_zones": conflicting_zones,
            "total_weekly_minutes": total_weekly_minutes,
            "speed_anchor_count": len(usable), "real_test_count": 0,
            "z5_duration_source": "Z4_SHARED_BOUNDARY",
            "duration_is_training_dose": False, "is_measured_zone_distribution": False}


def summary(result):
    """Serializable provenance; never emit the curve as new measurements."""
    return {key: value for key, value in result.items() if key != "curve"}


def curve_from_summary(value):
    """Rebuild the same preliminary shape for planning, without re-estimation."""
    if not value or value.get("version") != VERSION or value.get("status") != "PRELIMINARY":
        return None
    anchors = value.get("anchors") or []
    if ([a.get("zone") for a in anchors] != list(TMAX_RANGES_S)
            or any(a.get("kind") != "ESTIMATE" or a.get("is_maximal_test") is not False
                   or not _number(a.get("duration_s"), positive=True)
                   or not TMAX_RANGES_S[a["zone"]][0] <= a["duration_s"] <= TMAX_RANGES_S[a["zone"]][1]
                   for a in anchors)
            or any(a["duration_s"] <= b["duration_s"] for a, b in zip(anchors, anchors[1:]))):
        raise ValueError("Invalid preliminary capacity provenance")
    usable = [a for a in anchors if a.get("speed_kmh") is not None]
    if not usable or any(not _number(a["speed_kmh"], positive=True)
                         or a.get("speed_source") != "PAIRED_INDEX" for a in usable):
        raise ValueError("Preliminary absolute speed requires paired observations")
    return _curve(usable)


def calibration_diagnostics(result, tests):
    """Expose disagreements without inventing reliability weights.

    Paired seconds and counts describe sampling support, not a calibrated error
    variance comparable with maximal tests. They cannot justify an automatic
    numeric blend. Zero/one-test modes already use the prior; two real anchors
    retain the separately verified normative fit and exact test constraints.
    """
    curve = result.get("curve")
    residuals = []
    if curve:
        for test in tests:
            t, measured = test["duration_s"], test["speed_kmh"]
            predicted = curve.speed(t)*3.6
            residuals.append({"duration_s": t, "measured_speed_kmh": measured,
                              "prior_speed_kmh": predicted,
                              "measured_vs_prior_percent": 100*(measured/predicted-1)})
    return {"residuals": residuals, "real_test_anchors_take_precedence": True,
            "prior_shape_used": bool(curve) and len(tests) <= 1,
            "blend_status": "NOT_APPLIED",
            "blend_reason": "NO_VALIDATED_RELIABILITY_WEIGHTS" if residuals else "NO_COMPARABLE_PRIOR_AND_TEST",
            "sampling_counts_are_confidence_weights": False}
