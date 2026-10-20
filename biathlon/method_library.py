"""Source-preserving training recipes resolved on the approved dosing curve.

A prescription range is an explicit coaching choice, never an inference that
fitness improves with the calendar. The preview and weekly planner share this
resolver. Power/qualitative references do not acquire speed prescriptions.
"""
from copy import deepcopy
import json
from hashlib import sha256
from functools import lru_cache
import math
from pathlib import Path
import re

from . import method_dosing, dosing_curve, preliminary_capacity, speed_duration, speed_zones, method_progression, hr_speed

VERSION = "training-method-library-v6-source-standard"
CATALOG_PATH = Path(__file__).parent / "data" / "training_method_library.json"
PHASE_POSITION = {"RE_ENTRY": 0., "GENERAL_PREPARATION": 0., "SPECIAL_PREPARATION": .5,
                  "PRECOMPETITION": 1., "COMPETITION": 1., "TRANSITION": 0.}
_CACHE = (None, None)


def entries():
    global _CACHE
    if not CATALOG_PATH.exists():
        return []
    stamp = CATALOG_PATH.stat().st_mtime_ns
    if _CACHE[0] != stamp:
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        rows = payload["methods"] if isinstance(payload, dict) else payload
        if len({r["id"] for r in rows}) != len(rows):
            raise ValueError("Duplicate training method IDs")
        _CACHE = (stamp, rows)
    return deepcopy(_CACHE[1])


def provenance():
    """Content identity invalidates saved plans after a source-catalog edit."""
    if not CATALOG_PATH.exists():
        return {"version": VERSION, "catalog_version": None, "sha256": None, "method_count": 0}
    raw = CATALOG_PATH.read_bytes()
    payload = json.loads(raw)
    return {"version": VERSION, "catalog_version": payload.get("version"),
            "sha256": sha256(raw).hexdigest(), "method_count": len(payload.get("methods", []))}


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _range(value, name, *, zero=False):
    if isinstance(value, (int, float)):
        value = [value, value]
    if (not isinstance(value, (list, tuple)) or len(value) != 2
        or any(not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v)
               or (v < 0 if zero else v <= 0) for v in value)
        or value[0] > value[1]):
        raise ValueError("INVALID_" + name.upper())
    return [float(value[0]), float(value[1])]


def validate_overrides(config):
    by_id = {m["id"]: m for m in entries()}
    for identifier, override in (config.get("overrides") or {}).items():
        if identifier not in by_id:
            raise ValueError("Unknown training method: " + identifier)
        if any(k != "enabled" for k in override) and not by_id[identifier].get("prescription"):
            raise ValueError("Reference-only method cannot receive an executable override")
        for key in ("speed_multiplier", "work_range", "repetitions"):
            if key in override:
                _range(override[key], key)
    return config


def selected_curve(view):
    if not view or view.get("status") not in {"CALIBRATED", "PRELIMINARY"}:
        raise ValueError("NO_INDIVIDUAL_SPEED_CURVE")
    keys = set(view.get("active_test_keys", []))
    tests = [r["payload"] for r in view.get("tests", []) if r.get("entry_key") in keys]
    if any(not t.get("maximal") or t.get("test_mode", "STRICT") != "STRICT"
           or t.get("generated") or t.get("is_estimated") for t in tests):
        raise ValueError("EXPLORATORY_OR_NONMAXIMAL_TESTS")
    blend = dosing_curve.from_view(view)
    if blend:
        return blend, tests, "BLENDED_DOSING_CURVE"
    prior = preliminary_capacity.curve_from_summary(view.get("preliminary_capacity") or {})
    if not tests and prior is None:
        raise ValueError("NO_INDIVIDUAL_SPEED_CURVE")
    return speed_duration.calibrated(tests, prior=prior), tests, "INDIVIDUAL_TEST_CURVE" if tests else "INDIVIDUAL_PRIOR_CURVE"


class _CachedCurve:
    """Request-local memoization: no athlete data survives into another request."""
    def __init__(self, curve):
        self._curve = curve
        self.speed = lru_cache(maxsize=2048)(curve.speed)
        self.inverse = lru_cache(maxsize=2048)(curve.inverse)

    def __getattr__(self, key):
        return getattr(self._curve, key)


def _prepare(view):
    curve, tests, source = selected_curve(view)
    policy = (view or {}).get("sport_hr_policy") or {}
    predictor = None
    try:
        predictor_type = dosing_curve.Predictor if source == "BLENDED_DOSING_CURVE" else hr_speed.Predictor
        durations = {a["zone"]: a["duration_s"] for a in (view.get("preliminary_capacity") or {}).get("anchors", [])}
        predictor = predictor_type(curve, policy["sport_zone_bounds_bpm"], policy["sport_hrmax_bpm"], view.get("index_summary", {}),
            expert_durations=durations or None, normalization_offset_bpm=policy.get("offset_bpm", 0))
        predictor.hr_for_speed = lru_cache(maxsize=2048)(predictor.hr_for_speed)
    except (ValueError, KeyError, TypeError):
        pass
    return (_CachedCurve(curve), tests, source), predictor


def _hr_reference(segment, predictor):
    """Secondary reference only: never synthesize measured HR or %CS = %HRmax."""
    if predictor is None:
        return {"hr_reference_range_bpm": None, "hr_reference_basis": "INDIVIDUAL_HR_PROFILE_REQUIRED"}
    try:
        values = [predictor.hr_for_speed(v) for v in segment["target_speed_range_kmh"]]
        basis = "INDIVIDUAL_SPEED_HR_MODEL_ESTIMATE"
    except ValueError:
        index = int(segment["zone"][1:])-1
        values = list(predictor.bounds[index:index+2])
        values[1] = min(values[1], predictor.hrmax)
        basis = "INDIVIDUAL_ZONE_OBSERVATION_NOT_SPEED_PREDICTION"
    return {"hr_reference_range_bpm": [math.floor(values[0]), math.ceil(values[1])],
            "hr_reference_basis": basis, "hr_role": "SECONDARY_OBSERVATION_WITH_LAG" if segment["work_duration_seconds"] < 180 else "SECONDARY_OBSERVATION",
            "hr_is_measured": False}


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


def _anchor(spec, profile, view, curve, tests):
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


def _segment(spec, base, profile, view, curve, tests, position, zone_profile):
    anchor = _anchor(spec.get("anchor", base.get("anchor", {})), profile, view, curve, tests)
    multipliers = _range(spec.get("speed_multiplier", base.get("speed_multiplier")), "speed_multiplier")
    work = spec.get("work", base.get("work", {})) or {}
    fallback_duration = not work.get("range") and base.get("structure") == "CONTINUOUS"
    if fallback_duration:
        work = {"unit": "capacity_fraction", "range": [.65, .65]}
    works = _range(work.get("range"), "work_range")
    # Couple shorter work to the upper speed bound only where this recipe
    # actually declares a variable work range. A fixed 8-minute method does
    # not automatically slow down merely because the calendar phase changes.
    speed_position = position if works[0] != works[1] else .5
    multiplier = multipliers[1] - speed_position * (multipliers[1] - multipliers[0])
    amount = works[0] + position * (works[1] - works[0])
    velocity = anchor["speed_kmh"] * multiplier
    velocities = [anchor["speed_kmh"] * v for v in multipliers]
    offset = spec.get("pace_offset", base.get("pace_offset"))
    if offset:
        reference = offset.get("distance_m")
        values = offset.get("seconds")
        if (not _positive(reference) or not isinstance(values, list) or len(values) != 2
            or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in values)
            or values[0] > values[1]):
            raise ValueError("INVALID_PACE_OFFSET")
        def shifted(v, delta):
            seconds = reference / (v / 3.6) + delta
            if seconds <= 0:
                raise ValueError("INVALID_PACE_OFFSET")
            return reference / seconds * 3.6
        velocities = sorted(shifted(v, d) for v in velocities for d in values)
        velocities = [velocities[0], velocities[-1]]
        velocity = shifted(velocity, sum(values)/2)
        anchor["pace_offset"] = deepcopy(offset)
    if work.get("unit") == "seconds":
        seconds, seconds_range = amount, works
        distance = velocity / 3.6 * seconds
        # The endpoints describe the paired short/fast and long/slow options.
        distance_range = sorted([velocities[1] / 3.6 * works[0], velocities[0] / 3.6 * works[1]])
    elif work.get("unit") == "meters":
        distance, distance_range = amount, works
        seconds = amount / (velocity / 3.6)
        seconds_range = [works[0] / (velocities[1] / 3.6), works[1] / (velocities[0] / 3.6)]
    elif work.get("unit") == "capacity_fraction":
        capacity = curve.inverse(velocity / 3.6)
        seconds = amount * capacity
        seconds_range = [works[0] * capacity, works[1] * capacity]
        distance, distance_range = velocity / 3.6 * seconds, None
    else:
        raise ValueError("UNKNOWN_WORK_UNIT")
    recovery_segment = spec.get("kind") == "RECOVERY"
    duration = None if recovery_segment else curve.inverse(velocity / 3.6)
    continuous = base.get("structure") in {"CONTINUOUS", "PROGRESSIVE_CONTINUOUS"}
    if not continuous and not recovery_segment and seconds >= duration:
        raise ValueError("REPETITION_REACHES_CONTINUOUS_MAXIMUM")
    zone = speed_zones.classify(zone_profile, velocity)
    if zone not in {"Z1", "Z2", "Z3", "Z4", "Z5"}:
        raise ValueError("INDIVIDUAL_SPEED_ZONES_REQUIRED")
    rest = _range(spec.get("recovery_after_seconds", 0), "segment_recovery", zero=True)
    return {"kind": "RECOVERY" if recovery_segment else "WORK", "anchor": anchor, "speed_multiplier": multiplier, "speed_multiplier_range": multipliers,
            "target_speed_kmh": velocity, "target_speed_range_kmh": velocities,
            "work_duration_seconds": seconds, "work_duration_range_seconds": seconds_range,
            "work_distance_m": distance, "work_distance_range_m": distance_range,
            "work_unit": work["unit"], "work_range": works, "continuous_capacity_seconds": duration,
            "duration_is_tmax_fallback": fallback_duration,
            "zone": zone, "recovery_after_seconds": sum(rest) / 2,
            "recovery_after_type": spec.get("recovery_after_type", (base.get("recovery") or {}).get("type", "UNSPECIFIED")),
            "recovery_after_instructions": spec.get("recovery_after_instructions"),
            "execution_control": spec.get("execution_control", base.get("execution_control", "SPEED_AND_QUALITY"))}


def _athlete_level(profile):
    """Use the existing explicit level; no universal beginner dose multiplier."""
    level = profile.get("athlete_level") or (profile.get("load_progression") or {}).get("training_level")
    return {"LOW": "BEGINNER", "MEDIUM": "ADVANCED", "HIGH": "HIGHLY_TRAINED"}.get(level, level)


def _selected_prescription(p, override):
    """Choose one coupled standard, without combining independent maxima."""
    standard = deepcopy(p.get("standard_variant") or {})
    variants = deepcopy(p.get("allowed_variants") or [])
    for variant in ([standard] if standard else []) + variants:
        for key, upper in (("repetitions", 100), ("sets", 10)):
            value = variant.get(key, 1)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= upper:
                raise ValueError("INVALID_VARIANT_" + key.upper())
        if variant.get("work"):
            _range(variant["work"].get("range"), "variant_work")
            if variant["work"].get("unit") not in {"seconds", "meters", "capacity_fraction"}:
                raise ValueError("INVALID_VARIANT_WORK_UNIT")
        for key in ("speed_multiplier", "hrmax_fraction"):
            if key in variant:
                _range(variant[key], "variant_" + key)
    if standard:
        if variants and standard not in variants:
            raise ValueError("STANDARD_VARIANT_NOT_ALLOWED")
        for key in ("work", "speed_multiplier"):
            if key in standard:
                p[key] = deepcopy(standard[key])
        if "sets" in standard:
            p["sets"] = {**(p.get("sets") or {}), "count": standard["sets"]}
    source_repeats = p.get("source_repetitions_range") or p.get("repetitions")
    repeats = standard.get("repetitions", source_repeats[0] if source_repeats else None)
    if override.get("repetitions") is not None:
        # Explicit coach changes remain attributable and do not alter originals.
        repeats = override["repetitions"][0]
    if repeats is None and p.get("structure") not in {"CONTINUOUS", "SCALED_SEQUENCE", "PROGRESSIVE_CONTINUOUS", "SEQUENCE"}:
        raise ValueError("STANDARD_DOSE_REQUIRED_NO_SOURCE_COUNT")
    if not isinstance(repeats or 1, int) or isinstance(repeats, bool):
        raise ValueError("INVALID_STANDARD_REPETITIONS")
    p["selected_standard_repetitions"] = repeats or 1
    p["source_standard_variant"] = deepcopy(standard)
    p["selected_standard_variant"] = {**standard,
        "repetitions": repeats or 1, "sets": (p.get("sets") or {}).get("count", 1),
        "work": deepcopy(p.get("work")), "speed_multiplier": deepcopy(override.get("speed_multiplier", p.get("speed_multiplier")))}
    if override.get("work_range") is not None:
        p["selected_standard_variant"]["work"] = {**p["work"], "range": deepcopy(override["work_range"])}
    return p


def resolve(record, profile, speed_view=None, *, phase=None, _prepared=None, _hr_predictor=None):
    phase = phase or "GENERAL_PREPARATION"
    config = profile.get("method_library") or {}
    override = (config.get("overrides") or {}).get(record["id"], {})
    dosing = method_dosing.parameters(record, profile)
    result = {"dosing": dosing, "status": "UNAVAILABLE", "reason": None, "phase": phase,
              "speed_role": "FLAT_EQUIVALENT_REFERENCE_NOT_TERRAIN_PACE",
              "selection_basis": "EXPLICIT_METHOD_RANGES_CURRENT_CURVE", "curve_improvement_assumed": False}
    if config.get("enabled", True) is False or override.get("enabled", True) is False:
        return {**result, "status": "DISABLED", "reason": "DISABLED_BY_PROFILE"}
    if "enabled" not in override and any((config.get("overrides", {}).get(parent) or {}).get("enabled") is False
                                         for parent in record.get("derived_from", [])):
        return {**result, "status": "DISABLED", "reason": "SOURCE_FAMILY_DISABLED_BY_PROFILE"}
    p = deepcopy(record.get("prescription"))
    if not p:
        return {**result, "status": "REFERENCE_ONLY", "reason": record.get("non_executable_reason") or "NO_COMPLETE_SPEED_PRESCRIPTION"}
    actual_sport = profile.get("actual_sport") or profile.get("sport")
    if actual_sport not in record.get("sports", []):
        return {**result, "status": "INCOMPATIBLE", "reason": "SPORT_NOT_SUPPORTED"}
    disciplines = record.get("disciplines") or []
    primary = primary_distance(profile)
    if disciplines and (primary is None or not any(isinstance(d, (float, int)) and abs(primary-d) <= max(1., d*.005) for d in disciplines)):
        return {**result, "status": "INCOMPATIBLE", "reason": "PRIMARY_DISCIPLINE_NOT_SUPPORTED"}
    if phase not in record.get("periods", PHASE_POSITION):
        return {**result, "status": "INCOMPATIBLE", "reason": "PERIOD_NOT_SUPPORTED"}
    levels = record.get("athlete_levels") or []
    level = _athlete_level(profile)
    result.update(athlete_level=level, suitable_athlete_levels=levels)
    if levels and level not in levels and not override.get("enabled"):
        return {**result, "status": "INCOMPATIBLE", "reason": "ATHLETE_LEVEL_REQUIRED" if level in {None, "AUTO"} else "ATHLETE_LEVEL_NOT_SUPPORTED"}
    if speed_view and speed_view.get("sport") != actual_sport:
        return {**result, "reason": "SPEED_SPORT_MISMATCH"}
    progression = method_progression.state(record, profile, phase=phase)
    result["progression"] = progression
    if progression and not progression["eligible"] and not profile.get("_confirming_execution"):
        return {**result, "status": "PROGRESSION_LOCKED", "reason": "CONFIRMED_EXECUTION_REQUIRED"}
    try:
        chosen = p.get("phase_ranges", {}).get(phase, {})
        p.update({k: deepcopy(v) for k, v in chosen.items() if k != "work_range"})
        if "work_range" in chosen:
            p["work"]["range"] = chosen["work_range"]
        for key in ("speed_multiplier", "repetitions"):
            if override.get(key) is not None:
                p[key] = deepcopy(override[key])
        if override.get("work_range") is not None:
            if p.get("pattern"):
                raise ValueError("COMPOUND_WORK_OVERRIDE_REQUIRES_SEGMENT_PRESCRIPTION")
            p["work"]["range"] = deepcopy(override["work_range"])
        p = _selected_prescription(p, override)
        # Explicit work/intensity overrides take precedence over the retained
        # original standard; they remain marked in expert_override below.
        for key in ("speed_multiplier", "repetitions"):
            if override.get(key) is not None:
                p[key] = deepcopy(override[key])
        if override.get("work_range") is not None:
            p["work"]["range"] = deepcopy(override["work_range"])
        if (p.get("anchor") or {}).get("type") == "HRMAX":
            from . import method_hr
            return {**result, **method_hr.resolve(record, profile, speed_view, phase=phase, prescription=p)}
        if _prepared is None:
            _prepared, _hr_predictor = _prepare(speed_view)
        curve, tests, curve_source = _prepared
        short_test = (record.get("requirements") or {}).get("short_test_max_seconds")
        if short_test and not any(0 < t.get("duration_s", math.inf) <= short_test for t in tests):
            return {**result, "reason": "SHORT_SPEED_TEST_REQUIRED", "required_test_max_seconds": short_test}
        zones = (speed_view.get("dosing_model") or {}).get("speed_zones") if curve_source == "BLENDED_DOSING_CURVE" else speed_view.get("speed_zones")
        position = progression["position"] if progression else 0.
        # Calendar selects permitted methods, never successful execution.
        # Fixed prescriptions remain fixed; variable ranges start at the short end.
        pattern = p.get("pattern") or [{}]
        if p.get("structure") == "COMPOUND" and not p.get("pattern"):
            raise ValueError("COMPOUND_PATTERN_REQUIRED")
        segments = [_segment(s, p, profile, speed_view, curve, tests, position, zones) for s in pattern]
        tail = _segment(p["tail"], p, profile, speed_view, curve, tests, position, zones) if p.get("tail") else None
        for segment in segments + ([tail] if tail else []):
            # A fixed source intensity remains fixed. Generic ±2% tolerances
            # are not a published method range.
            segment.update(_hr_reference(segment, _hr_predictor))
            segment.update(method_dosing.target_ranges(None, None, segment["zone"], dosing=dosing,
                hr_range=segment.get("hr_reference_range_bpm"), hr_bounds=getattr(_hr_predictor, "bounds", None)))
        if (profile.get("planning_controls") or {}).get("capacity_policy", "MODEL_WITH_PRIOR") != "MODEL_WITH_PRIOR":
            observed = sorted({t["duration_s"] for t in tests})
            if len(observed) < 2 or any(not observed[0] <= s["continuous_capacity_seconds"] <= observed[-1] for s in segments if s["kind"] == "WORK"):
                raise ValueError("OUTSIDE_OBSERVED_CAPACITY_SUPPORT")
        repetitions = (override.get("repetitions") or p.get("source_repetitions_range")
                       or p.get("repetitions") or [1, 1])
        repetitions = _range(repetitions, "repetitions")
        if any(v != int(v) or v > 100 for v in repetitions):
            raise ValueError("INVALID_REPETITIONS")
        sets = p.get("sets") or {}
        count = sets.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 10:
            raise ValueError("INVALID_SETS")
        recovery = p.get("recovery") or {"seconds": [0, 0], "type": "UNSPECIFIED"}
        rests = _range(recovery.get("seconds", [0, 0]), "recovery", zero=True)
        set_rest = _range(sets.get("recovery_seconds", 0), "set_recovery", zero=True)
        work_segments = [s for s in segments if s["kind"] == "WORK"]
        cycle = sum(s["work_duration_seconds"] for s in work_segments)
        if recovery.get("work_ratio") is not None:
            ratio = _range(recovery["work_ratio"], "recovery_work_ratio", zero=True)
            rests = [cycle * v for v in ratio]
        dominant = max(work_segments, key=lambda s: int(s["zone"][1:]))
        if p.get("race_duration_fraction_max"):
            for segment in work_segments:
                duration = segment["anchor"].get("duration_seconds")
                if duration and segment["work_duration_seconds"] > duration * p["race_duration_fraction_max"]:
                    raise ValueError("WORK_INTERVAL_TOO_LONG_FOR_SELECTED_DISCIPLINE")
        source_prescription = record.get("prescription") or {}
        original_repetitions = (source_prescription.get("source_repetitions_range") or
                                (None if source_prescription.get("repetitions_from_budget") else source_prescription.get("repetitions")))
        published_repetitions = deepcopy(original_repetitions)
        standard_repetitions = int(p["selected_standard_repetitions"])
        if standard_repetitions < repetitions[0] or standard_repetitions > repetitions[1]:
            raise ValueError("STANDARD_REPETITIONS_OUTSIDE_METHOD")
        # Source recipes own the work dose. Tmax is retained as individual
        # feasibility evidence, never used to multiply a prescribed dose.
        allocation = 1.
        standard_seconds = cycle * count * standard_repetitions
        maximum = cycle * count * repetitions[1]
        work_options = None
        structure = p.get("structure", "INTERVALS")
        if structure == "CONTINUOUS":
            minimum, maximum = work_segments[0]["work_duration_range_seconds"]
            standard_seconds = cycle
            if work_segments[0].get("duration_is_tmax_fallback"):
                if dominant["zone"] not in {"Z1", "Z2"}:
                    raise ValueError("TMAX_FALLBACK_ONLY_Z1_Z2")
                minimum, maximum = standard_seconds * .3, standard_seconds
            reduction = p.get("minimum_scale")
            if reduction is not None:
                if not 0 < reduction <= 1:
                    raise ValueError("INVALID_CONTINUOUS_REDUCTION")
                minimum = min(minimum, standard_seconds * reduction)
            repetitions = [1, 1]
        elif structure in {"SCALED_SEQUENCE", "PROGRESSIVE_CONTINUOUS"}:
            minimum = cycle * p.get("minimum_scale", 1.)
            maximum = standard_seconds = cycle
            repetitions = [1, 1]
        elif structure == "SEQUENCE":
            if not tail or count != 1:
                raise ValueError("INVALID_SEQUENCE")
            low, high = _range(p.get("tail_repetitions", [0, 0]), "tail_repetitions", zero=True)
            if any(v != int(v) for v in (low, high)):
                raise ValueError("INVALID_TAIL_REPETITIONS")
            work_options = [cycle + n*tail["work_duration_seconds"] for n in range(int(low), int(high)+1)]
            if not work_options:
                raise ValueError("NO_COMPLETE_REPEAT_WITHIN_METHOD_BUDGET")
            minimum, maximum = work_options[0], work_options[-1]
            standard_seconds = work_options[0]
            repetitions = [1, 1]
        else:
            if p.get("allow_fewer_repetitions") and override.get("repetitions") is None:
                repetitions[0] = min(repetitions[0], p.get("minimum_repetitions", 1))
            if repetitions[1] < repetitions[0]:
                raise ValueError("NO_COMPLETE_REPEAT_WITHIN_METHOD_BUDGET")
            allowed_counts = set(range(int(repetitions[0]), int(repetitions[1])+1))
            variants = p.get("allowed_variants") or []
            dose_override = any(key in override for key in ("repetitions", "work_range", "speed_multiplier"))
            if variants and not dose_override:
                allowed_counts = {int(v["repetitions"]) for v in variants
                    if v.get("sets", count) == count and v.get("work", p.get("work")) == p.get("work")
                    and v.get("speed_multiplier", p.get("speed_multiplier")) == p.get("speed_multiplier")
                    and isinstance(v.get("repetitions"), int) and not isinstance(v.get("repetitions"), bool)}
                if p.get("allow_fewer_repetitions"):
                    allowed_counts.update(range(int(repetitions[0]), standard_repetitions+1))
                if standard_repetitions not in allowed_counts:
                    raise ValueError("NO_COMPLETE_STANDARD_VARIANT")
            if not allowed_counts:
                raise ValueError("NO_COMPLETE_REPEAT_WITHIN_METHOD_BUDGET")
            repetitions = [min(allowed_counts), max(allowed_counts)]
            work_options = sorted(cycle * count * n for n in allowed_counts)
            minimum, maximum = work_options[0], work_options[-1]
        recovery_resolution = None
        if recovery.get("anchor") or recovery.get("speed_multiplier"):
            recovery_resolution = _segment({"anchor": recovery.get("anchor", p.get("anchor")),
                "speed_multiplier": recovery.get("speed_multiplier", [1, 1]),
                "work": {"unit": "seconds", "range": rests}, "kind": "RECOVERY"},
                p, profile, speed_view, curve, tests, .5, zones)
            recovery_resolution.update(_hr_reference(recovery_resolution, _hr_predictor))
        return {**result, **deepcopy(dominant), "status": "READY", "curve_source": curve_source,
                "actual_sport": actual_sport, "source_repetitions": original_repetitions,
                "source_count_policy": "EXPLICIT_COACH_REPEAT_RANGE" if override.get("repetitions") is not None else "SOURCE_STANDARD_WITH_DECLARED_WHOLE_REPEAT_ADAPTATIONS",
                "automatic_selection_enabled": override.get("enabled", record.get("automatic_selection", True)),
                "requires_coach_activation": not override.get("enabled", record.get("automatic_selection", True)),
                "recovery_resolution": recovery_resolution,
                "tail_segment": tail, "work_options_seconds": work_options,
                "curve_model_version": getattr(curve, "model_version", speed_view.get("model_version")),
                "segments": segments, "components": sorted({s["zone"] for s in segments} | ({recovery_resolution["zone"]} if recovery_resolution else set())),
                "repetitions": [int(v) for v in repetitions], "sets": count,
                "recovery_seconds": sum(rests)/2, "recovery_range_seconds": rests,
                "recovery_type": recovery.get("type", "UNSPECIFIED"), "recovery_mode_hint": recovery.get("mode_hint"), "set_recovery_seconds": sum(set_rest)/2, "set_recovery_type": sets.get("recovery_type", recovery.get("type", "UNSPECIFIED")),
                "minimum_work_seconds": minimum, "maximum_work_seconds": maximum,
                "standard_work_seconds": standard_seconds, "published_repetitions": published_repetitions,
                "standard_variant": p["selected_standard_variant"], "allowed_variants": deepcopy(p.get("allowed_variants") or []),
                "source_standard_variant": deepcopy(p.get("source_standard_variant")),
                "dose_basis": ("INDIVIDUAL_TMAX_FALLBACK_65_PERCENT" if work_segments[0].get("duration_is_tmax_fallback") else
                               "EXPLICIT_COACH_OVERRIDE" if any(key in override for key in ("repetitions", "work_range", "speed_multiplier"))
                               else p.get("dose_basis", "EXPLICIT_SOURCE_RECIPE")),
                "reduction_policy": deepcopy(p.get("reduction_policy")),
                "cycle_work_seconds": cycle, "structure": p.get("structure", "INTERVALS"),
                "expert_override": deepcopy(override), "active_test_count": len(tests),
                "method_work_capacity_seconds": standard_seconds, "component_allocation": allocation,
                "budget_policy": "SELECTED_SOURCE_STANDARD_TIMES_ROLE_TIMES_RECOVERY_ONCE"}
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return {**result, "reason": str(exc) or "UNRESOLVED_RECIPE"}


def catalog(profile, speed_view=None, *, phase=None, today=None):
    try:
        prepared, predictor = _prepare(speed_view)
    except (ValueError, KeyError, TypeError):
        prepared, predictor = None, None
    return {"version": VERSION, "methods": [{**m, "resolution": resolve(m, profile, speed_view, phase=phase, _prepared=prepared, _hr_predictor=predictor)} for m in entries()]}


def metadata(record, resolution):
    return {k: deepcopy(record.get(k)) for k in ("id", "title", "source", "original", "adaptation", "guidance", "prescription", "progression", "derived_from", "requirements", "athlete_levels", "provenance")} | {"resolution": deepcopy(resolution)}


def executable_methods(profile, speed_view, *, phase=None):
    result = []
    automatic_intervals = (profile.get("planning_controls") or {}).get("automatic_intervals", True)
    try:
        prepared, predictor = _prepare(speed_view)
    except (ValueError, KeyError, TypeError):
        prepared, predictor = None, None
    for record in entries():
        if not automatic_intervals and (record.get("prescription") or {}).get("structure") not in {"CONTINUOUS", "PROGRESSIVE_CONTINUOUS"}:
            continue
        override = ((profile.get("method_library") or {}).get("overrides") or {}).get(record["id"], {})
        if not override.get("enabled", record.get("automatic_selection", True)):
            continue
        for actual_phase in ([phase] if phase else record.get("periods", [])):
            r = resolve(record, profile, speed_view, phase=actual_phase, _prepared=prepared, _hr_predictor=predictor)
            if r["status"] != "READY":
                continue
            guidance = record.get("guidance") or {}
            preparation = (record.get("prescription") or {}).get("preparation") or {}
            capacity = (r["standard_work_seconds"] if r.get("primary_control") == "HRMAX" else
                        min(s["continuous_capacity_seconds"] for s in r["segments"] if s["kind"] == "WORK"))
            interval = r["structure"] != "CONTINUOUS"
            result.append({"id": "LIB-" + record["id"] + "-" + actual_phase,
                "method_library_id": record["id"], "title": record["title"], "zone": r["zone"],
                "sports": (r["actual_sport"],), "periods": (actual_phase,), "purpose": "BUILDING", "position": .5,
                "structure": "LIBRARY_INTERVALS" if interval else "LIBRARY_CONTINUOUS",
                "min_work_min": r["minimum_work_seconds"]/60, "max_work_min": r["maximum_work_seconds"]/60,
                "minimum_fraction": r["minimum_work_seconds"] / r["standard_work_seconds"],
                "warmup_min": preparation.get("warmup_seconds", 0. if (record.get("prescription") or {}).get("includes_preparation") else 900.) / 60,
                "cooldown_min": preparation.get("cooldown_seconds", 0. if (record.get("prescription") or {}).get("includes_preparation") else 600.) / 60,
                "source_preparation": deepcopy(preparation),
                "preparation_basis": "SOURCE_SPECIFIED" if preparation else "EXPLICIT_ONFLOWS_PREPARATION_DEFAULT",
                "source_id": record["id"], "source_version": VERSION, "implementation_profile": VERSION,
                "instructions": guidance.get("execution") or record.get("adaptation") or record["title"],
                "adaptation": record.get("adaptation"), "method_library": metadata(record, r),
                "dose_evidence": {"readiness_policy": {"required_components": {z: None for z in r["components"]}}},
                "interval_template": {"zone": r["zone"], "work_seconds": r["cycle_work_seconds"],
                    "recovery_seconds": r["recovery_seconds"], "min_repetitions": r["repetitions"][0],
                    "max_repetitions": r["repetitions"][1], "reserve_repetitions": 2,
                    "total_capacity_ratio": r["method_work_capacity_seconds"]/capacity}})
    return result
