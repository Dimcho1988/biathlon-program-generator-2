from copy import deepcopy
from datetime import date,timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.api import actual_load_projection as projection,model_service
from apps.api.oauth_store import PersistentStoreFailure
from apps.api.speed_load_cache import SpeedLoadCache
from biathlon import speed_equivalence
from tests.test_unified_zone_load import BOUNDS,profile,streams


@pytest.fixture
def fixture(monkeypatch):
    settings=SimpleNamespace(zone_bounds_bpm=BOUNDS,hrmax_bpm=200,leading_indicator='SPEED')
    inputs={'alias':'durable-test','today':date(2026,10,24),'calendar':{'generation_id':'generation-1','revision':1,
        'activities':[{'activity_ref':'one','sport':'Run','local_date':'2026-10-23','latest_shadow_run_key':'immutable-run','elapsed_time_s':60.}]},
        'entries':[]}
    source={'period_start':'2026-10-23','period_end':'2026-10-23','activities':[{'activity_ref':'one','sport':'Run',
        'date':'2026-10-23','duration_min':1.,'zones':[{'zone':z,'effective_load':0.} for z in speed_equivalence.ZONES]}]}
    durable={}
    reader=Mock(side_effect=lambda a,k:{'immutable-run':{'activity_ref':'one','shadow_payload':streams(None,16.)}})
    def store(alias,key,value):durable[alias]=(key,json.loads(json.dumps(value)));return {'stored':True}
    repo=SimpleNamespace(athlete_settings=lambda _:settings,activity_speed_history_samples=reader,
        speed_load_cache_namespace='isolated-fixture',write_load_projection=Mock(side_effect=store),
        read_load_projection=Mock(side_effect=lambda a,k:deepcopy(durable[a][1]) if a in durable and durable[a][0]==k else None))
    monkeypatch.setattr(model_service,'speed_view',lambda *a,**k:{'sport':'Run','speed_zones':profile(),'status':'UNAVAILABLE'})
    def reset():
        monkeypatch.setattr(projection,'_cache',SpeedLoadCache())
        monkeypatch.setattr(projection,'_activity_cache',SpeedLoadCache())
    reset()
    return SimpleNamespace(repo=repo,inputs=inputs,source=source,settings=settings,reader=reader,durable=durable,reset=reset)


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def test_restart_reads_the_identical_full_history_without_decoding_any_recording(fixture):
    f=fixture;saved=deepcopy(f.source)
    first=projection.reconstruct_history(f.repo,'durable-test',f.source,speed_inputs=f.inputs)
    assert f.reader.call_count==1 and f.repo.write_load_projection.call_count==1
    f.reset()
    second=projection.reconstruct_history(f.repo,'durable-test',f.source,speed_inputs=f.inputs)
    assert canonical(second)==canonical(first) and f.source==saved
    assert f.reader.call_count==1 and f.repo.write_load_projection.call_count==1
    second['activities'][0]['zones'][0]['equivalent_time_min']=-1
    f.reset()
    assert canonical(projection.reconstruct_history(f.repo,'durable-test',f.source,speed_inputs=f.inputs))==canonical(first)


@pytest.mark.parametrize('change',['day','run','generation','test','bounds','leading','profile','alias'])
def test_changed_inputs_cannot_reuse_a_durable_result(fixture,change):
    f=fixture
    before=projection.reconstruct_history(f.repo,'durable-test',f.source,speed_inputs=f.inputs)
    if change=='day':f.inputs['today']+=timedelta(days=1)
    if change=='run':
        f.inputs['calendar']['activities'][0]['latest_shadow_run_key']='new-run'
        f.reader.side_effect=lambda a,k:{'new-run':{'activity_ref':'one','shadow_payload':streams(None,17.)}}
    if change=='generation':f.inputs['calendar'].update(generation_id='generation-2',revision=2)
    if change=='test':f.inputs['entries']=[{'kind':'SPEED_TEST','entry_key':'new-test','revision':1,'payload':{}}]
    if change=='bounds':f.settings.hrmax_bpm=201
    if change=='leading':f.settings.leading_indicator='HEART_RATE'
    if change=='profile':f.inputs['initial_profile']={'revision':2,'training_experience_years':6}
    f.reset()
    after=projection.reconstruct_history(f.repo,'other-athlete' if change=='alias' else 'durable-test',f.source,speed_inputs=f.inputs)
    assert f.reader.call_count==2
    assert after['load_selection']['fingerprint']!=before['load_selection']['fingerprint']


@pytest.mark.parametrize('operation',['read','write'])
def test_optional_cache_transport_failure_never_discards_valid_computation(fixture,operation):
    f=fixture
    getattr(f.repo,operation+'_load_projection').side_effect=PersistentStoreFailure('unavailable')
    result=projection.reconstruct_history(f.repo,'durable-test',f.source,speed_inputs=f.inputs)
    assert f.reader.call_count==1 and result['activities'][0]['load_source']['recorded_seconds']==60.


def test_unversioned_file_repository_never_uses_shared_cache(fixture):
    f=fixture;f.repo.speed_load_cache_namespace=None
    projection.reconstruct_history(f.repo,'durable-test',f.source,speed_inputs=f.inputs)
    f.repo.read_load_projection.assert_not_called();f.repo.write_load_projection.assert_not_called()
