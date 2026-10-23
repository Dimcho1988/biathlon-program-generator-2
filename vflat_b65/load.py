"""Load-only speed completion; measured Vflat and calibration stay immutable.

The Nordic balance uses identical, timestamp-supported intervals on both sides.
It is an equivalent-distance constraint, not evidence of downhill effort.
"""
import math

from .sports import is_nordic, is_running

VERSION = "speed-load-samples-v1-descent-balance-rest"
REST_SPEED_KMH = 5.0


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def complete_load_speed(rows, *, sport, terrain_factor=1.0):
    """Return annotations without changing the input speed or validity fields.

    Raw speed below 5 km/h is known rest, including stationary samples without
    grade. Its numeric speed is retained in the balance. Only supported moving
    Nordic samples below -3% receive the uniform residual speed. Other quality
    exclusions and gaps cannot receive fabricated speed or rest classifications.
    """
    if not (is_nordic(sport) or is_running(sport)):
        return rows, None
    if not finite(terrain_factor) or terrain_factor <= 0:
        raise ValueError("A positive terrain factor is required")
    nordic = is_nordic(sport)
    output, descents = [], []
    duration = raw_distance = known_distance = excluded = 0.0
    for source in rows:
        row = {**source, "load_speed_kmh": None, "load_status": "UNAVAILABLE",
               "load_source": None, "load_reason": "INVALID_SPEED_SAMPLE"}
        output.append(row)
        dt, raw = row.get("dt_s"), row.get("speed_raw_kmh")
        grade, vflat = row.get("grade_raw_pct"), row.get("vflat_b65_kmh")
        # Adapter intervals are at most one second and cannot cross a block.
        if not finite(dt) or not 0 < dt <= 1:
            row["load_reason"] = "MISSING_INTERVAL"
            continue
        if not finite(raw) or raw < 0:
            excluded += dt
            continue
        if raw < REST_SPEED_KMH:
            speed, origin = raw * terrain_factor, "RAW_LOW_SPEED"
        elif (row.get("exclusion_reason") or not finite(grade)
              or not finite(vflat) or vflat < 0):
            excluded += dt
            continue
        elif nordic and grade < -3:
            descents.append(row)
            duration += dt
            raw_distance += raw * dt
            continue
        else:
            # Vflat already includes the session terrain factor. Never apply it twice.
            speed, origin = vflat, "VFLAT"
        row.update(load_speed_kmh=speed, load_source=origin, load_reason=None,
                   load_status="REST" if raw < REST_SPEED_KMH or speed < REST_SPEED_KMH else "ACTIVE")
        duration += dt
        raw_distance += raw * dt
        known_distance += speed * dt
    descent_seconds = sum(row["dt_s"] for row in descents)
    target_distance = terrain_factor * raw_distance
    residual = target_distance - known_distance
    descent_speed = None
    status = "NOT_NEEDED"
    if descents:
        # Tolerate floating-point cancellation only, not a negative solution.
        if residual < -1e-9 * max(1.0, target_distance, known_distance):
            status = "NEGATIVE_RESIDUAL"
            for row in descents:
                row["load_reason"] = "NEGATIVE_DESCENT_RESIDUAL"
        else:
            descent_speed = max(0.0, residual) / descent_seconds
            status = "BALANCED"
            for row in descents:
                row.update(load_speed_kmh=descent_speed, load_source="DESCENT_BALANCE", load_reason=None,
                           load_status="REST" if descent_speed < REST_SPEED_KMH else "ACTIVE")
    summary = {"version": VERSION, "rest_threshold_kmh": REST_SPEED_KMH,
               "terrain_factor": terrain_factor, "balance_status": status,
               "balance_duration_s": duration, "excluded_interval_s": excluded,
               "raw_mean_kmh": raw_distance / duration if duration else None,
               "target_mean_kmh": target_distance / duration if duration else None,
               "descent_duration_s": descent_seconds, "descent_speed_kmh": descent_speed,
               "rest_duration_s": sum(r["dt_s"] for r in output if r["load_status"] == "REST"),
               "estimated_descent_duration_s": descent_seconds if status == "BALANCED" else 0.0,
               "balance_scope": "SUPPORTED_INTERVALS_INCLUDING_KNOWN_REST",
               "calibration_eligible": False}
    return output, summary
