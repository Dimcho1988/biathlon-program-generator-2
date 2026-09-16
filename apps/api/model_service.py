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

from biathlon import recovery_v2, speed_duration
from .trainability_history import history_from_calendar, robust_mean
from .model_schemas import RecoveryConfigInput, RecoveryHistoryV2, initial_settings
from .oauth_store import PersistentStoreFailure
from .speed_segments import segment_measurement


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
    if not day or not (today-timedelta(days=90)).isoformat()<=day<=today.isoformat():
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
        and e["payload"].get("enabled") and e["payload"].get("sport")==payload["sport"]
        and (e["payload"].get("vflat_version"),e["payload"].get("vflat_config_version"))==(payload["vflat_version"],payload["vflat_config_version"])
        and e["payload"].get("day","") >= (today-timedelta(days=90)).isoformat()]
    if body.enabled:
        try: speed_duration.calibrated(comparable+[payload])
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    return ModelStore(repository).save(alias,"SPEED_TEST",key,payload,body.expected_revision,actor)


def speed_view(repository,alias,sport=None,*,duration_s=None,distance_m=None,speed_kmh=None,hr_bpm=None):
    from biathlon import hr_speed
    settings=repository.athlete_settings(alias)
    if settings is None: raise HTTPException(409,"Athlete settings are required")
    today=datetime.now(timezone.utc).astimezone(ZoneInfo(settings.timezone)).date()
    start=today-timedelta(days=90)
    calendar=repository.active_activity_calendar(alias,date.min,today) or {"activities":[]}
    activities=[a for a in (calendar.get("activities") or []) if a["local_date"]>=start.isoformat()]
    entries=[e for e in ModelStore(repository).entries(alias) if e["kind"]=="SPEED_TEST"]
    sports=sorted({r["sport"] for r in activities}|{e["payload"]["sport"] for e in entries})
    sport=sport or (sports[0] if sports else "Run")
    tests=[e["payload"] for e in entries if e["payload"].get("enabled") and e["payload"]["sport"]==sport
           and start.isoformat()<=e["payload"]["day"]<=today.isoformat()]
    versions={t.get("vflat_version") for t in tests}
    warnings=["EXPERT_REFERENCE_NOT_POPULATION_VALIDATED", "HEART_RATE_ESTIMATE_FROM_PAIRED_INDEX"]
    if len({(t.get("vflat_version"),t.get("vflat_config_version")) for t in tests})>1:
        tests=[]; warnings.append("INCOMPARABLE_MODEL_VERSIONS")
    try: curve=speed_duration.calibrated(tests)
    except ValueError:
        curve=speed_duration.calibrated([]); tests=[]; warnings.append("CONFLICTING_TESTS")
    exploratory_count=sum(t.get("test_mode")=="EXPLORATORY" for t in tests)
    if exploratory_count:
        warnings.append("EXPLORATORY_CALIBRATION")
    history=history_from_calendar(repository,alias,calendar)
    recent=[a for a in history if a["sport"]==sport and a["local_date"] >= (today-timedelta(days=40)).isoformat()]
    indices={}
    for zone in [*speed_duration.VOLUME_RANGES_MIN,"GENERAL"]:
        values=[];weights=[]
        for activity in recent:
            index=activity.get("index")
            if not index or index.get("hrmax_bpm")!=settings.hrmax_bpm or list(index.get("zone_bounds_bpm",[]))!=list(settings.zone_bounds_bpm):continue
            if versions and index.get("source_versions",{}).get("vflat") not in versions:continue
            for band in [*index["zones"],index["general"]]:
                if band["name"]==zone and band["valid"]:
                    values.append(band["index"]);weights.append(band["hr_seconds"])
        indices[zone]={"index":robust_mean(values,weights) if values else None,"count":len(values),"seconds":sum(weights)}
    admission={"activities":len(recent),"excluded":sum(a.get("index",{}).get("admission",{}).get("status")=="EXCLUDED" for a in recent if a.get("index")),
               "refresh_required":sum(a["unavailable_reason"]=="REFRESH_REQUIRED" for a in recent)}
    source=(calendar.get("snapshot_payload") or {}).get("load_history") or {}
    first=max(date.fromisoformat(source.get("period_start",today.isoformat())),today-timedelta(days=40))
    last=min(date.fromisoformat(source.get("period_end",today.isoformat())),today-timedelta(days=1))
    n=max(0,(last-first).days+1)
    volumes={z:None for z in speed_duration.VOLUME_RANGES_MIN}
    if n:
        # Volume describes the athlete's total zone exposure across sports.
        # Tests and HR-speed summaries above remain specific to the chosen sport.
        volumes={z:7*sum(r["equivalent_time_min"] for a in source.get("activities",[])
            if first.isoformat()<=a["date"]<=last.isoformat() for r in a["zones"] if r["zone"]==z)/n for z in volumes}
    corrections=[speed_duration.volume_correction(z,volumes[z]) for z in volumes]
    centers=hr_speed.volume_duration_centers(settings.zone_bounds_bpm)
    tuned,factor=speed_duration.adjusted(curve,tests,None,[],corrections,duration_centers=centers)
    if factor<1 and tests and any(corrections): warnings.append("CORRECTION_REDUCED_FOR_MONOTONICITY")
    predictor=hr_speed.Predictor(tuned,settings.zone_bounds_bpm,settings.hrmax_bpm,indices) if tests and settings.hrmax_bpm else None
    def prediction(t):
        v=tuned.speed(t)*3.6
        hr=None;meta={"hr_prediction_source":None,"hr_prediction_reason":None,"zone":None}
        if predictor:
            try:
                hr=predictor.hr_for_duration(t);meta=predictor.metadata(hr)
            except ValueError:pass
        return {"duration_s":t,"speed_kmh":v,"distance_m":t*v/3.6,"estimated_hr_bpm":hr,**meta}
    output=None
    prediction_error=None
    supplied=sum(v is not None for v in (duration_s,distance_m,speed_kmh,hr_bpm))
    if supplied>1: raise HTTPException(422,"Choose one prediction input")
    if supplied:
        if not tests:
            prediction_error="MAXIMAL_TEST_REQUIRED"
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
    return {"schema_version":"speed-model-v1","model_version":speed_duration.VERSION,"sport":sport,"sports":sports,
        "activities":[{"activity_ref":a["activity_ref"],"name":a.get("name") or a["sport"],"day":a.get("local_date"),"sport":a["sport"],"elapsed_s":a.get("elapsed_time_s")} for a in activities],
        "status":"CALIBRATED" if tests else "REFERENCE_ONLY","tests":entries,"active_test_count":len(tests),
        "exploratory_test_count":exploratory_count,
        "active_test_keys":[e["entry_key"] for e in entries if e["payload"] in tests],
        "points":[prediction(math.exp(tuned._x[0]+(tuned._x[-1]-tuned._x[0])*i/180)) for i in range(181)],
        "volume_scope":"ALL_SPORTS","volume_weekly_min":volumes,"history_days":n,"zone_corrections":dict(zip(volumes,corrections)),
        "correction_applied_fraction":factor,"critical_speed":speed_duration.critical_speed(tests),
        "prediction":output,"prediction_error":prediction_error,"warnings":warnings,"source_generation_id":calendar.get("generation_id"),
        "source_revision":calendar.get("revision"),"hr_speed_range_kmh":list(predictor.speed_range) if predictor else None,
        "hr_model":predictor.summary() if predictor else None,"index_summary":indices,"index_admission":admission,
        "volume_position_basis":"EXPERT_DURATION"}
