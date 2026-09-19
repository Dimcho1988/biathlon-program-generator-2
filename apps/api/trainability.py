"""Time-paired raw-HR/Vflat index with a whole-activity signal screen."""
from __future__ import annotations
from datetime import datetime
import math
from typing import Any, Mapping, Sequence
import numpy as np

SCHEMA_VERSION = "trainability-index-v3"
MODEL_VERSION = "trainability_paired_raw_lag20_v3"
MIN_SECONDS_BY_BAND = {"Z1":420.,"Z2":420.,"Z3":420.,"Z4":420.,"Z5":300.,"GENERAL":420.}
MIN_ACTIVITY_SECONDS = 420.
GENERAL_RANGE = (.75,.92)
MIN_GRADE_PCT = -3.
LAG_SECONDS = 20.
MAX_GAP_SECONDS = 10.


def _number(value: Any) -> float | None:
    if isinstance(value,bool): return None
    try: number=float(value)
    except (TypeError,ValueError): return None
    return number if math.isfinite(number) else None


def _time(row):
    if row.get("timestamp") is not None:
        try: return datetime.fromisoformat(str(row["timestamp"]).replace("Z","+00:00")).timestamp()
        except (ValueError,TypeError): return None
    return _number(row.get("elapsed_s"))


def signal_quality(hr_rows):
    points=sorted((t,h) for r in hr_rows if (t:=_time(r)) is not None
                  and (h:=_number(r.get("hr_raw_bpm"))) is not None and h>0)
    episodes=[]
    for (t0,h0),(t1,h1) in zip(points,points[1:]):
        if 0<t1-t0<=5 and abs(h1-h0)>=15:
            if episodes and t0-episodes[-1][1]<=15: episodes[-1][1]=t1
            else: episodes.append([t0,t1])
    return {"status":"EXCLUDED" if len(episodes)>=3 else "PASSED_SCREEN",
            "reason":"HR_SIGNAL_SUSPECT" if len(episodes)>=3 else None,
            "rapid_change_episodes":len(episodes),"threshold_bpm":15,"window_s":5,
            "minimum_episodes":3,"validated":False}


def compute_trainability(hr_rows: Sequence[Mapping[str,Any]], speed_rows: Sequence[Mapping[str,Any]], *,
    zone_bounds_bpm: Sequence[int],hrmax_bpm:int|None,activity_duration_s:float|None,
    comparison_key:str,source_versions:Mapping[str,str]) -> dict[str,Any]:
    bounds=[float(x) for x in zone_bounds_bpm]
    if len(bounds)!=6 or any(not math.isfinite(x) for x in bounds) or any(a>=b for a,b in zip(bounds,bounds[1:])):
        raise ValueError("Trainability requires five ordered HR zones")
    if hrmax_bpm is not None and (not math.isfinite(hrmax_bpm) or hrmax_bpm<=0): raise ValueError("HRmax must be positive")
    quality=signal_quality(hr_rows)
    # Preserve missing HR timestamps. Never join across missing observations.
    hr_by_time={t:_number(r.get("hr_raw_bpm")) for r in hr_rows if (t:=_time(r)) is not None}
    ordered=sorted(hr_by_time)
    times=np.asarray(ordered,float)
    hrs=np.asarray([hr_by_time[t] if hr_by_time[t] is not None else np.nan for t in ordered],float)
    # HRmax is a normalization setting, not a sensor-validity cutoff.
    good=np.isfinite(hrs)&(hrs>=30)&(hrs<=300)
    pairs=[];downhill=unavailable=0.
    for row in speed_rows:
        dt=_number(row.get("dt_s"));end=_time(row)
        if dt is None or dt<=0: continue
        v=_number(row.get("vflat_b65_kmh"));grade=_number(row.get("grade_raw_pct"))
        if end is None or dt>MAX_GAP_SECONDS or v is None or v<=0 or grade is None or row.get("exclusion_reason"):
            unavailable+=dt;continue
        if grade<MIN_GRADE_PCT: downhill+=dt;continue
        pairs.append((end-dt/2,dt,v))
    h=np.asarray([],float);v=h.copy();weights=h.copy()
    if len(times)>=2 and pairs:
        p=np.asarray(pairs);start=p[:,0];query=start+LAG_SECONDS
        left=np.searchsorted(times,start,side="right")-1
        right=np.searchsorted(times,query,side="left")
        a=np.searchsorted(times,query,side="right")-1;b=a+1
        exact=a>=0
        exact[exact] &= times[a[exact]]==query[exact]
        b=np.where(exact,a,b)
        in_range=(left>=0)&(right<len(times))&(a>=0)&(b<len(times))
        left=np.clip(left,0,len(times)-1);right=np.clip(right,0,len(times)-1)
        a=np.clip(a,0,len(times)-1);b=np.clip(b,0,len(times)-1)
        bad_point=np.r_[0,np.cumsum(~good)]
        bad_gap=np.r_[0,np.cumsum(np.diff(times)>MAX_GAP_SECONDS)]
        valid=in_range&(bad_point[right+1]-bad_point[left]==0)&(bad_gap[right]-bad_gap[left]==0)
        span=times[b]-times[a]
        fraction=np.divide(query-times[a],span,out=np.zeros(len(query)),where=span>0)
        interpolated=hrs[a]+fraction*(hrs[b]-hrs[a])
        valid &= np.isfinite(interpolated)
        unavailable+=float(p[~valid,1].sum())
        h=interpolated[valid];v=p[valid,2];weights=p[valid,1]
    elif pairs: unavailable+=sum(p[1] for p in pairs)
    total=float(weights.sum())
    def band(name,lo,hi,inclusive=False):
        mask=(h>=lo)&((h<=hi) if inclusive else (h<hi)) if lo is not None else np.zeros(len(h),bool)
        seconds=float(weights[mask].sum());hh=float(np.average(h[mask],weights=weights[mask])) if seconds else None
        vv=float(np.average(v[mask],weights=weights[mask])) if seconds else None
        hp=hh/hrmax_bpm*100 if hh is not None and hrmax_bpm else None
        reason=quality["reason"]
        if not reason:
            if activity_duration_s is None: reason="ACTIVITY_DURATION_MISSING"
            elif activity_duration_s<MIN_ACTIVITY_SECONDS: reason="ACTIVITY_BELOW_7MIN"
            elif hrmax_bpm is None: reason="HRMAX_MISSING"
            elif seconds+1e-9<MIN_SECONDS_BY_BAND[name]: reason="PAIRED_TIME_BELOW_MINIMUM"
            elif vv is None or vv<=0: reason="ZERO_SPEED"
        return {"name":name,"lower_bpm":lo,"upper_bpm":hi,"minimum_seconds":MIN_SECONDS_BY_BAND[name],
                "hr_seconds":seconds,"speed_seconds":seconds,"hr_percent":100*seconds/total if total else 0.,
                "mean_hr_bpm":hh,"mean_hrmax_percent":hp,"mean_vflat_kmh":vv,
                "index":hp/vv if not reason else None,"valid":reason is None,"invalid_reason":reason}
    return {"schema_version":SCHEMA_VERSION,"model_version":MODEL_VERSION,"normalization":"percent_hrmax",
            "hr_source":"raw","lag_seconds":LAG_SECONDS,"signal_quality":quality,
            "comparison_key":comparison_key,"source_versions":dict(source_versions),"hrmax_bpm":hrmax_bpm,
            "zone_bounds_bpm":bounds,"activity_duration_s":activity_duration_s,"minimum_activity_seconds":MIN_ACTIVITY_SECONDS,
            "minimum_seconds_by_band":dict(MIN_SECONDS_BY_BAND),"minimum_grade_pct":MIN_GRADE_PCT,
            "general_range_percent":[75,92],"hr_seconds":total,"eligible_speed_seconds":total,
            "downhill_excluded_seconds":downhill,"unavailable_speed_seconds":unavailable,
            "zones":[band(f"Z{i+1}",bounds[i],bounds[i+1],i==4) for i in range(5)],
            "general":band("GENERAL",*(tuple(hrmax_bpm*p for p in GENERAL_RANGE) if hrmax_bpm else (None,None)),True)}
