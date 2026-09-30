"""Profile-scoped model settings, frozen performance tests and recovery view.

The immutable load snapshot remains the source. The v2 projection is evaluated
with one settings revision for all readiness surfaces, including older snapshots.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from urllib.parse import quote
from zoneinfo import ZoneInfo
from fastapi import HTTPException

from biathlon import recovery_v2, speed_duration, preliminary_capacity
from biathlon.athlete_functional_profile import build_functional_profile
from .trainability_history import HISTORY_DAYS, history_from_calendar, robust_mean, read_calendar, window_start
from .model_schemas import RecoveryConfigInput, RecoveryHistoryV2, initial_settings
from .oauth_store import PersistentStoreFailure
from .speed_segments import segment_measurement

SPEED_TEST_DAYS=90


def _maximal_test(test):
    """Only an explicitly accepted maximal measurement is a hard anchor."""
    return (test.get("test_mode", "STRICT") == "STRICT"
            and test.get("maximal") is True and test.get("comparable") is True
            and str(test.get("source", "")).upper() not in {"MODEL", "MODEL_GENERATED", "GENERATED", "ESTIMATED", "REFERENCE"}
            and not test.get("generated") and not test.get("is_estimated")
            and test.get("is_maximal_test") is not False)


def enabled():
    return os.getenv("ONFLOWS_RECOVERY_V2_ENABLED", "false").lower()=="true"


class ModelStore:
    def __init__(self, repository): self.repository=repository

    def entries(self,alias):
        rows=[]
        for offset in range(0,10000,1000):
            result=self.repository._request("GET", "/onflows_model_entries?select=kind,entry_key,revision,payload,recorded_at"
                f"&athlete_alias=eq.{quote(alias,safe='')}&order=kind.asc,entry_key.asc,revision.desc&limit=1000&offset={offset}")
            batch=self.repository._json(result)
            if not isinstance(batch,list): raise PersistentStoreFailure("Invalid model entries")
            rows.extend(batch)
            if len(batch)<1000: break
        else: raise PersistentStoreFailure("Model history exceeds bounded read")
        latest={}
        for row in rows: latest.setdefault((row["kind"],row["entry_key"]),row)
        return list(latest.values())

    def config(self,alias,entries=None):
        for row in entries if entries is not None else self.entries(alias):
            if row["kind"]=="RECOVERY":
                parsed=RecoveryConfigInput(zones=row["payload"]["zones"],expected_revision=row["revision"])
                return parsed.model_dump(mode="json")
        return {"expected_revision":0,"zones":initial_settings()}

    def save(self,alias,kind,key,payload,revision,actor):
        result=self.repository._json(self.repository._request("POST","/rpc/save_onflows_model_entry",json={
            "p_alias":alias,"p_kind":kind,"p_key":key,"p_payload":payload,
            "p_expected_revision":revision,"p_actor":str(actor)}))
        if not isinstance(result,dict): raise PersistentStoreFailure("Invalid model save result")
        if result.get("conflict"): raise HTTPException(409,"Model input changed; reload before editing")
        return result


def project_recovery(repository,alias,snapshot,*,now=None,config=None):
    if not enabled() or snapshot is None: return snapshot
    cfg=config or ModelStore(repository).config(alias)
    settings=repository.athlete_settings(alias)
    if settings is None: raise ValueError("Athlete settings are required")
    today=(now or datetime.now(timezone.utc)).astimezone(ZoneInfo(settings.timezone)).date()
    source=snapshot["load_history"]
    daily=[{"date":r["date"],"zone":r["zone"],"effective_load":r["effective_load"]} for r in source["daily"]]
    daily += [{"date":r["date"],"zone":"STR","effective_load":r["effective_load"]}
              for r in (source.get("strength") or {}).get("daily",[])]
    if not daily: raise ValueError("Daily E history is required for Recovery v2")
    result=recovery_v2.simulate(daily,cfg["zones"],target=today)
    stale=date.fromisoformat(source["period_end"]) < today
    fingerprint=sha256(json.dumps({"model":recovery_v2.VERSION,"zones":cfg["zones"]},sort_keys=True,separators=(",",":")).encode()).hexdigest()
    history=RecoveryHistoryV2(**result,athlete_id=alias,
        period_start=source["period_start"],period_end=source["period_end"],
        model={"algorithm_version":recovery_v2.VERSION,"parameter_version":f"profile-{cfg['expected_revision']}",
               "parameter_fingerprint":fingerprint,"practical_full_recovery_percent":90},
        config_revision=cfg["expected_revision"],settings=cfg["zones"],source_as_of=source["period_end"],source_stale=stale,
        wellness_diagnostics=(snapshot.get("recovery_history") or {}).get("wellness_diagnostics"),
        warnings=["EXPERT_MODEL", "DAILY_AGGREGATED_DOSES", "UNKNOWN_FATIGUE_BEFORE_HISTORY"]
            + (["SOURCE_STALE_NO_NEW_LOAD_ASSUMPTION"] if stale else [])
            + (["BASELINE_USES_PRIOR"] if any(r["baseline_source"]!="PERSONAL" for r in result["current"]) else []))
    out=deepcopy(snapshot)
    out["recovery_history"]=history.model_dump(mode="json")
    by_zone={r["zone"]:r for r in result["current"]}
    for row in out["training_status"]["zones"]:
        current=by_zone[row["zone"]]
        row["recovery_readiness_percent"]=current["readiness_percent"]
        row["recovery_days_to_full"]=current["days_to_practical_recovery"]
    return out


def save_config(repository,alias,body,actor):
    return ModelStore(repository).save(alias,"RECOVERY","recovery-v2",{"schema_version":"recovery-config-v2","zones":body.model_dump(mode="json")["zones"]},body.expected_revision,actor)


def save_test(repository,alias,body,actor):
    key=sha256(f"{body.activity_ref}:{body.start_s}:{body.duration_s}".encode()).hexdigest()[:32]
    entries=ModelStore(repository).entries(alias)
    if not body.enabled:
        old=next((e for e in entries if e["kind"]=="SPEED_TEST" and e["entry_key"]==key),None)
        if old is None: raise HTTPException(404,"Saved test is unavailable")
        return ModelStore(repository).save(alias,"SPEED_TEST",key,{**old["payload"],"enabled":False},body.expected_revision,actor)
    row=repository.active_activity_view(alias,body.activity_ref)
    if not row: raise HTTPException(404,"Activity is unavailable for this athlete")
    # Use the repository's decoder for packed immutable shadow samples.
    activity=row["catalog_payload"]
    shadow=row.get("shadow_payload")
    if not shadow: raise HTTPException(409,"Activity requires a shadow refresh")
    if body.expected_source_run_key and body.expected_source_run_key != row.get("shadow_run_key"):
        raise HTTPException(409,"Activity analysis changed; preview the segment again")
    day=activity.get("local_date") or activity.get("date")
    settings=repository.athlete_settings(alias)
    today=datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone)).date()
    if not day or not window_start(today,SPEED_TEST_DAYS).isoformat()<=day<=today.isoformat():
        raise HTTPException(422,"Choose a test within the last 90 days")
    measured=segment_measurement(shadow,body.start_s,body.duration_s,body.test_mode)
    payload=body.model_dump(mode="json",exclude={"expected_revision","expected_source_run_key"})
    payload.update(measured)
    payload.update({"schema_version":"speed-test-v1","day":day,"sport":activity["sport"],
        "input_hash":shadow.get("input_hash"),"configuration_fingerprint":shadow.get("configuration_fingerprint"),
        "vflat_version":shadow.get("vflat_model_version"),"vflat_config_version":shadow.get("vflat_config_version"),
        "hrmod_version":shadow.get("hrmod_model_version"),
        "source_run_key":row.get("shadow_run_key")})
    comparable=[e["payload"] for e in entries if e["kind"]=="SPEED_TEST" and e["entry_key"]!=key
        and e["payload"].get("enabled") and _maximal_test(e["payload"]) and e["payload"].get("sport")==payload["sport"]
        and (e["payload"].get("source")=="MANUAL" or
             (e["payload"].get("vflat_version"),e["payload"].get("vflat_config_version"))==(payload["vflat_version"],payload["vflat_config_version"]))
        and window_start(today,SPEED_TEST_DAYS).isoformat()<=e["payload"].get("day","")<=today.isoformat()]
    if body.enabled and _maximal_test(payload):
        try: speed_duration.calibrated(comparable+[payload])
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    return ModelStore(repository).save(alias,"SPEED_TEST",key,payload,body.expected_revision,actor)


def save_manual_test(repository,alias,body,actor):
    key=f"manual_{body.test_id.hex}"
    store=ModelStore(repository)
    entries=store.entries(alias)
    old=next((e for e in entries if e["kind"]=="SPEED_TEST" and e["entry_key"]==key),None)
    if (body.expected_revision or not body.enabled) and old is None:
        raise HTTPException(404,"Saved test is unavailable")
    if old and old["payload"].get("source")!="MANUAL":
        raise HTTPException(409,"Model input changed; reload before editing")
    if not body.enabled:
        payload={**old["payload"],"enabled":False}
    else:
        settings=repository.athlete_settings(alias)
        if settings is None: raise HTTPException(409,"Athlete settings are required")
        today=datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone)).date()
        start=window_start(today,SPEED_TEST_DAYS)
        if not start<=body.day<=today:
            raise HTTPException(422,"Choose a test within the last 90 days")
        speed=body.speed_kmh if body.speed_kmh is not None else body.distance_m/body.duration_s*3.6
        payload=body.model_dump(mode="json",exclude={"expected_revision"})
        payload.update({"schema_version":"speed-test-v1","source":"MANUAL","test_mode":"STRICT",
            "start_s":0,"speed_kmh":speed,
            "distance_m":body.distance_m if body.distance_m is not None else speed*body.duration_s/3.6,
            "measurement_input":"DISTANCE" if body.distance_m is not None else "SPEED",
            "distance_basis":"MEASURED_FLAT","coverage_percent":None})
        comparable=[e["payload"] for e in entries if e["kind"]=="SPEED_TEST" and e["entry_key"]!=key
            and e["payload"].get("enabled") and _maximal_test(e["payload"]) and e["payload"].get("sport")==body.sport
            and start.isoformat()<=e["payload"].get("day","")<=today.isoformat()]
        imported=[t for t in comparable if t.get("source")!="MANUAL"]
        if len({(t.get("vflat_version"),t.get("vflat_config_version")) for t in imported})>1:
            raise HTTPException(422,"Resolve incompatible Vflat test versions first")
        try: speed_duration.calibrated(comparable+[payload])
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    result=store.save(alias,"SPEED_TEST",key,payload,body.expected_revision,actor)
    return {**result,"entry_key":key}


def speed_view(repository,alias,sport=None,*,duration_s=None,distance_m=None,speed_kmh=None,hr_bpm=None):
    from biathlon import hr_speed
    from .activity_shadow_pipeline import activity_shadow_configuration_fingerprint
    from vflat_b65 import MODEL_VERSION as VFLAT_VERSION, CONFIG_VERSION as VFLAT_CONFIG
    settings=repository.athlete_settings(alias)
    if settings is None: raise HTTPException(409,"Athlete settings are required")
    today=datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone)).date()
    start=window_start(today,SPEED_TEST_DAYS)
    calendar=read_calendar(repository,alias,date.min,today) or {"activities":[]}
    activities=[a for a in (calendar.get("activities") or []) if start.isoformat()<=a["local_date"]<=today.isoformat()]
    entries=[e for e in ModelStore(repository).entries(alias) if e["kind"]=="SPEED_TEST"]
    sports=sorted({r["sport"] for r in activities}|{e["payload"]["sport"] for e in entries})
    sport=sport or (sports[0] if sports else "Run")
    selected=[e["payload"] for e in entries if e["payload"].get("enabled") and e["payload"]["sport"]==sport
           and start.isoformat()<=e["payload"]["day"]<=today.isoformat()]
    tests=[t for t in selected if _maximal_test(t)]
    imported=[t for t in tests if t.get("source")!="MANUAL"]
    warnings=["EXPERT_REFERENCE_NOT_POPULATION_VALIDATED", "HEART_RATE_ESTIMATE_FROM_PAIRED_INDEX"]
    incompatible_tests=len({(t.get("vflat_version"),t.get("vflat_config_version")) for t in imported})>1
    if incompatible_tests: warnings.append("INCOMPARABLE_MODEL_VERSIONS")
    exploratory_count=sum(t.get("test_mode")=="EXPLORATORY" for t in selected)
    if exploratory_count:
        warnings.append("EXPLORATORY_OBSERVATIONS_NOT_MAXIMAL_TESTS")
    if len(selected)>len(tests): warnings.append("NONMAXIMAL_OBSERVATIONS_NOT_USED_AS_ANCHORS")
    history=history_from_calendar(repository,alias,calendar)
    index_start=window_start(today)
    recent=[a for a in history if a["sport"]==sport and index_start.isoformat()<=a["local_date"]<=today.isoformat()]
    comparison_key=activity_shadow_configuration_fingerprint(settings.zone_bounds_bpm,settings.hrmax_bpm)
    # Imported Vflat tests are frozen measurements. A config change can alter
    # speed even when its public model version stays the same. Manual flat tests
    # have no Vflat dependency and remain compatible with the current index.
    tests_match_index=all(t.get("source")=="MANUAL" or
        (t.get("vflat_version"),t.get("vflat_config_version"))==(VFLAT_VERSION,VFLAT_CONFIG) for t in tests)
    compatible=[];incompatible=0
    for activity in recent:
        index=activity.get("index")
        if not index or index.get("admission",{}).get("status")!="ACCEPTED":continue
        if (not tests_match_index or index.get("hrmax_bpm")!=settings.hrmax_bpm
            or list(index.get("zone_bounds_bpm",[]))!=list(settings.zone_bounds_bpm)
            or index.get("comparison_key")!=comparison_key
            or index.get("source_versions",{}).get("vflat")!=VFLAT_VERSION):
            incompatible+=1;continue
        compatible.append(activity)
    if incompatible or not tests_match_index:warnings.append("INCOMPARABLE_INDEX_CONFIGURATION")
    indices={}
    used_dates=[]
    for activity in compatible:
        if any(b["valid"] for b in [*activity["index"]["zones"],activity["index"]["general"]]):
            used_dates.append(activity["local_date"])
    for zone in [*speed_duration.VOLUME_RANGES_MIN,"GENERAL"]:
        values=[];weights=[]
        for activity in compatible:
            index=activity["index"]
            for band in [*index["zones"],index["general"]]:
                if band["name"]==zone and band["valid"]:
                    values.append(band["index"]);weights.append(band["hr_seconds"])
        indices[zone]={"index":robust_mean(values,weights) if values else None,"count":len(values),"seconds":sum(weights)}
    admission={"activities":len(recent),"excluded":sum(a.get("index",{}).get("admission",{}).get("status")=="EXCLUDED" for a in recent if a.get("index")),
               "refresh_required":sum(a["unavailable_reason"]=="REFRESH_REQUIRED" for a in recent),
               "used":len(used_dates),"incompatible":incompatible}
    source=(calendar.get("snapshot_payload") or {}).get("load_history") or {}
    volume_evidence=_speed_volume_evidence(source,calendar.get("activities") or [],today)
    volumes=volume_evidence["zone_weekly_q"]
    prior=preliminary_capacity.build(settings.zone_bounds_bpm,settings.hrmax_bpm,indices,
        zone_weekly_q=volumes if volume_evidence["basis"]=="HR_MEASURED" else None,
        total_weekly_minutes=volume_evidence["total_weekly_minutes"],position_basis=volume_evidence["basis"])
    warnings.extend(prior["warnings"])
    tuned=None
    model_error=None
    if incompatible_tests:
        model_error="INCOMPARABLE_MODEL_VERSIONS"
    elif tests:
        try: tuned=speed_duration.calibrated(tests,prior=prior["curve"])
        except ValueError:
            model_error="CONFLICTING_TESTS"
            warnings.append(model_error)
    else:
        tuned=prior["curve"]
    # History has already positioned the preliminary expert TIME anchors.
    # Never deform the calibrated curve again with a second volume correction.
    corrections=[speed_duration.volume_correction(z,volumes[z]) for z in volumes]
    predictor=None
    if tuned and settings.hrmax_bpm:
        try:
            predictor=hr_speed.Predictor(tuned,settings.zone_bounds_bpm,settings.hrmax_bpm,indices,
                expert_durations={a["zone"]:a["duration_s"] for a in prior["anchors"]})
        except ValueError:
            warnings.append("HR_SPEED_MAPPING_UNAVAILABLE")
    window=[min(t["duration_s"] for t in tests),max(t["duration_s"] for t in tests)] if tests else None
    def prediction(t):
        v=tuned.speed(t)*3.6
        hr=None;meta={"hr_prediction_source":None,"hr_prediction_reason":None,"zone":None}
        if predictor:
            try:
                hr=predictor.hr_for_duration(t);meta=predictor.metadata(hr)
            except ValueError:pass
        exact=any(math.isclose(t,test["duration_s"],rel_tol=1e-10) for test in tests)
        evidence="MEASURED" if exact else "INTERPOLATED" if window and window[0]<=t<=window[1] else "EXTRAPOLATED" if tests else "ESTIMATED"
        point_meta=tuned.point_metadata(t) if hasattr(tuned,"point_metadata") else {}
        return {"duration_s":t,"speed_kmh":v,"distance_m":t*v/3.6,"estimated_hr_bpm":hr,
                "evidence":evidence,"capped":bool(point_meta.get("extrapolation_capped")),**meta}
    output=None
    prediction_error=None
    supplied=sum(v is not None for v in (duration_s,distance_m,speed_kmh,hr_bpm))
    if supplied>1: raise HTTPException(422,"Choose one prediction input")
    if supplied:
        if tuned is None:
            prediction_error=model_error or "ABSOLUTE_SPEED_EVIDENCE_REQUIRED"
        else:
            try:
                if hr_bpm is not None:
                    if predictor is None:raise ValueError("HR profile required")
                    t=predictor.duration(hr_bpm)
                else:
                    t=duration_s if duration_s is not None else tuned.inverse(distance_m,distance=True) if distance_m is not None else tuned.inverse(speed_kmh/3.6)
                output=prediction(t)
            except ValueError:
                prediction_error="OUTSIDE_PREDICTION_RANGE"
    metadata=speed_duration.model_metadata(tuned) if tuned else {}
    points=[prediction(math.exp(tuned._x[0]+(tuned._x[-1]-tuned._x[0])*i/180)) for i in range(181)] if tuned else []
    # Include the exact real anchors in the chart as well as its plotting grid.
    by_duration={p["duration_s"]:p for p in points}
    if tuned:
        by_duration.update({t["duration_s"]:prediction(t["duration_s"]) for t in tests})
    points=[by_duration[t] for t in sorted(by_duration)]
    limited_tails=[]
    if window:
        if any(p["capped"] and p["duration_s"]<window[0] for p in points):limited_tails.append("SHORT")
        if any(p["capped"] and p["duration_s"]>window[1] for p in points):limited_tails.append("LONG")
    return {"schema_version":"speed-model-v1","model_version":metadata.get("model_version",speed_duration.VERSION),"sport":sport,"sports":sports,
        "test_window":{"start":start.isoformat(),"end":today.isoformat()},
        "index_window":{"start":index_start.isoformat(),"end":today.isoformat(),"days":HISTORY_DAYS,
                        "last_activity_date":max(used_dates) if used_dates else None},
        "activities":[{"activity_ref":a["activity_ref"],"name":a.get("name") or a["sport"],"day":a.get("local_date"),"sport":a["sport"],"elapsed_s":a.get("elapsed_time_s")} for a in activities],
        "status":"CONFLICTING_TESTS" if model_error else "CALIBRATED" if tests else "PRELIMINARY" if tuned else "UNAVAILABLE",
        "tests":entries,"active_test_count":len(tests),"exploratory_test_count":exploratory_count,
        "active_test_keys":[e["entry_key"] for e in entries if e["payload"] in tests],
        "points":points,"curve_metadata":{"mode":metadata.get("calibration_mode","UNAVAILABLE"),
            "absolute_speed_available":tuned is not None,"measured_window_s":window,
            "cap_percent":5 if metadata.get("additional_corridor_fraction")==.05 else None,
            "limited_tails":limited_tails,"reasons":[model_error] if model_error else [],
            "normative_version":speed_duration.NORMATIVE_VERSION},
        "volume_scope":"ALL_SPORTS","volume_weekly_min":volumes,"history_days":volume_evidence["days"],
        "volume_history_basis":volume_evidence["basis"],"total_weekly_minutes":volume_evidence["total_weekly_minutes"],
        "hr_zone_source":getattr(settings,"hr_zone_source","MANUAL"),
        "zone_corrections":dict(zip(volumes,corrections)),"correction_applied_fraction":0.,
        "preliminary_capacity":preliminary_capacity.summary(prior),
        "calibration_diagnostics":preliminary_capacity.calibration_diagnostics(prior,tests) if not model_error else {"model_error":model_error},
        "functional_profile":build_functional_profile(tuned,tests,zone_weekly_min=volumes,exposure_source=volume_evidence["basis"]),
        "critical_speed":speed_duration.critical_speed(tests) if not model_error else {"status":"INCONSISTENT_TESTS","count":len(tests)},
        "prediction":output,"prediction_error":prediction_error,"warnings":list(dict.fromkeys(warnings)),"source_generation_id":calendar.get("generation_id"),
        "source_revision":calendar.get("revision"),"hr_speed_range_kmh":list(predictor.speed_range) if predictor else None,
        "hr_model":predictor.summary() if predictor else None,"index_summary":indices,"index_admission":admission,
        "volume_position_basis":"EXPERT_DURATION"}


def _speed_volume_evidence(source, activities, today):
    """Measured exposure and actual duration stay separate from estimated zones."""
    from .activity_catalog import calendar_item

    def valid(value):
        return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0

    first=max(date.fromisoformat(source.get("period_start",today.isoformat())),today-timedelta(days=40))
    last=min(date.fromisoformat(source.get("period_end",today.isoformat())),today-timedelta(days=1))
    days=max(0,(last-first).days+1)
    zones={z:None for z in speed_duration.VOLUME_RANGES_MIN}
    if not days:
        return {"zone_weekly_q":zones,"total_weekly_minutes":None,"days":0,"basis":"UNAVAILABLE"}
    source_activities=source.get("activities",[])
    measured=[a for a in source_activities if first.isoformat()<=a["date"]<=last.isoformat() and a.get("sport")!="WeightTraining"]
    catalog=[a for a in activities if first.isoformat()<=a.get("local_date","")<=last.isoformat() and a.get("sport")!="WeightTraining"]
    observed={a.get("activity_ref") or f"snapshot-{i}":a for i,a in enumerate(measured)}
    catalog_by_ref={a["activity_ref"]:a for a in catalog}
    extra_refs=set(catalog_by_ref)-set(observed)
    quality=source.get("quality") or {}

    # Aggregate flags can include older activities outside this window. Resolve
    # their identities first; only unexplained flags remain global uncertainty.
    quality_refs={status:set() for status in ("limited","excluded")}
    source_start,source_end=source.get("period_start",""),source.get("period_end",today.isoformat())
    for group,rows,day_key in (("snapshot",source_activities,"date"),("catalog",activities,"local_date")):
        for i,a in enumerate(rows):
            if source_start<=a.get(day_key,"")<=source_end and a.get("quality_status") in quality_refs:
                quality_refs[a["quality_status"]].add(a.get("activity_ref") or f"{group}-{i}")
    unexplained={status:max(0,quality.get(f"{status}_activities",0)-len(refs)) for status,refs in quality_refs.items()}
    incomplete_hr=bool(extra_refs or any(unexplained.values()) or any(
        a.get("quality_status") in {"limited","excluded","provider_missing"}
        or (a.get("hr_coverage_percent") is not None and
            (not valid(a["hr_coverage_percent"]) or a["hr_coverage_percent"]==0))
        for a in [*measured,*catalog]))
    all_zones_complete=not incomplete_hr
    # Every activity needs exactly one valid record per zone. Missing or invalid
    # rows are unknown, not zero; positive partial sums are explicit lower totals.
    for z in zones:
        values=[]
        complete=not incomplete_hr
        for a in measured:
            rows=[r for r in a.get("zones",[]) if r.get("zone")==z]
            if len(rows)==1 and valid(rows[0].get("equivalent_time_min")):
                values.append(rows[0]["equivalent_time_min"])
            else:
                complete=False
        all_zones_complete=all_zones_complete and complete
        if complete or sum(values)>0:zones[z]=7*sum(values)/days
    durations={}
    duration_sources={}
    missing=set()
    for key,a in observed.items():
        value=a.get("duration_min")
        if valid(value):
            durations[key]=value
            duration_sources[key]="CANONICAL_DURATION"
        else:missing.add(key)
    # Preserve canonical time and only fill missing values from the same pinned
    # calendar. Its standard serializer shares moving/recording/elapsed precedence.
    for key,a in catalog_by_ref.items():
        if key in durations:continue
        value=a.get("duration_min")
        duration_source="CATALOG_DURATION"
        if not valid(value):
            candidate={**a}
            for key_name in ("moving_time_s","recording_time_s","elapsed_time_s"):
                if not valid(candidate.get(key_name)):candidate[key_name]=None
            canonical=candidate.get("canonical_summary")
            if isinstance(canonical,dict) and not valid(canonical.get("duration_min")):
                candidate["canonical_summary"]={**canonical,"duration_min":None}
            value=calendar_item(candidate)["duration_min"]
            if isinstance(canonical,dict) and valid(canonical.get("duration_min")):
                duration_source="CATALOG_CANONICAL_DURATION"
            elif candidate.get("moving_time_s"):
                duration_source="CATALOG_MOVING_TIME"
            elif candidate.get("recording_time_s"):
                duration_source="CATALOG_RECORDING_TIME"
            else:duration_source="CATALOG_ELAPSED_TIME"
        if valid(value):
            durations[key]=value
            duration_sources[key]=duration_source
            missing.discard(key)
        else:missing.add(key)
    # Without activity identity, combining two lists could count a workout twice.
    ambiguous_identity=bool(catalog and any(not a.get("activity_ref") for a in measured))
    total=None if missing or unexplained["excluded"] or ambiguous_identity else 7*sum(durations.values())/days
    basis="HR_MEASURED" if all_zones_complete else "HR_PARTIAL" if any(v is not None and v>0 for v in zones.values()) else "TOTAL_DURATION" if total is not None else "UNAVAILABLE"
    warnings=(["ELAPSED_ONLY_DURATION_ESTIMATE"] if "CATALOG_ELAPSED_TIME" in duration_sources.values() else [])
    return {"zone_weekly_q":zones,"total_weekly_minutes":total,"days":days,"basis":basis,
            "duration_sources":duration_sources,"missing_duration_refs":sorted(missing),
            "unexplained_quality_counts":unexplained,"warnings":warnings}
