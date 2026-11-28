"""Scientific selection rules shared by refresh and read-time reconstruction."""
from biathlon import initial_speed_profile, speed_anchor_policy, speed_equivalence, unified_load, zone_profile


def identity():
    # Import lazily: model_service also exposes the projections that consume us.
    from .model_service import HISTORICAL_SPEED_POLICY
    return {
        "unified_load": unified_load.VERSION,
        "speed_equivalence": speed_equivalence.VERSION,
        "speed_anchor_policy": speed_anchor_policy.VERSION,
        "historical_speed_policy": HISTORICAL_SPEED_POLICY,
        "zone_profile": zone_profile.VERSION,
        "initial_zone_policy": initial_speed_profile.ZONE_POLICY_VERSION,
    }
