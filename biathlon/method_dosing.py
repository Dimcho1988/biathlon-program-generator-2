"""Shared method role budgets and role-specific Recovery adjustment."""
import math

VERSION = "source-standard-and-native-method-tmax-v3-execution-bounds"
DEFAULT_SPEED_TOLERANCE = .02
DEFAULT_HR_TOLERANCE = 3.
DOSE_CONTROLS = {"total_capacity_ratio", "speed_tolerance_fraction", "hr_tolerance_bpm"}


def parameters(method, profile=None):
    """Editable coach starting values, never inferred from source repeat counts."""
    prescription = method.get("prescription") or {}
    interval = method.get("interval_template") or method.get("interval_profile") or {}
    structure = prescription.get("structure", method.get("structure", "INTERVALS" if prescription else "CONTINUOUS"))
    work = interval.get("work_seconds") or (prescription.get("work") or {}).get("range", [180])[0]
    intervals = {"MODEL_INTERVALS", "METABOLIC_INTERVALS", "INTERVALS", "COMPOUND", "SEQUENCE", "SCALED_SEQUENCE"}
    default = (1. if structure not in intervals else 2.5 if structure == "COMPOUND" or work <= 120 else 2.)
    overrides = ((profile or {}).get("method_library") or {}).get("overrides") or {}
    parent = method.get("variation_parent_id")
    chosen = {**overrides.get(parent, {}), **overrides.get(method.get("method_library_id", method.get("id")), {})}
    return {"version": VERSION, "basis": "SOURCE_STANDARD_NO_CAPACITY_MULTIPLIER" if prescription else "EDITABLE_COACH_STARTING_VALUES_NOT_VALIDATED_MAXIMUM",
            "applies_to_standard_dose": not bool(prescription),
            "total_capacity_ratio": chosen.get("total_capacity_ratio", interval.get("total_capacity_ratio", prescription.get("total_capacity_ratio", default))),
            "speed_tolerance_fraction": chosen.get("speed_tolerance_fraction", DEFAULT_SPEED_TOLERANCE),
            "hr_tolerance_bpm": chosen.get("hr_tolerance_bpm", DEFAULT_HR_TOLERANCE)}


def recipe_capacity(segments, coefficient):
    """Independent work capacity; mixed zones each receive an equal own share.

    Sum work/Tmax inside each zone, then respect the most limiting component.
    The recipe supplies proportions only. Original repetition counts are absent.
    """
    work = [s for s in segments if s["kind"] == "WORK"]
    usage = {}
    for s in work:
        usage[s["zone"]] = usage.get(s["zone"], 0.) + s["work_duration_seconds"] / s["continuous_capacity_seconds"]
    allocation = 1 / len(usage)
    scale = min(coefficient * allocation / amount for amount in usage.values())
    return sum(s["work_duration_seconds"] for s in work) * scale, allocation


def prepare_native_budget(method, evidence, profile):
    """Resolve coach controls and remove source-example upper counts once."""
    if method["zone"] == "STR" or method.get("method_library") or method.get("combined_methods"):
        return
    dosing = parameters(method, profile)
    method["dosing"] = dosing
    ratio = dosing["total_capacity_ratio"]
    if method["structure"] == "CONTINUOUS" and method["zone"] in {"Z1", "Z2"}:
        ratio = 1.
        dosing.update(total_capacity_ratio=1., basis="UNDOSED_CONTINUOUS_Z1_Z2_TMAX_FALLBACK")
    # The high component in a mixed threshold method has a separate budget.
    if method["structure"] == "THRESHOLD_HIGH":
        ratio = 1.
    evidence.update(method_work_capacity_ratio=ratio, dosing=dosing)
    if "capacity_minutes" not in evidence or "max_work_min" not in method:
        return
    capacity = evidence["capacity_minutes"] * ratio
    bounds = method.get("execution_limits", {})
    key = (method["structure"], evidence["capacity_minutes"], ratio, tuple(sorted(bounds.items())))
    if method.get("_resolved_dose_key") == key:
        return
    method["_resolved_dose_key"] = key
    for key in ("interval_template", "interval_profile"):
        if method.get(key) and method["structure"] != "THRESHOLD_HIGH":
            p = {**method[key], "total_capacity_ratio": ratio}
            if method["structure"] in {"MODEL_INTERVALS", "METABOLIC_INTERVALS"}:
                maximum = max(1, math.floor(capacity*60/p["work_seconds"]))
                # An introductory adaptation is an execution contract, not
                # the source example whose counts native dosing replaces.
                p.update(min_repetitions=bounds.get("min_repetitions", 1),
                         max_repetitions=min(bounds.get("max_repetitions", maximum), maximum))
            method[key] = p
            evidence["effort_profile"] = {**evidence.get("effort_profile", {}), **p}
    if method.get("purpose") != "RECOVERY":
        method.setdefault("source_work_minutes", [method["min_work_min"], method["max_work_min"]])
        maximum = capacity * (2 if method.get("double_threshold") else 1)
        method["max_work_min"] = min(bounds.get("max_work_min", maximum), maximum)
        interval = method.get("interval_template") or method.get("interval_profile")
        if interval and method["structure"] != "THRESHOLD_HIGH":
            method["min_work_min"] = interval["min_repetitions"] * interval["work_seconds"] / 60
        else:
            method["min_work_min"] = bounds.get("min_work_min", min(method["source_work_minutes"][0], .25*evidence["capacity_minutes"]))


def target_ranges(speed, hr, zone, *, dosing=None, speed_range=None, hr_range=None,
                  speed_zones=None, hr_bounds=None):
    """Execution ranges only; canonical load keeps its original central target."""
    dosing = dosing or {}
    result = {}
    if speed is not None:
        explicit = speed_range and speed_range[1] > speed_range[0]
        width = dosing.get("speed_tolerance_fraction", DEFAULT_SPEED_TOLERANCE)
        low, high = speed_range if explicit else (speed*(1-width), speed*(1+width))
        # Preserve already agreed explicit method ranges. Clip new tolerances.
        if not explicit:
            band = next((b for b in (speed_zones or {}).get("zones", []) if b["zone"] == zone), None)
            if band:
                low = max(low, band["low_kmh"])
                high = min(high, band.get("high_kmh") or high)
        result["target_speed_range_kmh"] = [low, high]
    if hr is not None or hr_range:
        width = dosing.get("hr_tolerance_bpm", DEFAULT_HR_TOLERANCE)
        low, high = hr_range if hr_range and hr_range[1] > hr_range[0] else ((hr if hr is not None else hr_range[0])-width, (hr if hr is not None else hr_range[1])+width)
        if hr_bounds and zone in {"Z1", "Z2", "Z3", "Z4", "Z5"}:
            index = int(zone[1:])-1
            low, high = max(low, hr_bounds[index]), min(high, hr_bounds[index+1])
        if low <= high:
            result["hr_reference_range_bpm"] = [math.ceil(low), math.floor(high)]
    return result


def continuous_building_fraction(profile):
    """Resolve the current developing dose, including saved legacy choices.

    Invalid values are preserved so the API schema can reject them. The former
    50% default becomes 65%; formerly valid choices are bounded to 60–70%.
    """
    value = profile.get("building_fraction", .65)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and .5 <= value <= .8:
        return .65 if value == .5 else min(.7, max(.6, value))
    return value


def continuous_maintenance_fraction(profile):
    """Approved 2026-10-15: 60% of the method budget, no Recovery multiplier."""
    return continuous_building_fraction(profile) * .6


def recovery_factor(policy, role):
    """Eligibility is separate from the role selected by the training goal."""
    if not policy.get("allowed", True):
        return 0.
    observed = policy.get("observed_dose_factor", policy.get("dose_factor", 1.))
    if role in {"MAINTENANCE", "SUPPORTING", "ACTIVATION"}:
        return 1.
    # Light recovery sessions retain their existing proportional reduction.
    return observed if role == "RECOVERY" else .5 + .5 * observed


def threshold_structure(method):
    """One classification for library, legacy and reservation paths."""
    if method.get("double_threshold"):
        return "DOUBLE"
    structure = ((method.get("method_library") or {}).get("resolution") or {}).get("structure", method.get("structure"))
    if (structure in {"CONTINUOUS", "LIBRARY_CONTINUOUS", "PROGRESSIVE_CONTINUOUS", "AEROBIC_SUPPORT"}
            or structure == "MIXED_AEROBIC" and method.get("mixed_variant") == "STEADY"):
        return "CONTINUOUS"
    return "INTERVALS"


def threshold_allowed(method, preference):
    """Check the actual threshold parts, regardless of the wrapper's zone.

    A combined method cannot turn a continuous Z3 parent into intervals.
    Explicit double-threshold days retain their separate two-session policy.
    """
    if preference == "AUTO":
        return True
    if method.get("combined_methods"):
        return all(threshold_allowed(part, preference) for part in method["combined_methods"])
    resolution = (method.get("method_library") or {}).get("resolution") or {}
    threshold = method.get("zone") == "Z3" or any(
        part.get("zone") == "Z3" and part.get("kind") == "WORK"
        for part in resolution.get("segments", []))
    return not threshold or threshold_structure(method) in {"DOUBLE", preference}
