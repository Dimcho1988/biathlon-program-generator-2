"""Resolve a source-native HRmax protocol without manufacturing a speed or Tmax.

Only an explicitly published HRMAX anchor enters this path. A MAS, race-speed,
power or all-out protocol cannot acquire an HR replacement here. Work is kept
in the source's time units; the selected standard work is a scheduling budget,
not a physiological maximum-duration estimate.
"""
from bisect import bisect_right
from copy import deepcopy
import math

VERSION = "source-native-hrmax-method-v1"


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _range(value, name, *, zero=False):
    if _number(value):
        value = [value, value]
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(_number(v) and (v >= 0 if zero else v > 0) for v in value)
            or value[0] > value[1]):
        raise ValueError("INVALID_" + name)
    return [float(v) for v in value]


def _count(value, name, maximum=100):
    if not _number(value) or int(value) != value or not 1 <= value <= maximum:
        raise ValueError("INVALID_" + name)
    return int(value)


def _settings(profile, view):
    policy = (view or {}).get("sport_hr_policy") or profile.get("_method_hr_settings") or {}
    hrmax = policy.get("sport_hrmax_bpm")
    bounds = policy.get("sport_zone_bounds_bpm")
    if not _number(hrmax) or hrmax <= 0:
        raise ValueError("HRMAX_SETTING_REQUIRED")
    if (not isinstance(bounds, (tuple, list)) or len(bounds) != 6
            or not all(_number(v) and v >= 0 for v in bounds)
            or not all(a < b for a, b in zip(bounds, bounds[1:]))):
        raise ValueError("VALID_HR_ZONE_BOUNDS_REQUIRED")
    return float(hrmax), list(bounds)


def _zone(bounds, value):
    # Exactly one component at a shared boundary. Source ranges remain intact;
    # only the concrete planned central target is assigned to a component.
    if value > bounds[-1] or value < bounds[0]:
        raise ValueError("HR_TARGET_OUTSIDE_CONFIGURED_ZONES")
    return f"Z{min(4, bisect_right(bounds[1:-1], value)) + 1}"


def _segment(spec, base, hrmax, bounds, *, recovery=False):
    anchor = spec.get("anchor", base.get("anchor", {}))
    if anchor.get("type") != "HRMAX":
        raise ValueError("SOURCE_NATIVE_HRMAX_ANCHOR_REQUIRED")
    fractions = _range(spec.get("hrmax_fraction", base.get("hrmax_fraction")), "HRMAX_FRACTION")
    if fractions[1] > 1:
        raise ValueError("HRMAX_FRACTION_EXCEEDS_ONE")
    work = spec.get("work", base.get("work", {}))
    if work.get("unit") != "seconds":
        raise ValueError("HR_PROTOCOL_REQUIRES_SOURCE_TIME_DOSE")
    seconds_range = _range(work.get("range"), "WORK_RANGE")
    # The caller has already selected a coupled standard variant. For an
    # unresolved duration range the published lower duration is conservative;
    # never multiply the highest duration by the highest repetition count.
    seconds = seconds_range[0]
    hr_range = [hrmax * f for f in fractions]
    target = sum(hr_range) / 2
    zone = _zone(bounds, target)
    after = _range(spec.get("recovery_after_seconds", 0), "SEGMENT_RECOVERY", zero=True)
    return {
        "kind": "RECOVERY" if recovery else "WORK", "zone": zone,
        "anchor": {**deepcopy(anchor), "hrmax_bpm": hrmax,
                   "source": "ATHLETE_HRMAX_SETTING", "is_measured": False},
        "hrmax_fraction": sum(fractions) / 2, "hrmax_fraction_range": fractions,
        "target_hr_bpm": target, "hr_reference_range_bpm": hr_range,
        "hr_reference_basis": "PUBLISHED_PERCENT_HRMAX_ON_ATHLETE_SETTING",
        "hr_role": "PRIMARY_SOURCE_PRESCRIPTION", "hr_is_measured": False,
        "execution_control": "HRMAX", "primary_control": "HRMAX",
        "target_speed_kmh": None, "target_speed_range_kmh": None,
        "speed_multiplier": None, "speed_multiplier_range": None,
        "work_duration_seconds": seconds, "work_duration_range_seconds": seconds_range,
        "work_distance_m": None, "work_distance_range_m": None,
        "work_unit": "seconds", "work_range": seconds_range,
        "continuous_capacity_seconds": None,
        "recovery_after_seconds": sum(after) / 2,
        "recovery_after_type": spec.get("recovery_after_type", "UNSPECIFIED"),
        "recovery_after_instructions": spec.get("recovery_after_instructions"),
    }


def resolve(record, profile, speed_view=None, *, phase=None, prescription=None):
    """Return the library resolution shape for an original HRmax protocol.

    Generic eligibility (sport, phase, level, progression and profile overrides)
    belongs to method_library.resolve. This helper validates only the source
    protocol and the actual athlete HR setting and never requires a speed curve.
    """
    result = {
        "version": VERSION, "status": "UNAVAILABLE", "reason": None,
        "phase": phase or "GENERAL_PREPARATION",
        "speed_role": "NOT_PRESCRIBED_SOURCE_HRMAX_PROTOCOL",
        "selection_basis": "PUBLISHED_HRMAX_PROTOCOL",
        "curve_improvement_assumed": False,
        "capacity_reference": "SOURCE_STANDARD_WORK_NOT_PHYSIOLOGICAL_TMAX",
        "curve_source": "SOURCE_HRMAX_PROTOCOL", "curve_model_version": VERSION,
        "active_test_count": 0,
    }
    try:
        p = deepcopy(prescription if prescription is not None else record.get("prescription") or {})
        if (p.get("anchor") or {}).get("type") != "HRMAX":
            raise ValueError("SOURCE_NATIVE_HRMAX_ANCHOR_REQUIRED")
        if p.get("structure", "INTERVALS") not in {"INTERVALS", "CONTINUOUS"}:
            raise ValueError("HR_PROTOCOL_STRUCTURE_NOT_SUPPORTED")
        if p.get("execution_control") in {"ALL_OUT", "SPRINT", "MAXIMAL_EFFORT"}:
            raise ValueError("SPRINT_EFFORT_HAS_NO_HR_SUBSTITUTE")
        if p.get("pattern") or p.get("tail"):
            raise ValueError("HR_COMPOUND_PROTOCOL_REQUIRES_EXPLICIT_IMPLEMENTATION")
        hrmax, bounds = _settings(profile, speed_view)
        override = ((profile.get("method_library") or {}).get("overrides") or {}).get(record["id"], {})
        standard = deepcopy(p.get("selected_standard_variant") or p.get("standard_variant") or {})
        if standard.get("work"):
            p["work"] = deepcopy(standard["work"])
        if standard.get("hrmax_fraction"):
            p["hrmax_fraction"] = deepcopy(standard["hrmax_fraction"])
        source_reps = _range(p.get("source_repetitions_range") or p.get("repetitions", [1, 1]), "REPETITIONS")
        source_reps = [_count(n, "REPETITIONS") for n in source_reps]
        execution_reps = _range(override.get("repetitions") or p.get("repetitions") or source_reps, "REPETITIONS")
        execution_reps = [_count(n, "REPETITIONS") for n in execution_reps]
        reps = _count(p.get("selected_standard_repetitions", standard.get("repetitions", source_reps[0])), "STANDARD_REPETITIONS")
        if override.get("repetitions") is not None:
            reps = execution_reps[0]
        sets_spec = p.get("sets") or {}
        count = _count(standard.get("sets", sets_spec.get("count", 1)), "SETS", 10)
        if not execution_reps[0] <= reps <= execution_reps[1]:
            raise ValueError("STANDARD_REPETITIONS_OUTSIDE_ALLOWED_RANGE")
        segment = _segment({}, p, hrmax, bounds)
        cycle = segment["work_duration_seconds"]
        standard_work = cycle * reps * count
        if p.get("structure") == "CONTINUOUS" and (reps != 1 or count != 1):
            raise ValueError("CONTINUOUS_PROTOCOL_IS_ONE_WORK_BLOCK")
        minimum_reps = execution_reps[0]
        if p.get("allow_fewer_repetitions") and override.get("repetitions") is None:
            minimum_reps = _count(p.get("minimum_repetitions", 1), "MINIMUM_REPETITIONS")
        # Alternatives are coupled. Only variants using this selected interval,
        # intensity and set count can be represented by this executable card.
        variants = p.get("allowed_variants") or []
        allowed_counts = set()
        for variant in variants:
            same_work = variant.get("work", p["work"]) == p["work"]
            same_sets = variant.get("sets", count) == count
            same_intensity = variant.get("hrmax_fraction", p["hrmax_fraction"]) == p["hrmax_fraction"]
            if same_work and same_sets and same_intensity:
                allowed_counts.add(_count(variant.get("repetitions", reps), "VARIANT_REPETITIONS"))
        if variants and reps not in allowed_counts and override.get("repetitions") is None:
            raise ValueError("STANDARD_VARIANT_NOT_ALLOWED")
        if override.get("repetitions") is not None:
            allowed_counts = set(range(execution_reps[0], execution_reps[1] + 1))
        elif not variants:
            allowed_counts.update(range(execution_reps[0], execution_reps[1] + 1))
        if p.get("allow_fewer_repetitions"):
            allowed_counts.update(range(minimum_reps, reps + 1))
        options = sorted(cycle * count * n for n in allowed_counts)
        if not options:
            raise ValueError("NO_COMPLETE_SOURCE_VARIANT")
        recovery = p.get("recovery") or {}
        rests = _range(recovery.get("seconds", 0), "RECOVERY", zero=True)
        if recovery.get("work_ratio") is not None:
            ratio = _range(recovery["work_ratio"], "RECOVERY_WORK_RATIO", zero=True)
            rests = [cycle * n for n in ratio]
        set_rests = _range(sets_spec.get("recovery_seconds", 0), "SET_RECOVERY", zero=True)
        recovery_resolution = None
        if recovery.get("hrmax_fraction") is not None and rests[0] > 0:
            recovery_resolution = _segment({
                "anchor": {"type": "HRMAX"}, "hrmax_fraction": recovery["hrmax_fraction"],
                "work": {"unit": "seconds", "range": rests}}, p, hrmax, bounds, recovery=True)
        components = {segment["zone"]}
        if recovery_resolution:
            components.add(recovery_resolution["zone"])
        automatic = override.get("enabled", record.get("automatic_selection", True))
        continuous = p.get("structure") == "CONTINUOUS"
        return {
            **result, **segment, "status": "READY",
            "actual_sport": profile.get("actual_sport") or profile.get("sport"),
            "source_repetitions": source_reps,
            "source_count_policy": "EXPLICIT_COACH_REPEAT_RANGE" if override.get("repetitions") is not None else
                "PUBLISHED_STANDARD_WITH_EXPLICIT_WHOLE_REPETITION_ADAPTATION" if p.get("allow_fewer_repetitions") else "PUBLISHED_STANDARD",
            "automatic_selection_enabled": automatic, "requires_coach_activation": not automatic,
            "segments": [segment], "components": sorted(components),
            "repetitions": [min(allowed_counts), max(allowed_counts)], "sets": count,
            "standard_variant": {**standard, "repetitions": reps, "sets": count, "work": deepcopy(p["work"])},
            "source_standard_variant": deepcopy(p.get("standard_variant")),
            "standard_work_seconds": standard_work,
            "minimum_work_seconds": segment["work_duration_range_seconds"][0] if continuous else options[0],
            "maximum_work_seconds": segment["work_duration_range_seconds"][1] if continuous else options[-1],
            "work_options_seconds": None if continuous else options, "cycle_work_seconds": cycle,
            "structure": p.get("structure", "INTERVALS"),
            "recovery_resolution": recovery_resolution, "tail_segment": None,
            "recovery_seconds": sum(rests) / 2, "recovery_range_seconds": rests,
            "recovery_type": recovery.get("type", "UNSPECIFIED"),
            "recovery_mode_hint": recovery.get("mode_hint"),
            "set_recovery_seconds": sum(set_rests) / 2,
            "set_recovery_type": sets_spec.get("recovery_type", recovery.get("type", "UNSPECIFIED")),
            "expert_override": deepcopy(override),
            "method_work_capacity_seconds": standard_work, "component_allocation": 1.,
            "dose_basis": "PUBLISHED_PROTOCOL",
            "budget_policy": "SOURCE_STANDARD_WORK_ROLE_AND_RECOVERY_ONCE",
            "hr_setting_source": "SPORT_HRMAX_SETTING",
            "hr_setting_is_laboratory_measurement": False,
        }
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return {**result, "reason": str(exc) or "UNRESOLVED_HR_PROTOCOL"}
