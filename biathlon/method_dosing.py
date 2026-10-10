"""Shared continuous-method role budgets, before Recovery is applied once."""


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
    """Maintenance, supporting and re-entry share half the developing budget."""
    return continuous_building_fraction(profile) / 2
