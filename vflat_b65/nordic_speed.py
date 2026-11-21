"""Recover Nordic ski interval speed from the watch's distance counter."""
from __future__ import annotations

import numpy as np

# Reject a different-scale or incomplete counter before combining channels.
TOTAL_RELATIVE_TOLERANCE = 0.02
TOTAL_ABSOLUTE_TOLERANCE_M = 10.0
MAX_INTERVAL_MPS = 30.0
MOVING_MPS = 2.0
MIN_STALLED_SECONDS = 5.0
MAX_STALLED_SHARE = 0.10


def prepare_speed(offsets, distance, provider_speed, blocks, total_m):
    """Use backward interval speeds only inside supported recording blocks."""
    speed = provider_speed.copy()
    finite_distance = distance[np.isfinite(distance)]
    span = float(finite_distance[-1] - finite_distance[0]) if len(finite_distance) > 1 else 0.0
    verified = bool(total_m is not None and total_m > 0 and span > 0
                    and abs(span - total_m) <= max(TOTAL_ABSOLUTE_TOLERANCE_M, TOTAL_RELATIVE_TOLERANCE * total_m))
    distance_s = provider_s = missing_s = 0.0
    for left, right in blocks:
        interval = slice(left + 1, right)
        dt = np.diff(offsets[left:right])
        supported = (dt > 0) & (dt <= 1)
        used = np.zeros(len(dt), dtype=bool)
        if verified and len(dt):
            increment = np.diff(distance[left:right])
            rates = np.divide(increment, dt, out=np.full_like(dt, np.nan), where=dt > 0)
            valid = np.isfinite(rates) & supported
            # A reset, implausible jump or coarse/stuck counter can give false
            # instantaneous speeds even when its session endpoint looks correct.
            moving = valid & (provider_speed[interval] > MOVING_MPS)
            stalled_s = dt[moving & (increment == 0)].sum()
            stale = stalled_s >= MIN_STALLED_SECONDS and stalled_s >= MAX_STALLED_SHARE * dt[moving].sum()
            if not stale and not np.any(valid & ((rates < 0) | (rates > MAX_INTERVAL_MPS))):
                speed[interval] = np.where(valid, rates, speed[interval])
                used = valid
        fallback = np.isfinite(provider_speed[interval])
        distance_s += dt[supported & used].sum()
        provider_s += dt[supported & ~used & fallback].sum()
        missing_s += dt[supported & ~used & ~fallback].sum()
    return speed, {"distance_total_verified": verified, "distance_seconds": float(distance_s),
                   "provider_seconds": float(provider_s), "missing_seconds": float(missing_s)}
