"""Speed exposure with explicit boundary provenance, independent of HR samples."""
from bisect import bisect_right
import math

VERSION = "individual-speed-zones-v2-v10-provenance"


def v10_anchor(curve, tests):
    """Maximal ten-second mean only; never a single GPS peak or V(10.8 s)."""
    from .speed_duration import with_ten_second_tail
    curve = with_ten_second_tail(curve)
    exact = [t for t in tests if t.get("maximal") is True
             and t.get("test_mode", "STRICT") == "STRICT"
             and t.get("comparable") is not False and not t.get("generated") and not t.get("is_estimated")
             and isinstance(t.get("duration_s"), (int, float)) and abs(t["duration_s"]-10.) < 1e-8
             and isinstance(t.get("speed_kmh"), (int, float)) and math.isfinite(t["speed_kmh"]) and t["speed_kmh"] > 0
             and (t.get("source") == "MANUAL" or t.get("sprint_speed_validated") is True)]
    if exact:
        chosen = max(exact, key=lambda t: t["speed_kmh"])
        return {"status": "AVAILABLE", "duration_s": 10., "speed_kmh": chosen["speed_kmh"],
                "source": "MAXIMAL_10_SECOND_MEAN_TEST", "evidence": "MEASURED",
                "extrapolated": False, "start_type": chosen.get("start_type", "UNKNOWN"),
                "test_source": chosen.get("source"), "is_gps_peak": False}
    result = {"status": "UNAVAILABLE", "duration_s": 10., "speed_kmh": None,
              "source": "INDIVIDUAL_CURVE", "evidence": "UNAVAILABLE", "extrapolated": None,
              "start_type": "UNKNOWN", "is_gps_peak": False}
    if curve is None:
        return {**result, "reason": "INDIVIDUAL_CURVE_REQUIRED"}
    try:
        value = curve.speed(10.)*3.6
    except (ValueError, ArithmeticError):
        return {**result, "reason": "TEN_SECONDS_OUTSIDE_CURVE_DOMAIN",
                "curve_duration_range_s": [curve.times[0], curve.times[-1]]}
    if not math.isfinite(value) or value <= 0:
        return {**result, "reason": "INVALID_TEN_SECOND_PREDICTION"}
    measured = [t["duration_s"] for t in tests if t.get("maximal") is True and t.get("test_mode", "STRICT") == "STRICT"
                and not t.get("generated") and not t.get("is_estimated") and t.get("comparable") is not False
                and (abs(t["duration_s"]-10.) >= 1e-8 or t.get("source") == "MANUAL" or t.get("sprint_speed_validated") is True)]
    extrapolated = not measured or not min(measured) <= 10. <= max(measured)
    return {**result, "status": "AVAILABLE", "speed_kmh": value,
            "evidence": "EXTRAPOLATED" if extrapolated else "INTERPOLATED", "extrapolated": extrapolated,
            "reason": "MODEL_ESTIMATE_NOT_MEASURED_SPRINT"}


def build(curve, predictor, tests, cs=None):
    if getattr(predictor, "profile", None):
        from copy import deepcopy
        result = deepcopy(predictor.profile)
        # A measured V10 is a boundary observation, never a GPS peak or a
        # calibration point fabricated from the inferred HR relationship.
        v10 = v10_anchor(curve, tests)
        if (v10.get("evidence") == "MEASURED" or (result.get("v10") or {}).get("status") != "AVAILABLE") and v10.get("status") == "AVAILABLE" and v10["speed_kmh"] > result["zones"][-1]["low_kmh"]:
            result["v10"] = v10
            result["zones"][-1]["high_kmh"] = v10["speed_kmh"]
            result["bounds_kmh"][-1] = v10["speed_kmh"]
        result["cs_kmh"] = (cs or {}).get("speed_kmh")
        return result
    result = {"version": VERSION, "status": "UNAVAILABLE", "zones": [],
              "basis": "INDIVIDUAL_CURVE_AND_ZONE_TIME_REFERENCES",
              "speed_role": "FLAT_EQUIVALENT", "is_measured_hr": False, "v10": v10_anchor(curve, tests)}
    if curve is None or predictor is None:
        return result
    anchors = predictor.summary()["zones"][:4]
    speeds = [a["speed_kmh"] for a in anchors]
    if not all(math.isfinite(v) and v > 0 for v in speeds) or not all(a < b for a,b in zip(speeds,speeds[1:])):
        return {**result, "status": "CONFLICTING_BOUNDARIES"}
    cs_speed = (cs or {}).get("speed_kmh")
    lower = 0.
    zones = []
    for i in range(5):
        upper = speeds[i] if i < 4 else result["v10"].get("speed_kmh")
        anchor = anchors[min(i,3)]
        zones.append({"zone": f"Z{i+1}", "low_kmh": lower, "high_kmh": upper,
                      "source": "PAIRED_INDEX" if anchor["source"] == "INDEX" else "CURVE_EXPERT_TIME",
                      "boundary_source": anchor["source"],
                      "upper_reference_duration_s": 10. if i == 4 else None,
                      "upper_percent_cs": 100*upper/cs_speed if upper is not None and cs_speed else None})
        lower = upper
    short = [t for t in tests if t.get("maximal") and t.get("test_mode", "STRICT") == "STRICT"
             and 120 <= t["duration_s"] <= 600]
    ceiling = max((t["speed_kmh"] for t in short), default=None)
    return {**result, "status": "AVAILABLE", "zones": zones,
            "supported_low_kmh": predictor.speed_range[0],
            "supported_z5_high_kmh": ceiling if ceiling and ceiling > speeds[-1] else None,
            "supported_z5_high_basis": "LEGACY_MISSING_HR_SUPPORT_120_TO_600_SECONDS_NOT_V10",
            "cs_kmh": cs_speed, "accepted_test_count": len(tests),
            "note": "Скоростните зони са индивидуални ориентири. Времето по скорост е измерена външна работа; приравненият товар от него е оценка."}


def classify(profile, speed):
    zones = (profile or {}).get("zones") or []
    if len(zones) != 5 or not isinstance(speed,(float,int)) or not math.isfinite(speed) or speed <= 0:
        return None
    return zones[min(4, bisect_right([z["high_kmh"] for z in zones[:4]], speed))]["zone"]
