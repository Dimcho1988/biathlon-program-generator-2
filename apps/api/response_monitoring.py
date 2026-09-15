"""Versioned, observation-only pilot. No load, recovery or plan mutations."""
from datetime import date, datetime, timedelta
from math import isfinite, log
from statistics import median
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION = "response-monitoring-v1"
FIELDS = ("sleep_quality", "fatigue", "soreness", "stress", "motivation")
WEIGHTS = {"subjective": .5, "rpe": .3, "physiology": .2}
BASELINE_DAYS, MIN_BASELINE, MIN_COMPARABLE = 28, 14, 3


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(default=0, ge=0, strict=True)


class DailyReport(InputModel):
    day: date
    observed_at: datetime | None = None
    sleep_quality: int = Field(ge=1, le=5, strict=True)
    fatigue: int = Field(ge=1, le=5, strict=True)
    soreness: int = Field(ge=1, le=5, strict=True)
    stress: int = Field(ge=1, le=5, strict=True)
    motivation: int = Field(ge=1, le=5, strict=True)
    pain_or_illness: bool = False
    note: str = Field(default="", max_length=500)


class SessionReport(InputModel):
    activity_ref: str = Field(pattern=r"^(?:act_|shadow-)[a-f0-9]{32}$")
    rpe: int = Field(ge=0, le=10, strict=True)
    timing: Literal["IMMEDIATE", "DELAYED", "NEXT_DAY"]
    duration_minutes: float = Field(gt=0, le=1440, allow_inf_nan=False)
    note: str = Field(default="", max_length=500)


class ResponseBlock(InputModel):
    start: date
    load_end: date
    recovery_end: date
    phase: Literal["BUILD", "MAINTAIN", "RECOVERY", "TAPER"]
    note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def dates(self):
        if not self.start <= self.load_end < self.recovery_end or (self.recovery_end-self.start).days > 56:
            raise ValueError("Block dates must be ordered with recovery within 56 days")
        return self


class OptionalTest(InputModel):
    day: date
    protocol: str = Field(min_length=3, max_length=100)
    protocol_version: str = Field(min_length=1, max_length=32)
    value: float = Field(gt=0, allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=24)
    direction: Literal["HIGHER", "LOWER"]
    conditions: str = Field(min_length=3, max_length=500)
    comparable: bool = False


def number(v):
    return float(v) if isinstance(v,(int,float)) and not isinstance(v,bool) and isfinite(v) else None


def latest_entries(entries):
    result = {}
    for e in entries:
        key = (e["kind"],e["entry_key"])
        if key not in result or e["revision"] > result[key]["revision"]:
            result[key] = dict(e)
    return result


def reference(values, floor):
    values = [v for v in values if v is not None]
    if len(values) < MIN_BASELINE:
        return None
    center = median(values)
    return {"median":center,"spread":max(floor,1.4826*median(abs(v-center) for v in values)),"count":len(values)}


def relative_score(value, base, direction=1):
    if value is None or base is None:
        return None
    return round(max(0,min(100,50+15*direction*(value-base["median"])/base["spread"])),3)


def subjective_score(report):
    if not report or not all(number(report.get(f)) is not None and 1<=report[f]<=5 for f in FIELDS):
        return None
    return round(sum((report[f]-1)*25 for f in FIELDS)/5,3)


def device_value(metrics, field):
    value = number(metrics.get(field,{}).get("value"))
    return value if value is not None and value > 0 else None


def baselines(daily, devices, anchor):
    prior = [(anchor-timedelta(days=n)).isoformat() for n in range(1,BASELINE_DAYS+1)]
    metric = lambda d,f:device_value(devices.get(d,{}),f)
    return {"subjective":reference([subjective_score(daily.get(p)) for p in prior],10),
        "resting_hr":reference([metric(p,"resting_hr") for p in prior],3),
        "hrv":reference([log(v) if (v:=metric(p,"hrv")) and v>0 else None for p in prior],.12)}


def zone_vector(activity):
    zones = (activity.get("canonical_summary") or {}).get("zones") or []
    if not isinstance(zones,list):
        return None
    by_zone = {z.get("zone"):number(z.get("equivalent_time_s")) for z in zones}
    values = [by_zone.get(f"Z{i}") for i in range(1,6)]
    return [v/sum(values) for v in values] if len(values)==5 and all(v is not None and v>=0 for v in values) and sum(values)>0 else None


def block_summary(block, daily, as_of):
    """Entire block, independent of chart zoom. No claim about unobserved days."""
    first, last = date.fromisoformat(block["start"]), min(as_of,date.fromisoformat(block["recovery_end"]))
    base = block.get("baseline",{}).get("subjective")
    tracked = []
    for offset in range(max(0,(last-first).days+1)):
        key = (first+timedelta(days=offset)).isoformat()
        report = daily.get(key)
        score = subjective_score(report)
        deviation = (score-base["median"])/base["spread"] if score is not None and base else None
        tracked.append({"day":key,"deviation":deviation,"flag":bool(report and report.get("pain_or_illness"))})
    observed = [d for d in tracked if d["deviation"] is not None]
    returned, consecutive = None, 0
    for d in tracked:
        if d["day"]>block["load_end"]:
            usual = d["deviation"] is not None and d["deviation"]<=1 and not d["flag"]
            consecutive = consecutive+1 if usual else 0
            if not usual:
                returned = None
            elif consecutive==2:
                returned = d["day"]
    final = tracked[-1] if tracked else None
    due = as_of.isoformat()>=block["recovery_end"]
    review = final and (final["flag"] or (final["deviation"] is not None and final["deviation"]>1))
    status = "REVIEW" if due and review else "OBSERVED_RETURN" if returned else "IN_PROGRESS" if not due else "INSUFFICIENT_DATA"
    return {"assessment_basis":"SUBJECTIVE","peak_deviation":max((d["deviation"] for d in observed),default=None),
        "elevated_days":sum(d["deviation"]>1 for d in observed),"observed_days":len(observed),"tracked_days":len(tracked),
        "returned_on":returned,"status":status,"as_of":last.isoformat() if tracked else None}


def session_rows(activities, entries):
    rows = []
    for a in activities:
        e = entries.get(("SESSION",a["activity_ref"]))
        m = e["payload"] if e else None
        imported = number(a.get("provider_rpe"))
        if imported is not None and not 0<=imported<=10:
            imported = None
        rpe = m["rpe"] if m else imported
        duration = m["duration_minutes"] if m else None
        rows.append({"activity_ref":a["activity_ref"],"day":a["local_date"],"name":a.get("name") or a.get("sport"),"sport":a.get("sport"),
            "rpe":rpe,"duration_minutes":duration,"suggested_duration_minutes":(a.get("elapsed_time_s") or 0)/60 or None,
            "timing":m["timing"] if m else "UNKNOWN","source":"ONFLOWS" if m else "INTERVALS" if rpe is not None else None,
            "provider_rpe":imported,"revision":e["revision"] if e else 0,"note":m.get("note","") if m else "",
            "srpe_load":round(rpe*duration,3) if rpe is not None and duration else None,
            "expected_rpe":None,"deviation_score":None,"comparable_count":0,"zone_vector":zone_vector(a)})
    rows.sort(key=lambda r:(r["day"],r["activity_ref"]))
    for r in rows:
        if r["rpe"] is None or not r["duration_minutes"] or r["zone_vector"] is None:
            continue
        start = (date.fromisoformat(r["day"])-timedelta(days=60)).isoformat()
        peers = [p for p in rows if start<=p["day"]<r["day"] and p["sport"]==r["sport"] and p["rpe"] is not None
            and p["timing"]==r["timing"] and p["duration_minutes"] and .8<=p["duration_minutes"]/r["duration_minutes"]<=1.2
            and p["zone_vector"] is not None and sum(abs(a-b) for a,b in zip(p["zone_vector"],r["zone_vector"]))<=.3]
        r["comparable_count"] = len(peers)
        if len(peers)>=MIN_COMPARABLE:
            r["expected_rpe"] = median(p["rpe"] for p in peers)
            r["deviation_score"] = round(max(0,min(100,50+15*(r["rpe"]-r["expected_rpe"]))),3)
    return rows


def build_history(*,entries,wellness,activities,start,end,today):
    selected = latest_entries(entries)
    daily = {key:e["payload"] for (kind,key),e in selected.items() if kind=="DAILY"}
    devices = {r["date"]:r.get("metrics",{}) for r in wellness}
    blocks = sorted([e for (kind,_),e in selected.items() if kind=="BLOCK"],key=lambda e:e["entry_key"])
    sessions = session_rows(activities,selected)
    days = []
    for offset in range((end-start).days+1):
        day = start+timedelta(days=offset)
        key, previous = day.isoformat(), (day-timedelta(days=1)).isoformat()
        manual, metrics = daily.get(key), devices.get(key,{})
        block = next((b for b in blocks if b["payload"]["start"]<=key<=b["payload"]["recovery_end"]),None)
        anchor = date.fromisoformat(block["payload"].get("baseline_frozen_on",block["payload"]["start"])) if block else day
        bases = block["payload"].get("baseline",{}) if block else baselines(daily,devices,anchor)
        subject_base = bases.get("subjective")
        raw_rhr, raw_hrv = (device_value(metrics,f) for f in ("resting_hr","hrv"))
        rhr_score = relative_score(raw_rhr,bases.get("resting_hr"))
        hrv_score = relative_score(log(raw_hrv) if raw_hrv and raw_hrv>0 else None,bases.get("hrv"),-1)
        # High HRV is an atypical response too, not evidence for increasing load.
        if hrv_score is not None:
            hrv_score = 50+abs(hrv_score-50)
        phys = round((rhr_score+hrv_score)/2,3) if rhr_score is not None and hrv_score is not None else None
        previous_sessions = [s for s in sessions if s["day"]==previous]
        rpe = round(sum(s["deviation_score"]*s["duration_minutes"] for s in previous_sessions)/sum(s["duration_minutes"] for s in previous_sessions),3) if previous_sessions and all(s["deviation_score"] is not None for s in previous_sessions) else None
        subject = subjective_score(manual)
        scores = {"subjective":subject,"rpe":rpe,"physiology":phys}
        available = sum(WEIGHTS[k] for k,v in scores.items() if v is not None)
        total = round(sum(scores[k]*WEIGHTS[k] for k in WEIGHTS),3) if all(v is not None for v in scores.values()) else None
        deviation = (subject-subject_base["median"])/subject_base["spread"] if subject is not None and subject_base else None
        state = "INSUFFICIENT_DATA" if deviation is None else "WITHIN_USUAL"
        if deviation is not None and deviation>1:
            state = "EXPECTED_ELEVATION" if block and block["payload"]["phase"]=="BUILD" and key<=block["payload"]["load_end"] else "ELEVATED"
        if block and key>=block["payload"]["recovery_end"] and deviation is not None and deviation>1:
            state = "REVIEW_AFTER_RECOVERY"
        if manual and manual.get("pain_or_illness"):
            state = "REVIEW"
        days.append({"day":key,"total":total,"coverage":round(available*100),
            "groups":[{"key":k,"score":v,"weight":WEIGHTS[k],"contribution":round(v*WEIGHTS[k],3) if v is not None else None} for k,v in scores.items()],
            "state":state,"assessment_basis":"SUBJECTIVE","phase":("RECOVERY" if key>block["payload"]["load_end"] else block["payload"]["phase"]) if block else "UNSPECIFIED",
            "baseline":subject_base,"deviation":deviation,"baseline_anchor":anchor.isoformat(),
            "daily_report":manual,"daily_revision":selected.get(("DAILY",key),{}).get("revision",0),"device_metrics":metrics,
            "physiology":{"resting_hr":{"raw":raw_rhr,"score":rhr_score,"baseline":bases.get("resting_hr")},"hrv":{"raw":raw_hrv,"score":hrv_score,"baseline":bases.get("hrv")}},
            "rpe_sessions":previous_sessions,"block_key":block["entry_key"] if block else None,"automatic_action":"NONE","data_age_days":(today-day).days})
    for e in blocks:
        e["summary"] = block_summary(e["payload"],daily,min(today,end))
    return {"schema_version":VERSION,"today":today.isoformat(),"period_start":start.isoformat(),"period_end":end.isoformat(),"mode":"OBSERVATION_ONLY","automatic_increase":False,"changes_recovery":False,"weights":WEIGHTS,
        "settings":{"baseline_days":BASELINE_DAYS,"minimum_baseline_days":MIN_BASELINE,"minimum_comparable_sessions":MIN_COMPARABLE,"elevation_threshold":1,"return_confirmations":2,"validated":False},
        "days":days,"sessions":[s for s in sessions if start.isoformat()<=s["day"]<=end.isoformat()],"blocks":blocks,"tests":[e for (kind,_),e in selected.items() if kind=="TEST"]}
