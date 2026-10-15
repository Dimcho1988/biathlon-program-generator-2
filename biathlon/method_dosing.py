"""Shared method role budgets and role-specific Recovery adjustment."""


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
    if role in {"MAINTENANCE", "SUPPORTING"}:
        return 1.
    # Light recovery sessions retain their existing proportional reduction.
    return observed if role == "RECOVERY" else .5 + .5 * observed


def threshold_structure(method):
    """One classification for library, legacy and reservation paths."""
    if method.get("double_threshold"):
        return "DOUBLE"
    structure = ((method.get("method_library") or {}).get("resolution") or {}).get("structure", method.get("structure"))
    if structure in {"CONTINUOUS", "LIBRARY_CONTINUOUS", "PROGRESSIVE_CONTINUOUS"}:
        return "CONTINUOUS"
    return "INTERVALS"


def threshold_allowed(method, preference):
    return (method.get("zone") != "Z3" or preference == "AUTO"
            or threshold_structure(method) in {"DOUBLE", preference})
