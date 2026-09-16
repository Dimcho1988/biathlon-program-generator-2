"""Causal, whole-workout admission shared by history and prediction."""
from copy import deepcopy
from datetime import date,timedelta
import numpy as np
from .trainability import MODEL_VERSION,SCHEMA_VERSION

HISTORY_DAYS=40
MIN_REFERENCE_ACTIVITIES=7
MAX_INDEX_DEVIATION=.20


def robust_mean(values,weights):
    x=np.asarray(values,float);w=np.asarray(weights,float)
    if not len(x) or np.any(~np.isfinite(x)) or np.any(x<=0) or np.any(~np.isfinite(w)) or np.any(w<=0): raise ValueError("Invalid index observations")
    mean=float(np.average(x,weights=w))
    if len(x)<MIN_REFERENCE_ACTIVITIES:return mean
    center=float(np.median(x));scale=float(1.4826*np.median(np.abs(x-center)))
    if scale<=1e-12:return center
    for _ in range(100):
        factors=np.minimum(1,1.5*scale/np.maximum(np.abs(x-center),1e-12))
        new=float(np.average(x,weights=w*factors))
        if abs(new-center)<1e-10:return new
        center=new
    return center


def admit_activities(activities):
    rows=deepcopy(sorted(activities,key=lambda r:(r['start_at_utc'],r['activity_ref'])))
    accepted=[]
    for row in rows:
        index=row.get('index')
        if index is None:continue
        if index.get('model_version')!=MODEL_VERSION or index.get('schema_version')!=SCHEMA_VERSION:
            row['index']=None;row['unavailable_reason']='REFRESH_REQUIRED';continue
        first=(date.fromisoformat(row['local_date'])-timedelta(days=HISTORY_DAYS)).isoformat()
        previous=[r for r in accepted if r['sport']==row['sport'] and r['index']['comparison_key']==index['comparison_key']
                  and first<=r['local_date']<=row['local_date'] and r['start_at_utc']<row['start_at_utc']]
        flags=[];checked=[]
        for band in [*index['zones'],index['general']]:
            if not band['valid']:continue
            observations=[b for r in previous for b in [*r['index']['zones'],r['index']['general']]
                          if b['name']==band['name'] and b['valid']]
            if len(observations)<MIN_REFERENCE_ACTIVITIES:continue
            reference=robust_mean([b['index'] for b in observations],[b['hr_seconds'] for b in observations])
            deviation=band['index']/reference-1
            item={'zone':band['name'],'reference_index':reference,'deviation_fraction':deviation,'reference_count':len(observations)}
            checked.append(item)
            if abs(deviation)>MAX_INDEX_DEVIATION+1e-12:flags.append(item)
        index['admission']={'status':'EXCLUDED' if flags or index.get('signal_quality',{}).get('status')=='EXCLUDED' else 'ACCEPTED',
                            'reason':'INDEX_OUTLIER' if flags else index.get('signal_quality',{}).get('reason'),
                            'reference_days':HISTORY_DAYS,'minimum_reference_activities':MIN_REFERENCE_ACTIVITIES,
                            'max_deviation_fraction':MAX_INDEX_DEVIATION,'checked_bands':checked,'flagged_bands':flags,
                            'reference_status':'CHECKED' if checked else 'INSUFFICIENT_HISTORY'}
        if flags:
            for band in [*index['zones'],index['general']]:
                band['candidate_index']=band['index'];band['index']=None;band['valid']=False;band['invalid_reason']='INDEX_OUTLIER'
        if index['admission']['status']=='ACCEPTED':accepted.append(row)
    return rows


def history_from_calendar(repository,alias,calendar):
    raw=calendar.get('activities')
    if not isinstance(raw,list):raise ValueError('Invalid activity calendar')
    keys=tuple(r['latest_shadow_run_key'] for r in raw if r.get('latest_shadow_run_key'))
    summaries=repository.trainability_summaries(alias,keys) if keys else {}
    rows=[]
    for r in raw:
        key=r.get('latest_shadow_run_key');summary=summaries.get(key) if key else None
        if key and (summary is None or summary.get('activity_ref')!=r['activity_ref']):raise ValueError('Pinned trainability activity mismatch')
        index=summary.get('trainability_index') if summary else None
        if index is not None and not isinstance(index,dict):raise ValueError('Invalid trainability index')
        if index and (index.get('model_version')!=MODEL_VERSION or index.get('schema_version')!=SCHEMA_VERSION):index=None
        rows.append({'activity_ref':r['activity_ref'],'name':r.get('name'),'sport':r.get('sport') or 'Unknown',
                     'start_at_utc':r.get('start_at_utc') or r['local_date']+'T00:00:00Z','local_date':r['local_date'],
                     'index':index,'unavailable_reason':None if index else 'REFRESH_REQUIRED' if key else 'NO_SHADOW'})
    return admit_activities(rows)
