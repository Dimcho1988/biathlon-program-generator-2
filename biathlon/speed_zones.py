"""Speed exposure with explicit boundary provenance, independent of HR samples."""
from bisect import bisect_right
import math

VERSION = "individual-speed-zones-v1"


def build(curve, predictor, tests, cs=None):
    result = {"version": VERSION, "status": "UNAVAILABLE", "zones": [],
              "basis": "INDIVIDUAL_CURVE_AND_ZONE_TIME_REFERENCES",
              "speed_role": "FLAT_EQUIVALENT", "is_measured_hr": False}
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
        upper = speeds[i] if i < 4 else None
        anchor = anchors[min(i,3)]
        zones.append({"zone": f"Z{i+1}", "low_kmh": lower, "high_kmh": upper,
                      "source": "PAIRED_INDEX" if anchor["source"] == "INDEX" else "CURVE_EXPERT_TIME",
                      "boundary_source": anchor["source"],
                      "upper_percent_cs": 100*upper/cs_speed if upper is not None and cs_speed else None})
        lower = upper
    short = [t for t in tests if t.get("maximal") and t.get("test_mode", "STRICT") == "STRICT"
             and 120 <= t["duration_s"] <= 600]
    ceiling = max((t["speed_kmh"] for t in short), default=None)
    return {**result, "status": "AVAILABLE", "zones": zones,
            "supported_low_kmh": predictor.speed_range[0],
            "supported_z5_high_kmh": ceiling if ceiling and ceiling > speeds[-1] else None,
            "cs_kmh": cs_speed, "accepted_test_count": len(tests),
            "note": "Скоростните зони са индивидуални ориентири. Времето по скорост е измерена външна работа; приравненият товар от него е оценка."}


def classify(profile, speed):
    zones = (profile or {}).get("zones") or []
    if len(zones) != 5 or not isinstance(speed,(float,int)) or not math.isfinite(speed) or speed <= 0:
        return None
    return zones[min(4, bisect_right([z["high_kmh"] for z in zones[:4]], speed))]["zone"]
