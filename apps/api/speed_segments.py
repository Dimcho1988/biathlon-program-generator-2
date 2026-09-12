"""Read-only selection diagnostics and full-resolution speed test integration."""
from datetime import datetime, timedelta, timezone
import math
from zoneinfo import ZoneInfo

from fastapi import HTTPException


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def sample_intervals(shadow):
    independent = shadow.get("speed_test_series")
    rows = sorted((r for r in (independent if independent is not None else shadow.get("timeseries") or [])
                   if finite(r.get("elapsed_s"))), key=lambda r: r["elapsed_s"])
    result = []
    previous_right = 0.
    for i, row in enumerate(rows):
        t = row["elapsed_s"]
        if independent is not None:
            dt = row.get("dt_s", 0)
            if not finite(dt) or dt <= 0:
                continue
            left, right = max(0., t - dt), t
        else:
            right = min(t + 1, rows[i + 1]["elapsed_s"]) if i + 1 < len(rows) else t + 1
            left = t
        speed, grade = row.get("vflat_b65_kmh"), row.get("grade_smoothed_pct")
        reason = ("DOWNHILL" if finite(grade) and grade < -3 else
                  "INVALID" if not finite(speed) or speed <= 0 or not finite(grade) or row.get("exclusion_reason") else None)
        left = max(left, previous_right)
        if right > left:
            result.append((left, right, speed if finite(speed) and speed > 0 else None, reason))
            previous_right = right
    return result


def measure(intervals, start, duration):
    covered = weighted = downhill = invalid = 0.
    for left, right, speed, reason in intervals:
        dt = max(0., min(right, start + duration) - max(left, start))
        if reason == "DOWNHILL":
            downhill += dt
        elif reason:
            invalid += dt
        else:
            covered += dt
            weighted += dt * speed
    eligible = covered / duration >= .98
    return {"start_s": start, "duration_s": duration, "eligible": eligible,
            "coverage_percent": 100 * covered / duration,
            "speed_kmh": weighted / covered if eligible else None,
            "distance_m": weighted / covered * duration / 3.6 if eligible else None,
            "distance_basis": "VFLAT_EQUIVALENT",
            "excluded_seconds": {"downhill": downhill, "invalid": invalid,
                                 "missing_or_paused": max(0., duration - covered - downhill - invalid)}}


def segment_measurement(shadow, start, duration):
    intervals = sample_intervals(shadow)
    if not intervals:
        raise HTTPException(409, "Full Vflat samples are required")
    if start + duration > max(r[1] for r in intervals):
        raise HTTPException(422, "Outside the activity range")
    result = measure(intervals, start, duration)
    if not result["eligible"]:
        raise HTTPException(422, "A continuous segment with at least 98% eligible Vflat coverage is required; refresh older activity analyses first")
    return {k: result[k] for k in ("duration_s", "speed_kmh", "coverage_percent", "distance_m", "distance_basis")}


def preview(repository, alias, activity_ref, start=None, duration=None):
    row = repository.active_activity_view(alias, activity_ref)
    if not row:
        raise HTTPException(404, "Activity is unavailable for this athlete")
    activity, shadow = row["catalog_payload"], row.get("shadow_payload") or {}
    settings = repository.athlete_settings(alias)
    if settings is None:
        raise HTTPException(409, "Athlete settings are required")
    today = datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone)).date()
    day = activity.get("local_date") or activity.get("date")
    intervals = sample_intervals(shadow)
    sample_end = max([r[1] for r in intervals] + [0])
    elapsed = sample_end
    declared = activity.get("elapsed_time_s")
    if finite(declared):
        elapsed = max(elapsed, declared)
    elapsed = math.floor(elapsed)
    status = "READY" if intervals else "ANALYSIS_REQUIRED"
    if not day or not (today - timedelta(days=90)).isoformat() <= day <= today.isoformat():
        status = "OUTSIDE_TEST_WINDOW"
    selection = None
    if start is not None and duration is not None:
        selection = measure(intervals, start, duration)
        outside = start + duration > min(elapsed, sample_end)
        if status != "READY" or outside:
            selection.update(eligible=False, speed_kmh=None, distance_m=None)
        selection["status"] = (status if status != "READY" else "OUTSIDE_ACTIVITY" if outside
                               else "ELIGIBLE" if selection["eligible"] else "INSUFFICIENT_COVERAGE")
    # Bounded display buckets only. Preview and save always integrate the full
    # intervals above, so moving handles never measures the displayed averages.
    width = max(1., elapsed / 360)
    buckets = {}
    for left, right, speed, reason in intervals:
        index = min(359, int(left / width))
        bucket = buckets.setdefault(index, [0., 0., 0.])
        dt = right - left
        if speed is not None:
            bucket[0] += dt * speed
            bucket[1] += dt
        if reason is None:
            bucket[2] += dt
    series = [{"elapsed_s": min(elapsed, (i + .5) * width),
               "speed_kmh": buckets[i][0] / buckets[i][1] if i in buckets and buckets[i][1] else None,
               "eligible_fraction": min(1., buckets[i][2] / width) if i in buckets else 0.}
              for i in range(min(360, math.ceil(elapsed / width)))]
    return {"schema_version": "speed-test-preview-v1", "activity_ref": activity_ref,
            "sport": activity["sport"], "name": activity.get("name") or activity["sport"], "day": day,
            "elapsed_s": elapsed, "status": status, "series": series, "selection": selection,
            "source_run_key": row.get("shadow_run_key"), "uses_legacy_samples": shadow.get("speed_test_series") is None}
