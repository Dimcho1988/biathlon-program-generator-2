"""Shared method role budgets and role-specific Recovery adjustment."""
import math
from copy import deepcopy

VERSION = "source-standard-and-native-method-tmax-v5-bounded-full-dose"
STRUCTURE_VERSION = "authored-method-role-floor-v2-bounded-full-dose"
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
    if method.get("personal_variant"):
        # A private method may select structure, never invent dose coefficients.
        chosen = overrides.get(parent, {}) if not prescription else {}
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
    """Bound the authored method by capacity without rewriting its recipe."""
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
    method.setdefault("source_work_minutes", [method["min_work_min"], method["max_work_min"]])
    interval = method.get("interval_template") or method.get("interval_profile")
    if interval and method["structure"] in {"MODEL_INTERVALS", "METABOLIC_INTERVALS"}:
        method.setdefault("authored_interval_bounds", {
            "min_repetitions": interval["min_repetitions"], "max_repetitions": interval["max_repetitions"],
            "work_seconds": interval["work_seconds"],
            "role_reduction_allowed": not bool(bounds.get("min_repetitions") or bounds.get("min_work_min")
                or method["structure"] == "METABOLIC_INTERVALS" or method.get("personal_variant"))})
    key = (method["structure"], evidence["capacity_minutes"], ratio, tuple(sorted(bounds.items())))
    if method.get("_resolved_dose_key") == key:
        return
    method["_resolved_dose_key"] = key
    for key in ("interval_template", "interval_profile"):
        if method.get(key) and method["structure"] != "THRESHOLD_HIGH":
            p = {**method[key], "total_capacity_ratio": ratio}
            if method["structure"] in {"MODEL_INTERVALS", "METABOLIC_INTERVALS"}:
                authored = method["authored_interval_bounds"]
                maximum = max(0, math.floor((capacity*60+1e-9)/p["work_seconds"]))
                p.update(min_repetitions=bounds.get("min_repetitions", authored["min_repetitions"]),
                         max_repetitions=min(authored["max_repetitions"], bounds.get("max_repetitions", maximum), maximum))
            method[key] = p
            evidence["effort_profile"] = {**evidence.get("effort_profile", {}), **p}
    if method.get("purpose") != "RECOVERY":
        maximum = capacity * (2 if method.get("double_threshold") else 1)
        if method.get("authored_interval_bounds"):
            resolved_interval = method.get("interval_template") or method.get("interval_profile")
            maximum = min(maximum, method["source_work_minutes"][1],
                          resolved_interval["max_repetitions"]*resolved_interval["work_seconds"]/60)
        method["max_work_min"] = min(bounds.get("max_work_min", maximum), maximum)
        interval = method.get("interval_template") or method.get("interval_profile")
        if interval and method["structure"] != "THRESHOLD_HIGH":
            method["min_work_min"] = interval["min_repetitions"] * interval["work_seconds"] / 60
        else:
            method["min_work_min"] = bounds.get("min_work_min", min(method["source_work_minutes"][0], .25*evidence["capacity_minutes"]))


def apply_structure_floor(method, evidence):
    """Reduce whole authored units by the already resolved role/Recovery once.

    Weekly allocation can choose another complete variant, but cannot turn a
    retained method into isolated repetitions. Explicit execution minima and
    explicit coach profiles remain hard. These are coaching recipe contracts,
    not a new empirical physiological threshold.
    """
    role = evidence.get("resolved_role", method.get("purpose"))
    role_factor = .6 if role in {"MAINTENANCE", "SUPPORTING", "RECOVERY", "ACTIVATION"} else 1.
    factor = role_factor * evidence.get("readiness_dose_factor", 1.)
    authored = method.get("authored_interval_bounds")
    library = method.get("method_library")
    evidence["method_structure_policy"] = STRUCTURE_VERSION
    evidence["method_structure_contract_required"] = bool(authored or library and
        library["resolution"].get("structure") in {"INTERVALS", "COMPOUND"})
    if library:
        original = library["resolution"]
        if original.get("structure") not in {"INTERVALS", "COMPOUND"}:
            return
        r = deepcopy(original)
        source_min = r.get("source_minimum_repetitions", r["repetitions"][0])
        unit = r["cycle_work_seconds"]*r["sets"]
        allowed = r.get("role_reduction_allowed", False)
        minimum = max(1, math.floor(source_min*factor+1e-9)) if allowed else source_min
        options = list(r.get("source_work_options_seconds") or r.get("work_options_seconds") or
                       [unit*n for n in range(source_min, r["repetitions"][1]+1)])
        if allowed:
            options.extend(unit*n for n in range(minimum, source_min))
        options = sorted({value for value in options if value+1e-6 >= minimum*unit})
        if not options:
            return
        r.update(minimum_work_seconds=options[0], work_options_seconds=options,
                 repetitions=[round(options[0]/unit), r["repetitions"][1]])
        resolved = {**library, "resolution": r}
        method["method_library"] = resolved
        evidence["method_library"] = resolved
        method["min_work_min"] = options[0]/60
        contract = {"source_minimum_repetitions": source_min, "minimum_repetitions": minimum,
                    "work_unit_seconds": unit, "scope": "ALL_METHOD_WORK", "zone": method["zone"],
                    "minimum_work_minutes": options[0]/60, "maximum_work_minutes": r["maximum_work_seconds"]/60}
    elif authored:
        key = "interval_template" if method.get("interval_template") else "interval_profile"
        interval = {**method[key]}
        minimum = (max(1, math.floor(authored["min_repetitions"]*factor+1e-9))
                   if authored["role_reduction_allowed"] else authored["min_repetitions"])
        limits = method.get("execution_limits") or {}
        minimum = max(minimum, limits.get("min_repetitions", 0),
                      math.ceil(limits.get("min_work_min", 0.)*60/interval["work_seconds"]-1e-9))
        interval["min_repetitions"] = minimum
        method[key] = interval
        evidence["effort_profile"] = {**evidence.get("effort_profile", {}), **interval}
        method["min_work_min"] = minimum*interval["work_seconds"]/60
        contract = {"source_minimum_repetitions": authored["min_repetitions"], "minimum_repetitions": minimum,
                    "work_unit_seconds": interval["work_seconds"], "scope": "PRIMARY_COMPONENT", "zone": method["zone"],
                    "minimum_work_minutes": method["min_work_min"], "maximum_work_minutes": method["max_work_min"]}
    else:
        return
    evidence["method_structure_contract"] = {"version": STRUCTURE_VERSION, "role": role,
        "role_factor": role_factor, "readiness_factor": evidence.get("readiness_dose_factor", 1.),
        "role_reduction_allowed": allowed if library else authored["role_reduction_allowed"], **contract}


def structure_fits(blocks, evidence, *, tolerance=.005):
    """Validate every complete parent, including saved and combined sessions."""
    if evidence.get("combined_capacities"):
        return all(structure_fits([b for b in blocks if b.get("combination_part") == i], cap, tolerance=tolerance)
                   for i, cap in enumerate(evidence["combined_capacities"], 1))
    contract = evidence.get("method_structure_contract")
    if contract:
        work = sum(b["duration_min"] for b in blocks if b.get("kind") == "WORK"
                   and (contract["scope"] == "ALL_METHOD_WORK" or b.get("zone") == contract["zone"]))
        if not contract["minimum_work_minutes"]-tolerance <= work <= contract["maximum_work_minutes"]+tolerance:
            return False
        unit = contract["work_unit_seconds"]/60
        if abs(work-round(work/unit)*unit) > tolerance:
            return False
    secondary = evidence.get("secondary_capacity") or {}
    return not secondary.get("method_structure_contract") or structure_fits(blocks, secondary, tolerance=tolerance)


def current_structure_known(evidence):
    """Old interval locks need a current contract, never their old zero floor."""
    if evidence.get("combined_capacities"):
        return all(current_structure_known(cap) for cap in evidence["combined_capacities"])
    resolution = (evidence.get("method_library") or {}).get("resolution") or {}
    needs_contract = (evidence.get("method_structure_contract_required", False)
                      if evidence.get("method_structure_policy") == STRUCTURE_VERSION else
                      bool(evidence.get("effort_profile")) or resolution.get("structure") in {"INTERVALS", "COMPOUND"})
    if needs_contract and (evidence.get("method_structure_contract") or {}).get("version") != STRUCTURE_VERSION:
        return False
    secondary = evidence.get("secondary_capacity") or {}
    return not secondary or current_structure_known(secondary)


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
            if band and band["low_kmh"] <= speed <= (band.get("high_kmh") or float("inf")):
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
