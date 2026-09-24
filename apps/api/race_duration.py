"""Approximate event duration from the existing, sport-specific speed curve.

The free-text discipline must contain one unambiguous distance. Model points
already include calibration and volume correction; do not apply either twice.
This is an estimate of moving time, not a course/weather/shooting prediction.
"""
import math
import re


def distance_m(discipline):
    text = str(discipline or "").strip().lower()
    if text in {"marathon", "маратон"}:
        return 42195.
    if text in {"half marathon", "half-marathon", "полумаратон"}:
        return 21097.5
    if re.search(r"\d\s*[xх×*]\s*\d", text):
        return None  # relay/repetitions are not a single race distance
    matches = list(re.finditer(r"(?<![\w.,+\-])([0-9]+(?:[.,][0-9]+)?)\s*(km|км|m|м)(?!\w)", text))
    if len(matches) != 1:
        return None
    match = matches[0]
    # Reject additional distances/numeric qualifiers instead of guessing.
    if re.search(r"\d", text[:match.start()] + text[match.end():]):
        return None
    value = float(match[1].replace(",", ".")) * (1000 if match[2] in {"km", "км"} else 1)
    return value if 0 < value <= 1_000_000 else None


def resolve(profile, view=None):
    distance = distance_m(profile.get("discipline"))
    sport = profile.get("sport")
    result = {"source": "UNAVAILABLE", "duration_min": None, "distance_m": distance,
              "sport": sport, "reason": "DISTANCE_REQUIRED" if distance is None else "CALIBRATED_MODEL_REQUIRED"}
    if distance is not None and view and view.get("sport") == sport and view.get("status") == "CALIBRATED" and view.get("active_test_count", 0) > 0 and not view.get("exploratory_test_count", 0):
        points = view.get("points") or []
        pairs = [(p.get("distance_m"), p.get("duration_s")) for p in points]
        valid = len(pairs) >= 2 and all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and v > 0 for pair in pairs for v in pair)
        if valid and all(a[0] < b[0] and a[1] < b[1] for a,b in zip(pairs, pairs[1:])):
            result["reason"] = "OUTSIDE_MODEL_RANGE"
            for (d0,t0),(d1,t1) in zip(pairs, pairs[1:]):
                if d0 <= distance <= d1:
                    fraction = math.log(distance/d0)/math.log(d1/d0)
                    minutes = math.exp(math.log(t0)+fraction*math.log(t1/t0))/60
                    if 0 < minutes <= 1440:
                        result.update(source="SPEED_DURATION", duration_min=round(minutes, 4), reason=None,
                            model_version=view.get("model_version"), source_generation_id=view.get("source_generation_id"),
                            source_revision=view.get("source_revision"), active_test_keys=view.get("active_test_keys", []),
                            method="LOG_INTERPOLATION_OF_CALIBRATED_DISTANCE", approximate=True)
                    break
    manual = profile.get("race_duration_min")
    if result["duration_min"] is None and isinstance(manual, (float,int)) and not isinstance(manual,bool) and math.isfinite(manual) and 0 < manual <= 1440:
        result.update(source="MANUAL", duration_min=manual)
    return result


def preview(repository, alias, profile):
    from . import model_service
    view = model_service.speed_view(repository, alias, profile["sport"]) if distance_m(profile.get("discipline")) is not None else None
    return resolve(profile, view)


def applied(profile, evidence):
    # Derived time is never written over the user's fallback in their profile.
    return {**profile, "race_duration_min": evidence["duration_min"]}
