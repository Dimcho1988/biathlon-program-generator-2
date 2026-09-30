"""Planning-only Q estimates from independent Vflat and measured paired TI.

Only timestamp-identified missing HR is estimated. The measured HR ledger and
trainability observations are never changed. Gaps in speed, mapping, versions,
or masks remain available to the separate recorded-duration expert fallback.
"""
from bisect import bisect_right
from datetime import date, timedelta
import math

from biathlon.equivalence import EQUIVALENCE_VERSION, equivalence_slope
from biathlon.physiology import linear_equivalence_coefficient
from vflat_b65.sports import speed_model_versions
from .activity_shadow_pipeline import activity_shadow_configuration_fingerprint
from .oauth_store import PersistentStoreFailure
from .speed_segments import sample_intervals
from .trainability import LAG_SECONDS, MAX_GAP_SECONDS, MIN_SECONDS_BY_BAND

VERSION = "speed-zone-planning-history-v2"
ZONES = tuple(f"Z{i}" for i in range(1, 6))
PROVENANCE = {"speed": "MEASURED_VFLAT", "hr": "PAIRED_INDEX_ESTIMATE",
              "load": "ESTIMATED_NOT_MEASURED_HR"}


def _number(value):
    return (float(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) else None)


def _missing_hr(row):
    # A suspicious nonzero signal is not evidence of an absent sensor.
    return "hr_raw_bpm" in row and (row["hr_raw_bpm"] is None or _number(row["hr_raw_bpm"]) == 0)


def _mapping(view, settings, today, sport):
    """All six edges need paired support; expert midpoint speeds never qualify."""
    if not isinstance(view, dict) or view.get("sport") != sport or settings is None:
        return None, "NO_SPORT_SPECIFIC_INDEX"
    hrmax = _number(getattr(settings, "hrmax_bpm", None))
    bounds = tuple(getattr(settings, "zone_bounds_bpm", ()))
    if not hrmax or hrmax <= 0 or len(bounds) != 6 or any(_number(v) is None for v in bounds):
        return None, "HR_PROFILE_REQUIRED"
    if not all(a < b for a, b in zip(bounds, bounds[1:])) or bounds[-1] > hrmax:
        return None, "INVALID_HR_BOUNDS"
    window = view.get("index_window") or {}
    try:
        age = (today-date.fromisoformat(window.get("last_activity_date", ""))).days
    except (ValueError, TypeError):
        return None, "NO_RECENT_PAIRED_INDEX"
    if not 0 <= age < 40 or window.get("days") != 40:
        return None, "NO_RECENT_PAIRED_INDEX"
    indices = []
    for zone in ZONES:
        band = (view.get("index_summary") or {}).get(zone) or {}
        value, count, seconds = (_number(band.get(k)) for k in ("index", "count", "seconds"))
        if (value is None or value <= 0 or count is None or count < 1 or seconds is None
                or seconds < MIN_SECONDS_BY_BAND[zone] or band.get("valid") is False
                or band.get("is_estimated") or band.get("generated")
                or band.get("source") not in (None, "PAIRED_RAW_HR_VFLAT", "PAIRED_HR_VFLAT")):
            return None, "INCOMPLETE_PAIRED_ZONE_BOUNDARIES"
        indices.append(value)
    speeds = (100*bounds[0]/hrmax/indices[0],) + tuple(100*bounds[i+1]/hrmax/v for i, v in enumerate(indices))
    if not all(a < b for a, b in zip(speeds, speeds[1:])):
        return None, "NONMONOTONE_PAIRED_ZONE_BOUNDARIES"
    return {"bounds_bpm": bounds, "bounds_kmh": speeds,
            "last_activity_date": window["last_activity_date"],
            "vflat_versions": speed_model_versions(sport),
            "comparison_key": activity_shadow_configuration_fingerprint(bounds, settings.hrmax_bpm, sport=sport)}, None


def _missing_spans(shadow, coverage, elapsed):
    rows = sorted((r for r in (shadow.get("timeseries") or []) if _number(r.get("elapsed_s")) is not None),
                  key=lambda r: r["elapsed_s"])
    if coverage == 0:
        if any(not _missing_hr(row) for row in rows):
            return [], "HR_MASK_COVERAGE_CONFLICT"
        return [(0., elapsed)], None
    if not rows:
        return [], "PARTIAL_HR_MASK_UNAVAILABLE"
    spans = []
    for left, right in zip(rows, rows[1:]):
        a, b = max(0., left["elapsed_s"]), min(elapsed, right["elapsed_s"])
        if not 0 < b-a <= MAX_GAP_SECONDS or not (_missing_hr(left) and _missing_hr(right)):
            continue
        if spans and abs(spans[-1][1]-a) <= 1e-9:
            spans[-1] = (spans[-1][0], b)
        else:
            spans.append((a, b))
    return spans, None


def _estimate(shadow, mapping, *, missing_minutes, coverage, elapsed):
    if not isinstance(shadow.get("speed_test_series"), list):
        return None, "INDEPENDENT_SPEED_SERIES_REQUIRED"
    # Session fingerprint also includes duration/terrain. Use the profile-only
    # comparison key stored even on an insufficient-HR trainability summary.
    if ((shadow.get("vflat_model_version"), shadow.get("vflat_config_version")) != mapping["vflat_versions"]
            or (shadow.get("trainability_index") or {}).get("comparison_key") != mapping["comparison_key"]):
        return None, "INCOMPATIBLE_SPEED_CONFIGURATION"
    spans, reason = _missing_spans(shadow, coverage, elapsed)
    if reason:
        return None, reason
    totals = {z: {"zone": z, "raw_time_min": 0., "equivalent_time_min": 0.} for z in ZONES}
    bounds, speeds = mapping["bounds_bpm"], mapping["bounds_kmh"]
    cursor = 0
    for left, right, speed, exclusion in sample_intervals(shadow):
        if exclusion or right-left > MAX_GAP_SECONDS or speed is None or not speeds[0] <= speed <= speeds[-1]:
            continue
        # Paired TI relates speed at t to HR at t+lag. Shift only the mask
        # overlap, and never fabricate end-of-record or paused samples.
        left, right = max(0., left+LAG_SECONDS), min(elapsed, right+LAG_SECONDS)
        while cursor < len(spans) and spans[cursor][1] <= left:
            cursor += 1
        if cursor >= len(spans):
            break
        overlap = 0.; i = cursor
        while i < len(spans) and spans[i][0] < right:
            overlap += max(0., min(right, spans[i][1])-max(left, spans[i][0])); i += 1
        if overlap <= 0:
            continue
        index = min(4, max(0, bisect_right(speeds, speed)-1))
        fraction = (speed-speeds[index])/(speeds[index+1]-speeds[index])
        estimated_hr = bounds[index]+fraction*(bounds[index+1]-bounds[index])
        zone = ZONES[index]
        coefficient = linear_equivalence_coefficient(estimated_hr, bounds[index], bounds[index+1],
            equivalence_slope(zone), is_z5=zone == "Z5")
        totals[zone]["raw_time_min"] += overlap/60.
        totals[zone]["equivalent_time_min"] += overlap/60.*coefficient
    covered = sum(row["raw_time_min"] for row in totals.values())
    if covered <= 0:
        return None, "NO_SUPPORTED_MISSING_HR_SPEED_INTERVALS"
    # A mismatch is evidence of a wrong mask/ledger, not permission to scale
    # or arbitrarily truncate measured speed to manufacture coverage.
    if covered > missing_minutes+1e-8:
        return None, "MISSING_HR_TIME_MASK_MISMATCH"
    return {"zones": list(totals.values()), "covered_missing_minutes": covered,
            "model_version": VERSION, "provenance": dict(PROVENANCE),
            "equivalence_version": EQUIVALENCE_VERSION, "paired_lag_seconds": LAG_SECONDS,
            "boundary_basis": "PAIRED_INDEX_ONLY", "boundary_speeds_kmh": list(speeds)}, None


def prepare_history(repository, alias, source, calendar, speed_by_sport, settings, today):
    """Return {activity_ref: estimate}, diagnostics without mutating any input."""
    diagnostics = {"version": VERSION, "estimated_activity_count": 0, "covered_missing_minutes": 0.,
                   "provenance": dict(PROVENANCE), "skipped": []}
    estimates, candidates, mappings = {}, [], {}
    catalog = {a["activity_ref"]: a for a in calendar if a.get("activity_ref")}
    ledger = {a["activity_ref"]: a for a in source.get("activities", []) if a.get("activity_ref")}
    earliest = (today-timedelta(days=39)).isoformat()
    for ref in sorted(set(catalog) | set(ledger)):
        item, recorded = catalog.get(ref, {}), ledger.get(ref, {})
        status = recorded.get("quality_status", item.get("quality_status", "valid"))
        if status not in ("limited", "excluded"):
            continue
        day = recorded.get("date") or item.get("local_date") or item.get("date")
        if not day or not earliest <= day <= today.isoformat():
            continue
        duration = _number(recorded.get("duration_min", item.get("duration_min")))
        coverage = _number(recorded.get("hr_coverage_percent", item.get("hr_coverage_percent")))
        if duration is None or duration <= 0 or coverage is None or not 0 <= coverage < 100:
            continue
        reason = str(item.get("quality_reason") or "").lower()
        if reason and not any(word in reason for word in ("hr", "пулс", "heart rate", "heartrate")):
            continue
        known = sum(max(0., _number(z.get("raw_time_min")) or 0.) for z in recorded.get("zones", []))
        missing = min(duration*(1-coverage/100), max(0., duration-known))
        if missing <= 0:
            continue
        sport = recorded.get("sport") or item.get("sport")
        if sport not in mappings:
            mappings[sport] = _mapping((speed_by_sport or {}).get(sport), settings, today, sport)
        mapping, reason = mappings[sport]
        run_key = item.get("latest_shadow_run_key")
        if mapping is None or not run_key:
            diagnostics["skipped"].append({"activity_ref": ref, "reason": reason or "NO_PINNED_SPEED_RUN"})
            continue
        elapsed = _number(item.get("elapsed_time_s")) or duration*60
        candidates.append((ref, run_key, mapping, missing, coverage, elapsed))
    if not candidates:
        return estimates, diagnostics
    reader = getattr(repository, "activity_speed_history_samples", None)
    if reader is None:
        diagnostics["reason"] = "BATCH_SPEED_HISTORY_UNAVAILABLE"
        return estimates, diagnostics
    try:
        rows = reader(alias, tuple(sorted({row[1] for row in candidates})))
    except PersistentStoreFailure:
        diagnostics["reason"] = "PINNED_SPEED_HISTORY_UNAVAILABLE"
        return estimates, diagnostics
    for ref, run_key, mapping, missing, coverage, elapsed in candidates:
        row = rows.get(run_key) or {}
        if row.get("activity_ref") != ref or not isinstance(row.get("shadow_payload"), dict):
            diagnostics["skipped"].append({"activity_ref": ref, "reason": "PINNED_SPEED_ACTIVITY_MISMATCH"})
            continue
        estimate, reason = _estimate(row["shadow_payload"], mapping,
            missing_minutes=missing, coverage=coverage, elapsed=elapsed)
        if estimate is None:
            diagnostics["skipped"].append({"activity_ref": ref, "reason": reason})
            continue
        estimates[ref] = {**estimate, "activity_ref": ref, "source_run_key": run_key,
                          "paired_index_last_activity_date": mapping["last_activity_date"]}
    diagnostics["estimated_activity_count"] = len(estimates)
    diagnostics["covered_missing_minutes"] = sum(row["covered_missing_minutes"] for row in estimates.values())
    return estimates, diagnostics
