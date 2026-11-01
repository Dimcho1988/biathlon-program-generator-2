"""Generation changes reuse immutable activity Q; scientific edits invalidate it."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
import pytest

from apps.api import actual_load_projection as model, model_service, component_load_context
from apps.api.speed_load_cache import SpeedLoadCache
from biathlon import speed_equivalence
from tests.test_unified_zone_load import BOUNDS, streams


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(model, '_cache', SpeedLoadCache())
    monkeypatch.setattr(model, '_activity_cache', SpeedLoadCache(max_entries=2))
    settings=SimpleNamespace(zone_bounds_bpm=BOUNDS,hrmax_bpm=200,leading_indicator='HEART_RATE')
    calls=[]
    refs={'run-a':'a','run-b':'b','run-c':'c','corrected-a':'a'}
    def read(alias,keys):
        calls.extend(keys)
        return {key:{'activity_ref':refs[key],'shadow_payload':streams(150.,16.)} for key in keys}
    repo=SimpleNamespace(athlete_settings=lambda _:settings,activity_speed_history_samples=read,
                         speed_load_cache_namespace='isolated-immutable-database')
    calendar=[{'activity_ref':ref,'sport':'Run','local_date':'2026-10-23',
               'latest_shadow_run_key':'run-'+ref,'elapsed_time_s':60.} for ref in ('a','b')]
    inputs={'alias':'athlete','today':date(2026,10,24),'entries':[],
            'calendar':{'generation_id':'first','revision':1,'activities':calendar}}
    source={'period_start':'2026-10-23','period_end':'2026-10-23','activities':[
        {'activity_ref':ref,'sport':'Run','date':'2026-10-23','duration_min':1.,
         'zones':[{'zone':z,'effective_load':0.} for z in speed_equivalence.ZONES]} for ref in ('a','b')]}
    monkeypatch.setattr(model_service,'speed_view',lambda *a,**kw:{'sport':'Run'})
    return repo,inputs,source,settings,calls


def test_new_generation_reuses_activity_results_without_reading_recordings(case):
    repo,inputs,source,_,calls=case
    before=model.reconstruct_history(repo,'athlete',source,speed_inputs=inputs)
    changed=deepcopy(inputs);changed['calendar']['generation_id']='next';changed['calendar']['revision']=2
    after=model.reconstruct_history(repo,'athlete',source,speed_inputs=changed)
    assert calls==['run-a','run-b']
    assert before['activities']==after['activities']
    assert before['load_selection']['fingerprint']!=after['load_selection']['fingerprint']
    assert all('timeseries' not in str(row) for _,row in model._activity_cache._entries.values())


def test_corrected_recording_recomputes_only_its_own_result(case):
    repo,inputs,source,_,calls=case
    model.reconstruct_history(repo,'athlete',source,speed_inputs=inputs)
    changed=deepcopy(inputs);changed['calendar']['activities'][0]['latest_shadow_run_key']='corrected-a'
    reused=model.reconstruct_history(repo,'athlete',source,speed_inputs=changed)
    assert calls==['run-a','run-b','corrected-a']
    model._activity_cache=SpeedLoadCache()
    model._cache=SpeedLoadCache()
    fresh=model.reconstruct_history(repo,'athlete',source,speed_inputs=changed)
    assert fresh==reused


def test_new_recording_keeps_existing_activity_ledgers_and_the_cache_is_bounded(case):
    repo,inputs,source,_,calls=case
    model.reconstruct_history(repo,'athlete',source,speed_inputs=inputs)
    changed=deepcopy(inputs)
    changed['calendar']['revision']=2
    changed['calendar']['activities'].append({
        'activity_ref':'c','sport':'Run','local_date':'2026-10-23',
        'latest_shadow_run_key':'run-c','elapsed_time_s':60.})
    after=model.reconstruct_history(repo,'athlete',source,speed_inputs=changed)
    assert calls==['run-a','run-b','run-c']
    assert len(after['activities'])==3
    assert len(model._activity_cache._entries)==2


def test_new_evaluation_day_reuses_historical_q_and_changes_the_history_identity(case):
    from datetime import timedelta
    repo,inputs,source,_,calls=case
    before=model.reconstruct_history(repo,'athlete',source,speed_inputs=inputs)
    changed=deepcopy(inputs);changed['today']+=timedelta(days=1)
    after=model.reconstruct_history(repo,'athlete',source,speed_inputs=changed)
    assert calls==['run-a','run-b']
    assert before['activities']==after['activities']
    assert before['load_selection']['fingerprint']!=after['load_selection']['fingerprint']


@pytest.mark.parametrize('change',['settings','duration','context','namespace','athlete'])
def test_changed_scientific_dependencies_never_reuse_a_stale_ledger(case,monkeypatch,change):
    repo,inputs,source,settings,calls=case
    model.reconstruct_history(repo,'athlete',source,speed_inputs=inputs)
    inputs=deepcopy(inputs);inputs['calendar']['revision']=2
    alias='athlete'
    if change=='settings': settings.leading_indicator='SPEED'
    if change=='duration':
        for row in inputs['calendar']['activities']:row['elapsed_time_s']=120.
    if change=='context':
        original=component_load_context.context_from_speed_view
        monkeypatch.setattr(component_load_context,'context_from_speed_view',lambda view:{**original(view),'revised_context':True})
    if change=='namespace':repo.speed_load_cache_namespace='other-immutable-database'
    if change=='athlete':alias='other';inputs['alias']=alias
    reused=model.reconstruct_history(repo,alias,source,speed_inputs=inputs)
    assert len(calls)==4
    monkeypatch.setattr(model,'_activity_cache',SpeedLoadCache())
    monkeypatch.setattr(model,'_cache',SpeedLoadCache())
    assert model.reconstruct_history(repo,alias,source,speed_inputs=inputs)==reused
