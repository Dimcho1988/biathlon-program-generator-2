"""Approved initial speed policy, kept separate from measured calibration.

The heuristic threshold is conditionally treated as V50. One uniform scale
preserves the existing normative curve and its ten-second continuation. The
existing V50/V6 zone policy supplies the boundaries; no observations, paired
indices or new physiological coefficients are manufactured.
"""
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
import json

from . import initial_threshold, speed_duration, zone_profile


VERSION = initial_threshold.VERSION
ZONE_POLICY_VERSION = "initial-heuristic-v50-reference-scale-v1"
CALIBRATION_MODE = "HEURISTIC_REFERENCE_SCALE"
SOURCE = "INITIAL_HEURISTIC_V50_REFERENCE_SCALE"
SPEED_ROLE = "RAW_MOVING_SPEED"
V50_DURATION_S = 3000.


class Curve:
    """Uniform normative scaling with exclusively estimated provenance."""
    model_version = VERSION
    calibration_mode = CALIBRATION_MODE
    zone_policy_version = ZONE_POLICY_VERSION
    speed_role = SPEED_ROLE
    calibration_eligible = False
    is_estimated = True

    def __init__(self, estimated_v50_kmh):
        self.v50_kmh = speed_duration.positive(estimated_v50_kmh)
        self.reference = speed_duration.with_ten_second_tail(
            speed_duration.Curve(speed_duration.REFERENCE_TIMES, speed_duration.REFERENCE_SPEEDS))
        self.ratio = self.v50_kmh/3.6/self.reference.speed(V50_DURATION_S)
        self.times, self._x = self.reference.times, self.reference._x
        self.speeds = tuple(speed_duration.positive(self.speed(t)) for t in self.times)

    def speed(self, t):
        return self.reference.speed(t)*self.ratio

    def distance(self, t):
        return t*self.speed(t)

    def log_slope(self, t):
        return self.reference.log_slope(t)

    def inverse(self, value, *, distance=False):
        return speed_duration.Curve.inverse(self, value, distance=distance)

    def point_metadata(self, t):
        self.speed(t)
        return {"calibration_mode": CALIBRATION_MODE, "evidence": "INITIAL_HEURISTIC_ESTIMATE",
                "is_estimated": True, "calibration_eligible": False, "speed_role": SPEED_ROLE,
                "within_test_window": False, "conditional_v50": t == V50_DURATION_S,
                "extrapolated": t != V50_DURATION_S, "additional_correction": 0.,
                "extrapolation_capped": False, "short_tail_extension": t < speed_duration.REFERENCE_TIMES[0]}


def _valid_estimate(estimate):
    return (isinstance(estimate, Mapping) and estimate.get("status") == "AVAILABLE"
            and estimate.get("use_status") != "SUPERSEDED"
            and estimate.get("model_version") == VERSION and estimate.get("speed_role") == SPEED_ROLE
            and estimate.get("is_estimated") is True and estimate.get("calibration_eligible") is False
            and initial_threshold._number(estimate.get("v_threshold_estimated_kmh")))


def build(estimate, bounds, hrmax, *, sport="Run", reliable_anchor=None):
    """Return a runtime curve and serializable profile only for fallback use.

    The caller validates individual evidence first; a reliable TESTS/INDEX
    anchor suppresses this policy. ``bounds`` and ``hrmax`` use the same common
    HR reference frame expected by ``zone_profile.build``. No calibration or
    test-writing function is called.
    """
    result = {"status": "UNAVAILABLE", "reason": None, "curve": None, "speed_zones": None,
              "model_version": VERSION, "zone_policy_version": ZONE_POLICY_VERSION,
              "calibration_mode": CALIBRATION_MODE, "normative_version": speed_duration.NORMATIVE_VERSION,
              "source": SOURCE, "speed_role": SPEED_ROLE, "is_estimated": True,
              "calibration_eligible": False, "accepted_test_count": 0, "conditional_v50": None}
    if reliable_anchor in {"TESTS", "INDEX"} or (isinstance(estimate, Mapping)
                                                 and estimate.get("use_status") == "SUPERSEDED"):
        return {**result, "status": "SUPERSEDED", "reason": "RELIABLE_INDIVIDUAL_ANCHOR_AVAILABLE"}
    if not _valid_estimate(estimate):
        reason = estimate.get("reason") if isinstance(estimate, Mapping) and estimate.get("status") != "AVAILABLE" else None
        return {**result, "reason": reason or "VALID_INITIAL_THRESHOLD_REQUIRED"}
    try:
        curve = Curve(estimate["v_threshold_estimated_kmh"])
        profile = zone_profile.build(curve, bounds, hrmax, {}, sport=sport, tests=())
    except (ArithmeticError, ValueError, TypeError):
        return {**result, "reason": "INVALID_INITIAL_SPEED_PROFILE"}
    if profile["status"] != "AVAILABLE":
        return {**result, "reason": profile.get("reason") or "INITIAL_ZONE_PROFILE_UNAVAILABLE"}
    conditional = {"duration_s": V50_DURATION_S, "speed_kmh": curve.v50_kmh,
                   "role": "CONDITIONAL_THRESHOLD_AS_V50", "is_measured": False}
    profile.update(zone_policy_version=ZONE_POLICY_VERSION, model_version=VERSION, source=SOURCE,
                   basis=SOURCE, speed_role=SPEED_ROLE, is_estimated=True, calibration_eligible=False,
                   conditional_v50=conditional, normative_version=speed_duration.NORMATIVE_VERSION,
                   accepted_test_count=0,
                   note="Начална моделна оценка: прагът е приет условно за V50; V6, V10 и зоните следват референтната крива.")
    for row in profile["zones"]:
        row.update(source=SOURCE, boundary_source=SOURCE, speed_role=SPEED_ROLE,
                   is_estimated=True, calibration_eligible=False)
    profile["v10"].update(source=SOURCE, evidence="ESTIMATED", is_estimated=True,
                          calibration_eligible=False, speed_role=SPEED_ROLE,
                          reason="NORMATIVE_TEN_SECOND_CONTINUATION_NOT_MEASURED_SPRINT")
    profile.pop("parameters_version", None)
    profile["parameters_version"] = sha256(json.dumps(profile, sort_keys=True, allow_nan=False,
                                                      separators=(",", ":")).encode()).hexdigest()
    return {**result, "status": "AVAILABLE", "curve": curve, "speed_zones": profile,
            "conditional_v50": conditional}


def summary(result):
    """Serializable policy provenance; the view publishes speed_zones separately."""
    return deepcopy({key: value for key, value in result.items() if key not in {"curve", "speed_zones"}})


def curve_from_view(view):
    """Reconstruct only the explicitly active raw-speed initial policy."""
    if not isinstance(view, Mapping) or view.get("status") != "INITIAL_HEURISTIC" or view.get("speed_role") != SPEED_ROLE:
        return None
    estimate, policy = view.get("initial_threshold"), view.get("initial_speed_profile")
    if (not _valid_estimate(estimate) or estimate.get("used_for_zones") is not True
            or estimate.get("use_status") != "ZONES" or not isinstance(policy, Mapping)
            or policy.get("status") != "AVAILABLE" or policy.get("model_version") != VERSION
            or policy.get("zone_policy_version") != ZONE_POLICY_VERSION
            or policy.get("normative_version") != speed_duration.NORMATIVE_VERSION
            or policy.get("calibration_mode") != CALIBRATION_MODE or policy.get("source") != SOURCE
            or policy.get("speed_role") != SPEED_ROLE or policy.get("is_estimated") is not True
            or policy.get("calibration_eligible") is not False or policy.get("accepted_test_count") != 0):
        return None
    conditional = policy.get("conditional_v50")
    if (not isinstance(conditional, Mapping) or conditional.get("duration_s") != V50_DURATION_S
            or conditional.get("role") != "CONDITIONAL_THRESHOLD_AS_V50" or conditional.get("is_measured") is not False
            or conditional.get("speed_kmh") != estimate["v_threshold_estimated_kmh"]):
        return None
    try:
        return Curve(estimate["v_threshold_estimated_kmh"])
    except (ArithmeticError, ValueError, TypeError):
        return None
