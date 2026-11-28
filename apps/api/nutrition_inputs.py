"""Minimal body inputs reused from the existing provider profile request."""
from datetime import date, timedelta
from math import isfinite


def provider_body_inputs(payload, observed_on, previous=None):
    if not isinstance(payload, dict):
        return None
    result = {"observed_on": str(observed_on)}
    sex = str(payload.get("sex") or "").upper()
    if sex in {"M", "MALE", "F", "FEMALE"}:
        result["sex"] = "MALE" if sex in {"M", "MALE"} else "FEMALE"
    for source, target, low, high in (("icu_weight", "weight_kg", 20, 250),
                                     ("weight", "weight_kg", 20, 250),
                                     ("height", "height_cm", 100, 230)):
        value = payload.get(source)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value):
            if low < value <= high and target not in result:
                result[target] = float(value)
    try:
        born = date.fromisoformat(str(payload.get("icu_date_of_birth")))
        if 1900 <= born.year <= observed_on.year and 0 <= (observed_on-born).days <= 36600:
            result["birth_year"] = born.year
    except (ValueError, TypeError):
        pass
    previous = previous or {}
    observations = list(previous.get("weight_observations") or [])
    if not observations and previous.get("weight_kg") and previous.get("observed_on"):
        observations.append({"day": previous["observed_on"], "weight_kg": previous["weight_kg"]})
    if result.get("weight_kg") and (not observations or observations[-1]["weight_kg"] != result["weight_kg"]):
        observations = [r for r in observations if r["day"] != str(observed_on)]
        observations.append({"day": str(observed_on), "weight_kg": result["weight_kg"]})
    cutoff = str(observed_on - timedelta(days=90))
    earlier = [r for r in observations if r["day"] < cutoff]
    observations = earlier[-1:] + [r for r in observations if r["day"] >= cutoff]
    if observations:
        result["weight_observations"] = observations
    # Never persist the raw profile, identity, credentials or full birth date.
    return result if len(result) > 1 else None
