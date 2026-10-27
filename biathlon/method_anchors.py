"""Canonical prescription anchors; no power-to-speed or HR-percentage conversion.

The source catalogue and scheduler share this resolver. Native coach methods
keep their actual effort definition instead of acquiring a source-study name.
"""
from copy import deepcopy
import math
import re

from . import speed_duration

VERSION = "method-anchors-v1"


def _positive(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def primary_distance(profile):
    text = str(profile.get("discipline") or "").strip().lower()
    if text in {"marathon", "маратон"}:
        return 42195.
    if text in {"half marathon", "half-marathon", "полумаратон"}:
        return 21097.5
    if re.search(r"\d\s*[xх×*]\s*\d", text):
        return None
    matches = list(re.finditer(r"(?<![\w.,+\-])([0-9]+(?:[.,][0-9]+)?)\s*(km|км|m|м)(?!\w)", text))
    if len(matches) != 1:
        return None
    m = matches[0]
    if re.search(r"\d", text[:m.start()] + text[m.end():]):
        return None
    value = float(m[1].replace(",", ".")) * (1000 if m[2] in {"km", "км"} else 1)
    return value if 0 < value <= 1e6 else None


def resolve_speed(spec, profile, view, curve, tests):
    kind = spec.get("type")
    result = deepcopy(spec)
    if kind in {"CV", "CS", "AnT", "ANT"}:
        # A measured/manual threshold belongs to the athlete; a model estimate
        # must never overwrite it. The estimate is only a prescription anchor.
        actual_sport = profile.get("actual_sport") or profile.get("sport")
        threshold = next((item for item in profile.get("ant_references", [])
                          if item.get("sport") == actual_sport and item.get("source") in {"MEASURED", "MANUAL"}), {})
        if kind in {"AnT", "ANT"} and _positive(threshold.get("speed_kmh")):
            return {**result, **deepcopy(threshold), "type": kind,
                    "is_estimate": threshold.get("source") not in {"MEASURED", "MANUAL"},
                    "source": threshold.get("source", "EXPLICIT_ATHLETE_REFERENCE")}
        descriptor = speed_duration.prescription_critical_speed(tests, curve,
            allow_model_estimate=(profile.get("planning_controls") or {}).get("capacity_policy", "MODEL_WITH_PRIOR") == "MODEL_WITH_PRIOR")
        velocity = descriptor["speed_kmh"]
        result["evidence"] = descriptor
        if kind in {"AnT", "ANT"}:
            velocity *= .95
            result.update(source="EXPERT_0_95_CS_ESTIMATE", is_measured_lactate_threshold=False,
                          label="AnT · работна оценка 0,95 × CS")
    elif kind in {"MAS", "V5", "V6", "V_DURATION", "RACE_DURATION"}:
        seconds = {"MAS": 360., "V5": 300., "V6": 360.}.get(kind, spec.get("duration_seconds"))
        if not _positive(seconds):
            raise ValueError("RACE_DURATION_REQUIRED")
        velocity = curve.speed(seconds) * 3.6
        result["duration_seconds"] = seconds
        if kind == "MAS":
            result.update(source="V_360_SECONDS_WORKING_ESTIMATE", is_laboratory_measurement=False,
                          label="MAS · работна оценка V(6 мин)")
    elif kind in {"RACE_DISTANCE", "PRIMARY_RACE"}:
        distance = spec.get("distance_m") or primary_distance(profile)
        if _positive(distance):
            seconds = curve.inverse(distance, distance=True)
            velocity = curve.speed(seconds) * 3.6
            result.update(distance_m=distance, duration_seconds=seconds,
                          inverse_equation="distance_m = duration_seconds * speed_m_per_second")
        elif _positive(profile.get("race_duration_min")):
            seconds = profile["race_duration_min"] * 60
            velocity = curve.speed(seconds) * 3.6
            result.update(duration_seconds=seconds, evidence="PROFILE_RACE_DURATION_ON_CURRENT_CURVE")
        else:
            raise ValueError("PRIMARY_RACE_DISTANCE_OR_DURATION_REQUIRED")
        result["discipline"] = profile.get("discipline")
    else:
        raise ValueError("UNSUPPORTED_OR_NON_SPEED_ANCHOR")
    if not _positive(velocity):
        raise ValueError("INVALID_ANCHOR_SPEED")
    return {**result, "speed_kmh": velocity, "is_estimate": True}


def definition(method):
    """Declare a native card's effort before a personal capacity is resolved."""
    if method["zone"] == "STR":
        return {"type": "STRENGTH_PROFILE", "method_id": method["id"],
                "basis": "WHOLE_SETS_REPETITIONS_RESERVE_AND_INTERNAL_RESTS"}
    interval = method.get("interval_template") or method.get("interval_profile") or {}
    if interval.get("speed_time_duration_ratio") is not None:
        duration = interval["work_seconds"]*interval["speed_time_duration_ratio"]
        if not _positive(duration):
            return {"type": "UNRESOLVED", "reason": "UNSUPPORTED_EFFORT_DURATION"}
        return {"type": "V_DURATION", "duration_seconds": duration,
                "source": "EXPERT_REPETITION_DURATION_COEFFICIENT_ON_CURVE"}
    if interval.get("target_speed_kmh") is not None:
        return {"type": "INDIVIDUAL_SPEED", "speed_kmh": interval["target_speed_kmh"],
                "source": "EXPLICIT_COACH_SPEED"}
    if method.get("structure") == "METABOLIC_INTERVALS":
        return {"type": "COACH_EFFORT", "method_id": method["id"], "source": "INDIVIDUAL_COACH_ASSESSMENT"}
    if method.get("structure") == "MODEL_INTERVALS" and method["zone"] == "Z5":
        return {"type": "INDIVIDUAL_ZONE", "zone": "Z5", "source": "ACTIVE_TEST_OR_ABOVE_ZONE_BOUNDARY_CURVE_CAPACITY"}
    return {"type": "HR_ZONE_POSITION", "zone": method["zone"], "position": method.get("position")}


def describe(method, capacity):
    """Freeze the effort actually used, without changing its definition.

    The resolved capacity remains authoritative for dose and model provenance;
    this small descriptor identifies the anchor, rather than duplicating it.
    """
    if method.get("combined_methods"):
        return {"type": "COMBINED", "parts": [
            {"method_id": part["id"], "zone": part["zone"], "anchor": cap["effort_anchor"]}
            for part, cap in zip(method["combined_methods"], capacity["combined_capacities"])]}
    resolution = (method.get("method_library") or {}).get("resolution") or {}
    if resolution:
        parts = [{"zone": s["zone"], "kind": s["kind"], "anchor": deepcopy(s["anchor"])}
                 for s in resolution.get("segments", []) if s.get("anchor")]
        return {"type": "SOURCE_RECIPE", "parts": parts,
                "anchor": deepcopy(resolution.get("anchor")),
                "standard_work_seconds": resolution.get("standard_work_seconds")}
    if method["zone"] == "STR":
        return definition(method)
    reference = capacity.get("capacity_reference")
    if capacity.get("target_hr_bpm") is not None:
        return {"type": "HR_ZONE_POSITION", "zone": method["zone"],
                "position": method.get("position"),
                "working_bounds_bpm": deepcopy(capacity.get("target_zone_working_bounds_bpm")),
                "target_hr_bpm": capacity["target_hr_bpm"]}
    if reference in {"EXPERT_REPETITION_DURATION_COEFFICIENT_ON_CURVE", "ACTIVE_MAXIMAL_TEST_DURATION"}:
        return {"type": "V_DURATION", "duration_seconds": capacity["capacity_minutes"]*60,
                "source": reference, "speed_kmh": capacity.get("target_speed_kmh")}
    if capacity.get("target_speed_kmh") is not None:
        return {"type": "INDIVIDUAL_SPEED", "speed_kmh": capacity["target_speed_kmh"],
                "source": reference, "speed_role": capacity.get("speed_role")}
    return {"type": "COACH_EFFORT", "method_id": method["id"],
            "source": capacity["capacity_source"], "is_estimate": True}


