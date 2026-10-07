"""Independent speed-derived Q/E ledger; never added to the HR ledger.

Activities use only earlier-date paired indices from 40 days. Treadmills without
a same-sport calibration can explicitly use this athlete's ordinary-run index.
This prevents the activity's own HR (or future training) defining its speed load.
Only measured Vflat is integrated; estimated HR is a conversion coordinate.
"""
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
import math
import hashlib
import json
import logging
from time import perf_counter
from zoneinfo import ZoneInfo
import pandas as pd
from fastapi import HTTPException
from biathlon.constants import COMPONENTS, fresh_parameters
from biathlon.equivalence import equivalence_slope, EQUIVALENCE_VERSION
from biathlon.physiology import linear_equivalence_coefficient, compute_daily_load_history, compute_load_statistics, rolling_load_statistics
from biathlon.sport_heart_rate import reference_offset, policy
from vflat_b65.sports import speed_model_versions, supports_speed_load, is_treadmill
from .activity_shadow_pipeline import activity_shadow_configuration_fingerprint
from .speed_segments import sample_intervals
from .speed_load_cache import speed_load_cache, speed_activity_cache
from .trainability_history import history_from_calendar, read_calendar, robust_mean
from .trainability import MIN_SECONDS_BY_BAND, MAX_GAP_SECONDS

VERSION="independent-speed-load-causal-ti-v1"
ZONES=tuple(f"Z{i}" for i in range(1,6))
logger = logging.getLogger("uvicorn.error")


def _elapsed_ms(started):
    return (perf_counter()-started)*1000


def finite_positive(value):
    return isinstance(value,(float,int)) and not isinstance(value,bool) and math.isfinite(value) and value>0


def zone_mapping(indices, bounds, hrmax, sport):
    """Continuous speed boundaries; expose any use of the overall TI.

An unavailable zone can use a measured GENERAL index for this same sport.
This is an explicit extrapolative estimate, never a fabricated zonal index.
Conflicting zonal boundaries can use the whole measured GENERAL relationship;
without that support the map remains unavailable. No boundaries are sorted.
"""
    if not finite_positive(hrmax) or len(bounds)!=6 or not all(finite_positive(b) for b in bounds) or not all(a<b for a,b in zip(bounds,bounds[1:])):
        return None,"HR_PROFILE_REQUIRED"
    def usable(name):
        band=indices.get(name) or {}
        return (finite_positive(band.get("index")) and band.get("count",0)>=1
                and band.get("seconds",0)>=MIN_SECONDS_BY_BAND[name])
    selected=[]
    for zone in ZONES:
        key=zone if usable(zone) else "GENERAL" if usable("GENERAL") else None
        if key is None:
            break
        selected.append({"zone":zone,"index":indices[key]["index"],"index_source":key,
                         "count":indices[key]["count"],"seconds":indices[key]["seconds"]})
    if not selected:
        return None,"NO_PRIOR_SPORT_INDEX"
    speeds=[100*bounds[0]/hrmax/selected[0]["index"]]
    speeds.extend(100*bounds[i+1]/hrmax/b["index"] for i,b in enumerate(selected))
    basis="ZONAL_WITH_GENERAL_FALLBACK" if any(b["index_source"]=="GENERAL" for b in selected) else "ZONAL_INDICES"
    if not all(a<b for a,b in zip(speeds,speeds[1:])):
        if not usable("GENERAL"):return None,"NONMONOTONE_INDEX_BOUNDARIES"
        general=indices["GENERAL"]
        selected=[{"zone":z,"index":general["index"],"index_source":"GENERAL",
                   "count":general["count"],"seconds":general["seconds"]} for z in ZONES]
        speeds=[100*b/hrmax/general["index"] for b in bounds]
        basis="GENERAL_CONFLICT_FALLBACK"
    return {"sport":sport,"bounds_kmh":speeds,"reference_bounds_bpm":list(bounds[:len(speeds)]),
            "mapping_basis":basis,
            "zones":selected,"hr_policy":policy(sport,bounds,hrmax),
            "uses_general_index":any(b["index_source"]=="GENERAL" for b in selected)},None


def convert_speed(mapping, speed):
    if not finite_positive(speed) or not mapping["bounds_kmh"][0]<=speed<=mapping["bounds_kmh"][-1]:
        return None
    speeds=mapping["bounds_kmh"];bounds=mapping["reference_bounds_bpm"]
    i=min(len(speeds)-2,bisect_right(speeds,speed)-1)
    fraction=(speed-speeds[i])/(speeds[i+1]-speeds[i])
    hr=bounds[i]+fraction*(bounds[i+1]-bounds[i])
    zone=ZONES[i]
    coefficient=linear_equivalence_coefficient(hr,bounds[i],bounds[i+1],equivalence_slope(zone),is_z5=zone=="Z5")
    return {"zone":zone,"reference_hr_bpm":hr,"sport_hr_bpm":hr-reference_offset(mapping["sport"]),
            "coefficient":coefficient}


def _integration_converter(mapping):
    """Prepare the unchanged scalar conversion for the Q integration loop.

    The public conversion above remains the reference including diagnostic HR.
    Integration only needs a zone and coefficient. For finite scalar HR, Python
    bounds give exactly the same clamp as NumPy clip without its per-sample
    array dispatch. Interpolation, arithmetic order and sequential Q summation
    are unchanged; nonfinite interpolation still uses the reference function.
    """
    speeds = mapping["bounds_kmh"]
    bounds = mapping["reference_bounds_bpm"]
    last = len(speeds)-2
    prepared = [(ZONES[i], max(0., float(equivalence_slope(ZONES[i])))/100.,
                 float(bounds[i]), float(bounds[i+1])) for i in range(len(speeds)-1)]
    def convert(speed):
        if not finite_positive(speed) or not speeds[0] <= speed <= speeds[-1]:
            return None
        i = min(last, bisect_right(speeds, speed)-1)
        fraction = (speed-speeds[i])/(speeds[i+1]-speeds[i])
        hr = bounds[i]+fraction*(bounds[i+1]-bounds[i])
        zone, slope, low, high = prepared[i]
        if not math.isfinite(hr):
            coefficient = linear_equivalence_coefficient(hr, bounds[i], bounds[i+1],
                equivalence_slope(zone), is_z5=zone == "Z5")
        elif zone == "Z5":
            coefficient = max(1., 1.+slope*(min(float(hr), high)-low))
        else:
            coefficient = min(1., max(0., 1.-slope*(high-float(hr))))
        return zone, coefficient
    return convert


def speed_at_reference_hr(mapping, hr):
    if not mapping or not finite_positive(hr):return None
    bounds=mapping["reference_bounds_bpm"];speeds=mapping["bounds_kmh"]
    if not bounds[0]<=hr<=bounds[-1]:return None
    i=min(len(bounds)-2,bisect_right(bounds,hr)-1)
    return speeds[i]+(hr-bounds[i])/(bounds[i+1]-bounds[i])*(speeds[i+1]-speeds[i])


def prior_indices(history, sport, day, settings):
    first=(day-timedelta(days=40)).isoformat();last=day.isoformat()
    key=activity_shadow_configuration_fingerprint(settings.zone_bounds_bpm,settings.hrmax_bpm,sport=sport)
    previous=[a for a in history if a["sport"]==sport and first<=a["local_date"]<last
              and a.get("index") and a["index"].get("admission",{}).get("status")=="ACCEPTED"
              and a["index"].get("comparison_key")==key
              and a["index"].get("source_versions",{}).get("vflat")==speed_model_versions(sport)[0]
              and a["index"].get("hr_reference_offset_bpm",0)==reference_offset(sport)]
    indices={}
    for name in (*ZONES,"GENERAL"):
        bands=[b for a in previous for b in [*a["index"]["zones"],a["index"]["general"]]
               if b["name"]==name and b["valid"] and finite_positive(b.get("index"))]
        indices[name]={"index":robust_mean([b["index"] for b in bands],[b["hr_seconds"] for b in bands]) if bands else None,
                       "count":len(bands),"seconds":sum(b["hr_seconds"] for b in bands)}
    return indices


def activity_samples(repository, alias, activities, *, metrics=None):
    """Bound live full-resolution data to ten activities, not an entire season."""
    reader=getattr(repository,"activity_speed_exposure_samples",None)
    for first in range(0,len(activities),10):
        batch=activities[first:first+10]
        keys=tuple(sorted({a["latest_shadow_run_key"] for a in batch if a.get("latest_shadow_run_key")}))
        started = perf_counter()
        rows=reader(alias,keys) if keys and reader else {}
        if metrics is not None:
            metrics["samples_read_ms"] = metrics.get("samples_read_ms", 0.) + _elapsed_ms(started)
        for activity in batch:
            yield activity,rows.get(activity.get("latest_shadow_run_key")) or {}


def calibrated_mapping(history, sport, day, settings):
    indices = prior_indices(history, sport, day, settings)
    mapping, reason = zone_mapping(indices, settings.zone_bounds_bpm, settings.hrmax_bpm, sport)
    if mapping is None and reason == "NO_PRIOR_SPORT_INDEX" and is_treadmill(sport):
        # Explicit cold-start estimate from this athlete's prior ordinary runs.
        # Both channels express flat-equivalent running speed. Never bootstrap
        # the current treadmill activity from its own HR or future observations.
        fallback = prior_indices(history, "Run", day, settings)
        alternative, _ = zone_mapping(fallback, settings.zone_bounds_bpm, settings.hrmax_bpm, sport)
        if alternative:
            indices, mapping, reason = fallback, {**alternative, "index_sport": "Run"}, None
    return indices, mapping, reason


def _activity_record(activity, row, mapping, reason):
    """Integrate one immutable speed stream; preserve sample order and formulas."""
    s = activity["sport"]
    shadow = row.get("shadow_payload") or {}
    compatible = (row.get("activity_ref") == activity["activity_ref"] and
        (shadow.get("vflat_model_version"), shadow.get("vflat_config_version")) == speed_model_versions(s)
        and isinstance(shadow.get("speed_test_series"), list))
    if not compatible:
        reason = "SPEED_RECOMPUTATION_REQUIRED"
    totals = {z: {"zone": z, "minutes": 0., "equivalent_minutes": 0.} for z in ZONES}
    excluded = outside = 0.
    if mapping and compatible:
        convert = _integration_converter(mapping)
        for left, right, speed, exclusion in sample_intervals(shadow):
            dt = right-left
            if exclusion or dt > MAX_GAP_SECONDS:
                excluded += dt
                continue
            estimate = convert(speed)
            if estimate is None:
                outside += dt
                continue
            zone, coefficient = estimate
            total = totals[zone]
            total["minutes"] += dt/60
            total["equivalent_minutes"] += dt/60*coefficient
    covered = sum(z["minutes"] for z in totals.values())
    duration = max(activity.get("duration_min") or 0.,
                   (activity.get("elapsed_time_s") or activity.get("moving_time_s") or 0.)/60,
                   covered+(excluded+outside)/60)
    return {"activity_ref": activity["activity_ref"], "date": activity["local_date"], "sport": s,
            "recorded_minutes": float(duration), "classified_minutes": covered,
            "excluded_speed_minutes": excluded/60, "outside_mapping_minutes": outside/60,
            "status": "CLASSIFIED" if covered else "UNAVAILABLE", "reason": reason,
            "assumes_flat_incline": bool(compatible and any(
                r.get("grade_assumed_flat") for r in shadow["speed_test_series"])),
            "mapping": mapping, "zones": list(totals.values())}


def _activity_records(repository, alias, selected, admitted, settings, namespace, metrics=None):
    mapping_started = perf_counter()
    mappings, inputs = {}, []
    for activity in selected:
        day = date.fromisoformat(activity["local_date"])
        key = (activity["sport"], day)
        if key not in mappings:
            _, mapping, reason = calibrated_mapping(admitted, activity["sport"], day, settings)
            mappings[key] = mapping, reason
        inputs.append((activity, *mappings[key]))
    if metrics is not None:
        metrics["mapping_ms"] = _elapsed_ms(mapping_started)
    def integrate(activity, row, mapping, reason):
        started = perf_counter()
        result = _activity_record(activity, row, mapping, reason)
        if metrics is not None:
            metrics["integration_ms"] = metrics.get("integration_ms", 0.) + _elapsed_ms(started)
        return result
    if not namespace:
        return [integrate(activity, row, mapping, reason)
                for (activity, row), (_, mapping, reason) in zip(
                    activity_samples(repository, alias, selected, metrics=metrics), inputs)]
    keyed, record_keys, unpinned = {}, [], {}
    for position, (activity, mapping, reason) in enumerate(inputs):
        # An activity without a pinned stream is unavailable and may be
        # repaired in place. Keep it out of the cache without discarding reuse
        # for all the other, immutable activities in this partial report.
        if not activity.get("latest_shadow_run_key"):
            record_keys.append(None)
            unpinned[position] = integrate(activity, {}, mapping, reason)
            continue
        # No generation/period in the key: an unchanged immutable stream with
        # the identical prior-40-day mapping has the identical direct Q. Old
        # corrections still recalculate admission and all causal mappings.
        identity = {"namespace": namespace, "alias": alias, "version": VERSION,
                    "equivalence": EQUIVALENCE_VERSION,
                    "activity": {name: activity.get(name) for name in ("activity_ref", "sport", "local_date",
                        "duration_min", "elapsed_time_s", "moving_time_s", "latest_shadow_run_key")},
                    "mapping": mapping, "reason": reason,
                    "speed_versions": speed_model_versions(activity["sport"]),
                    "max_gap_seconds": MAX_GAP_SECONDS}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        record_keys.append(key)
        keyed[key] = activity, mapping, reason
    def compute(missing):
        activities = [keyed[key][0] for key in missing]
        source_keys = {(keyed[key][0]["activity_ref"], keyed[key][0]["latest_shadow_run_key"]): key for key in missing}
        results = {}
        # Consume each ten-activity sample batch immediately. Only the small
        # integrated results survive; full-season streams are never retained.
        for activity, row in activity_samples(repository, alias, activities, metrics=metrics):
            key = source_keys[activity["activity_ref"], activity["latest_shadow_run_key"]]
            _, mapping, reason = keyed[key]
            results[key] = integrate(activity, row, mapping, reason)
        return results
    cached = dict(zip(keyed, speed_activity_cache.get_or_compute_many(list(keyed), compute, stats=metrics)))
    return [cached[key] if key is not None else unpinned[position] for position, key in enumerate(record_keys)]


def history_view(repository, alias, sport=None, *, today=None, as_of=None, period_start=None, period_end=None):
    started = phase_started = perf_counter()
    metrics = {"report_cache": "disabled"}
    settings=repository.athlete_settings(alias)
    metrics["settings_ms"] = _elapsed_ms(phase_started)
    if settings is None:raise HTTPException(409,"Athlete settings are required")
    today=today or datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone)).date()
    if as_of is not None:
        if period_start is not None or period_end is not None:
            raise HTTPException(422, "Choose either an analysis date or a custom report period")
        if as_of < date.min + timedelta(days=89):
            raise HTTPException(422, "Analysis date is too early for the report history")
        if as_of > today:
            raise HTTPException(422, "Choose a past or current analysis date")
        # Anchor the default report to the persisted HR analysis date without
        # changing its first-observed-day, warmup or causal load semantics.
        today = as_of
    if (period_start is None) != (period_end is None):
        raise HTTPException(422, "Both report dates are required")
    custom_period = period_start is not None
    if custom_period:
        if period_start > period_end or (period_end-period_start).days >= 366 or period_end > today:
            raise HTTPException(422, "Choose a past or current report period of 1 to 366 days")
        today = period_end
    start = period_start if custom_period else today-timedelta(days=39)
    warmup = min(start-timedelta(days=39), today-timedelta(days=89))
    phase_started = perf_counter()
    calendar=read_calendar(repository,alias,date.min,today) or {"activities":[]}
    metrics["calendar_ms"] = _elapsed_ms(phase_started)
    phase_started = perf_counter()
    admitted=history_from_calendar(repository,alias,calendar)
    metrics["admission_ms"] = _elapsed_ms(phase_started)
    def compute():
        return _compute_history(repository, alias, sport, settings, today, start, warmup,
                                calendar, admitted, custom_period, metrics)
    def finish(result):
        # No athlete identifiers, settings, mappings or source keys are logged.
        logger.info("onflows_speed_load elapsed_ms=%.1f report_cache=%s "
                    "settings_ms=%.1f calendar_ms=%.1f admission_ms=%.1f key_ms=%.1f "
                    "mapping_ms=%.1f samples_read_ms=%.1f integration_ms=%.1f daily_statistics_ms=%.1f "
                    "activity_cache_hits=%d activity_cache_misses=%d activity_cache_waits=%d",
                    _elapsed_ms(started), metrics["report_cache"],
                    *(metrics.get(name, 0.) for name in ("settings_ms", "calendar_ms", "admission_ms", "key_ms",
                        "mapping_ms", "samples_read_ms", "integration_ms", "daily_statistics_ms")),
                    *(metrics.get(name, 0) for name in ("activity_cache_hits", "activity_cache_misses", "activity_cache_waits")))
        return result
    namespace = getattr(repository, "speed_load_cache_namespace", None)
    if not namespace or not calendar.get("generation_id"):
        return finish(compute())
    # Fresh authorization happens at the route, and these inputs are reread on
    # every request. Immutable run keys, admitted summaries and source metadata
    # invalidate reuse when syncs, corrections or calibration settings change.
    phase_started = perf_counter()
    identity = {
        "namespace": namespace, "alias": alias, "sport": sport, "version": VERSION,
        "generation": calendar.get("generation_id"), "revision": calendar.get("revision"),
        "bounds": settings.zone_bounds_bpm, "hrmax": settings.hrmax_bpm, "timezone": settings.timezone,
        "start": start.isoformat(), "end": today.isoformat(), "custom": custom_period,
        "activities": calendar.get("activities", []), "admitted": admitted,
    }
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    metrics["key_ms"] = _elapsed_ms(phase_started)
    return finish(speed_load_cache.get_or_compute(key, compute, stats=metrics))


def _compute_history(repository, alias, sport, settings, today, start, warmup,
                     calendar, admitted, custom_period, metrics=None):
    available=sorted({a["sport"] for a in calendar.get("activities",[]) if supports_speed_load(a.get("sport"))})
    if sport is not None and not supports_speed_load(sport):raise HTTPException(422,"Unsupported speed-load sport")
    selected=[a for a in calendar.get("activities",[]) if supports_speed_load(a.get("sport"))
              and (sport is None or a["sport"]==sport) and warmup.isoformat()<=a.get("local_date","")<=today.isoformat()]
    selected.sort(key=lambda a:(a["local_date"],a["activity_ref"]))
    namespace = getattr(repository, "speed_load_cache_namespace", None) if calendar.get("generation_id") else None
    records = _activity_records(repository, alias, selected, admitted, settings, namespace, metrics)
    statistics_started = perf_counter()
    activity_summaries = [{"date": pd.Timestamp(a["date"]),
                          **{f"q_{z}": next((b["equivalent_minutes"] for b in a["zones"] if b["zone"] == z), 0.)
                             for z in COMPONENTS}} for a in records]
    # No-activity calendar days are zero. Unsupported activity minutes remain
    # disclosed; Q/E and ratios describe only the covered speed history.
    first=max(warmup,min((date.fromisoformat(a["local_date"]) for a in selected),default=start))
    if custom_period:
        first = min(first, start)
    activity_summaries.insert(0,{"date":pd.Timestamp(first),**{f"q_{z}":0. for z in COMPONENTS}})
    parameters=fresh_parameters()
    loads=compute_daily_load_history(pd.DataFrame(activity_summaries),parameters,today)
    statistics=compute_load_statistics(loads,parameters,today)
    visible=[a for a in records if a["date"]>=start.isoformat()]
    count=sum(a["classified_minutes"]>0 for a in visible)
    total_recorded=sum(a["recorded_minutes"] for a in visible);covered=sum(a["classified_minutes"] for a in visible)
    # The load history is already one row per calendar day. Existing rolling
    # statistics use the same inclusive windows and base formula without
    # repeatedly filtering the growing history for every chart day.
    ratios = rolling_load_statistics(loads, parameters).pivot(index="date", columns="component", values="index_7_40")
    daily=[]
    for day,row in loads.iterrows():
        if day.date()<start:continue
        for z in ZONES:
            daily.append({"date":day.date().isoformat(),"zone":z,"equivalent_minutes":float(row[f"q_{z}"]),
                          "effective_load":float(row[f"e_{z}"]),"ratio_7_40":float(ratios.loc[day,z]) if count else None})
    if metrics is not None:
        metrics["daily_statistics_ms"] = _elapsed_ms(statistics_started)
    current_indices=[]
    for s in available if sport is None else [sport]:
        indices,mapping,reason=calibrated_mapping(admitted,s,today+timedelta(days=1),settings)
        current_indices.append({"sport":s,"indices":indices,"mapping":mapping,"reason":reason,
                                "reference_hr_bpm":settings.zone_bounds_bpm[2],
                                "comparison_speed_kmh":speed_at_reference_hr(mapping,settings.zone_bounds_bpm[2]),
                                "hr_policy":policy(s,settings.zone_bounds_bpm,settings.hrmax_bpm)})
    return {"schema_version":"speed-load-history-v1","model_version":VERSION,
            "sport":sport,"sports":available,"start_date":start.isoformat(),"end_date":today.isoformat(),
            "status":"AVAILABLE" if count and covered>=total_recorded-1e-6 else "PARTIAL" if count else "UNAVAILABLE",
            "source_generation_id":calendar.get("generation_id"),"source_revision":calendar.get("revision"),
            "load_role":"PARALLEL_ESTIMATE_NOT_ADDED_TO_HR","equivalence_version":EQUIVALENCE_VERSION,
            "mapping_policy":"PRIOR_40_DAYS_EXCLUDING_CURRENT_DAY_WITH_TREADMILL_RUN_FALLBACK",
            "recorded_minutes":total_recorded,"classified_minutes":covered,
            "coverage_percent":100*covered/total_recorded if total_recorded else 0.,
            "activities":visible,"sport_indices":current_indices,"daily":daily,
            "zones":[{"zone":z,"minutes":sum(a["zones"][i]["minutes"] for a in visible),
                      "equivalent_minutes":sum(a["zones"][i]["equivalent_minutes"] for a in visible),
                      "effective_load":float(loads.loc[loads.index>=pd.Timestamp(start),f"e_{z}"].sum()),
                      "e7_daily":float(statistics.loc[z,"E7_daily"]),"e40_daily":float(statistics.loc[z,"E40_daily"]),
                      "ratio_7_40":float(statistics.loc[z,"index_7_40"]) if count else None} for i,z in enumerate(ZONES)],
            "warnings":["SPEED_LOAD_IS_ESTIMATED","RATIOS_DESCRIBE_COVERED_SPEED_HISTORY_ONLY",
                        "GENERAL_INDEX_USED_WHEN_ZONE_INDEX_MISSING","CYCLING_PARAMETERS_ARE_REFERENCE_ASSUMPTIONS"]
                       + (["SPEED_RECOMPUTATION_REQUIRED"] if any(a["reason"] == "SPEED_RECOMPUTATION_REQUIRED" for a in records) else [])
                       + (["TREADMILL_GRADE_ASSUMED_FLAT"] if any(a["assumes_flat_incline"] for a in visible) else [])
                       + (["TREADMILL_PRIOR_RUN_INDEX_FALLBACK"] if any(
                           a["classified_minutes"] > 0 and (a["mapping"] or {}).get("index_sport") == "Run" for a in visible) else [])}
