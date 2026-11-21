"""Recorded raw speed intervals for the initial model's raw-speed coordinate."""
import math

VERSION = "raw-speed-intervals-v1"
ROLE = "RAW_MOVING_SPEED"


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _rows(shadow):
    independent = shadow.get("speed_test_series")
    # Older cycling runs retained raw speed only at original timestamps.
    if isinstance(independent, list) and any("speed_raw_kmh" in r for r in independent):
        return independent, True
    return shadow.get("timeseries") or [], False


def available(shadow):
    rows, _ = _rows(shadow)
    return any("speed_raw_kmh" in r for r in rows)


def _smart_span(row, following):
    if following is None:
        return False
    dt = following["elapsed_s"]-row["elapsed_s"]
    if "hr_interval_classification" in row:
        return (row["hr_interval_classification"] == "smart_recording"
                and row.get("hr_interval_end_s") == following["elapsed_s"] and 1 < dt <= 10)
    if any("recording_stop" in str(flag) for r in (row, following) for flag in r.get("quality_flags", ())):
        return False
    return (1 < dt <= 5 or 5 < dt <= 10 and
            all(finite(r.get("speed_raw_kmh")) and r["speed_raw_kmh"] > 0 for r in (row, following)))


def intervals(shadow, *, rest_threshold_kmh=None):
    """Never substitute terrain-corrected speed, fill gaps, or infer missing rest."""
    source, independent = _rows(shadow)
    rows = sorted((r for r in source if finite(r.get("elapsed_s"))), key=lambda r: r["elapsed_s"])
    result, previous = [], 0.
    for i, row in enumerate(rows):
        t = row["elapsed_s"]
        if independent:
            dt = row.get("dt_s")
            if not finite(dt) or not 0 < dt <= 10:
                continue
            left, right = max(0., t-dt, previous), t
        else:
            following = rows[i+1] if i+1 < len(rows) else None
            left, right = max(0., t, previous), t+1.
            if following is not None:
                right = following["elapsed_s"] if _smart_span(row, following) else min(right, following["elapsed_s"])
        if right <= left:
            continue
        speed = row.get("speed_raw_kmh")
        reason = ("INVALID_RAW_SPEED" if not finite(speed) or speed < 0 else
                  "ESTIMATED_RAW_SPEED" if row.get("is_estimated") or row.get("generated") else None)
        if reason is None and (speed == 0 or finite(rest_threshold_kmh) and speed < rest_threshold_kmh):
            reason = "REST"
        result.append((left, right, speed if finite(speed) and speed >= 0 else None, reason))
        previous = right
    return result
