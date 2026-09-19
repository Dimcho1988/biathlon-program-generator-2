import base64
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api import main
from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from apps.api.trainability import compute_trainability


KEY = "a" * 64
REF = "shadow-" + "b" * 32
HEADERS = {"Authorization": "Bearer service-secret", "X-OnFlows-Athlete-Alias": "ath-test"}
PERIOD = "?period_start=2026-08-01&period_end=2026-08-31"


class PinnedRepository:
    def active_activity_calendar(self, alias, start, end):
        assert alias == "ath-test"
        return {"generation_id": "pinned-generation", "revision": 3, "activities": [
            {"activity_ref": REF, "name": "Training", "sport": "RollerSki",
             "start_at_utc": "2026-08-10T10:00:00Z", "local_date": "2026-08-10",
             "latest_shadow_run_key": KEY},
        ]}

    def trainability_summaries(self, alias, keys):
        assert alias == "ath-test" and keys == (KEY,)
        return {KEY: {"activity_ref": REF, "trainability_index": None}}


def test_history_reads_pinned_keys_and_marks_old_results_for_refresh(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(main, "_repository", lambda: PinnedRepository())
    with TestClient(main.app) as client:
        assert client.get("/api/v2/real/trainability" + PERIOD).status_code == 401
        response = client.get("/api/v2/real/trainability" + PERIOD, headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["generation_id"] == "pinned-generation"
        assert body["activities"][0]["unavailable_reason"] == "REFRESH_REQUIRED"
        assert client.get("/api/v2/real/trainability?period_start=2026-01-01&period_end=2026-08-31", headers=HEADERS).status_code == 422


def test_history_fails_closed_if_pinned_summary_belongs_to_other_activity(monkeypatch):
    class Mismatched(PinnedRepository):
        def trainability_summaries(self, alias, keys):
            return {KEY: {"activity_ref": "another-activity", "trainability_index": {}}}
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(main, "_repository", lambda: Mismatched())
    with TestClient(main.app) as client:
        response = client.get("/api/v2/real/trainability" + PERIOD, headers=HEADERS)
    assert response.status_code == 503
    assert "another-activity" not in response.text


@pytest.mark.parametrize("legacy", [False, True])
def test_history_serves_only_the_current_normalized_model(monkeypatch, legacy):
    index = compute_trainability(
        [{"elapsed_s":t,"hr_raw_bpm":154} for t in range(442)],
        [{"elapsed_s":t,"vflat_b65_kmh":20,"dt_s":1,"grade_raw_pct":0} for t in range(1,421)],
        zone_bounds_bpm=[50,137,147,158,170,178], hrmax_bpm=178,
        activity_duration_s=420, comparison_key="current", source_versions={},
    )
    if legacy:
        index = {"schema_version": "trainability-index-v1", "model_version": "trainability_rank_v1", "general": {"index": 7.7}}

    class WithIndex(PinnedRepository):
        def trainability_summaries(self, alias, keys):
            assert keys == (KEY,)
            return {KEY: {"activity_ref": REF, "trainability_index": index}}

    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(main, "_repository", lambda: WithIndex())
    with TestClient(main.app) as client:
        response = client.get("/api/v2/real/trainability" + PERIOD, headers=HEADERS)
    assert response.status_code == 200
    row = response.json()["activities"][0]
    assert row["unavailable_reason"] == ("REFRESH_REQUIRED" if legacy else None)
    if legacy:
        assert row["index"] is None
    else:
        assert row["index"].pop("admission")["status"] == "ACCEPTED"
        assert row["index"] == index
        assert index["general"]["valid"]


def test_summary_store_batches_exact_keys_and_only_projects_small_index():
    keys = tuple(f"{i:064x}" for i in range(51))
    calls = []

    class Client:
        def request(self, method, url, **kwargs):
            query = parse_qs(urlparse(url).query)
            calls.append(query)
            assert method == "GET"
            assert query["athlete_alias"] == ["eq.ath-test"]
            assert query["select"] == ["run_key,activity_ref,trainability_index:result_payload->trainability_index"]
            selected = query["run_key"][0][4:-1].split(",")
            assert len(selected) <= 50
            return httpx.Response(200, json=[{"run_key": key, "activity_ref": REF, "trainability_index": None} for key in selected])

    repository = SupabasePilotRepository(
        supabase_url="https://project.supabase.co", secret_key="sb_secret_server-key",
        encryption_key=base64.urlsafe_b64encode(bytes(range(32))).decode(), client=Client(),
    )
    assert set(repository.trainability_summaries("ath-test", keys)) == set(keys)
    assert len(calls) == 2
    with pytest.raises(PersistentStoreFailure):
        repository.trainability_summaries("ath-test", ("invalid-query-key",))


def history_row(day, value=5, sport='Walk', key='same'):
    from apps.api.trainability import MODEL_VERSION,SCHEMA_VERSION
    band=lambda name:{'name':name,'valid':True,'index':value,'hr_seconds':600,'invalid_reason':None}
    return {'activity_ref':str(day)+sport,'local_date':f'2026-08-{day:02d}',
            'start_at_utc':f'2026-08-{day:02d}T10:00:00Z','sport':sport,
            'index':{'model_version':MODEL_VERSION,'schema_version':SCHEMA_VERSION,'comparison_key':key,
                     'signal_quality':{'status':'PASSED_SCREEN','reason':None},
                     'zones':[band(f'Z{i}') for i in range(1,6)],'general':band('GENERAL')}}


def test_causal_screen_uses_prior_same_sport_and_rejects_whole_activity_without_mutation():
    from copy import deepcopy
    from apps.api.trainability_history import admit_activities
    rows=[history_row(i) for i in range(1,8)]+[history_row(8,6.01),history_row(9,5),history_row(10,20,'NordicSki')]
    before=deepcopy(rows)
    out=admit_activities(rows)
    assert rows==before
    assert out[6]['index']['admission']['reference_status']=='INSUFFICIENT_HISTORY'
    assert out[7]['index']['admission']['reason']=='INDEX_OUTLIER'
    assert all(b['index'] is None for b in [*out[7]['index']['zones'],out[7]['index']['general']])
    assert out[8]['index']['admission']['checked_bands'][0]['reference_count']==7
    assert out[9]['index']['admission']['reference_status']=='INSUFFICIENT_HISTORY'
    # Adding a future extreme workout cannot change earlier decisions.
    assert admit_activities(rows+[history_row(11,100)])[:len(rows)]==out


def test_exact_twenty_percent_is_allowed_and_one_bad_zone_excludes_all():
    from apps.api.trainability_history import admit_activities
    base=[history_row(i) for i in range(1,8)]
    assert admit_activities(base+[history_row(8,6)])[-1]['index']['admission']['status']=='ACCEPTED'
    last=history_row(8);last['index']['zones'][0]['index']=3.99
    result=admit_activities(base+[last])[-1]['index']
    assert [b['zone'] for b in result['admission']['flagged_bands']]==['Z1']
    assert not result['general']['valid']


def test_reference_excludes_signal_failures_other_settings_and_old_days():
    from apps.api.trainability_history import admit_activities
    rows=[history_row(i) for i in range(1,8)]
    rows[0]['index']['signal_quality']={'status':'EXCLUDED','reason':'HR_SIGNAL_SUSPECT'}
    rows[1]['index']['comparison_key']='different-settings'
    rows[2]['local_date']='2026-06-01';rows[2]['start_at_utc']='2026-06-01T10:00:00Z'
    result=admit_activities(rows+[history_row(8,10)])[-1]
    assert result['index']['admission']['reference_status']=='INSUFFICIENT_HISTORY'


def test_robust_aggregation_limits_extreme_values():
    from apps.api.trainability_history import robust_mean
    assert robust_mean([5,5.1,4.9,5.05,4.95,5.02,40],[600]*7)<5.2
    assert robust_mean([5]*7+[40],[600]*8)==5
    assert robust_mean([4,6],[1,3])==5.5


def test_lightweight_calendar_pins_generation_and_avoids_large_hrmod_payloads():
    from datetime import date
    class Catalog(SupabasePilotRepository):
        def __init__(self):self.calls=[];self.captures=0
        def active_analysis(self, alias):
            self.captures+=1
            return {'generation_id':'captured-generation','revision':41,'snapshot_payload':{}}
        def active_activity_calendar(self,*args):raise AssertionError('Heavy calendar must not be used')
        def _json(self,value):return value
        def _request(self,method,path,**kwargs):
            self.calls.append(path)
            assert method=='GET' and 'athlete_alias=eq.ath-test' in path
            if path.startswith('/onflows_analysis_generations?'):
                assert 'generation_id=eq.captured-generation' in path
                return [{'activity_set_generation_id':'pinned-activity-set','activity_count':201}]
            assert 'generation_id=eq.pinned-activity-set' in path and 'result_payload' not in path
            offset=int(path.split('offset=')[1])
            return [{'activity_ref':f'act_{i:032x}','catalog_payload':{'sport':'Walk','name':'synthetic'},
                'shadow_run_key':f'{i:064x}','canonical_run_key':None,'input_key':None,
                'start_at_utc':'2026-08-10T10:00:00Z','local_date':'2026-08-10'}
                for i in range(offset,min(201,offset+200))]
    repo=Catalog()
    result=repo.active_trainability_calendar('ath-test',date(2026,8,1),date(2026,8,31))
    assert repo.captures==1 and len(repo.calls)==3
    assert result['generation_id']=='captured-generation' and result['revision']==41
    assert len(result['activities'])==201
    assert result['activities'][-1]['latest_shadow_run_key']==f'{200:064x}'
    assert not repo.active_trainability_calendar('ath-test',date(2026,9,1),date(2026,9,30))['activities']


def test_lightweight_calendar_fails_closed_if_a_pinned_page_is_pruned():
    from datetime import date
    class Pruned(SupabasePilotRepository):
        def __init__(self):pass
        def active_analysis(self,alias):return {'generation_id':'captured','revision':41,'snapshot_payload':{}}
        def _json(self,value):return value
        def _request(self,method,path,**kwargs):
            return [{'activity_set_generation_id':'captured','activity_count':1}] if 'analysis_generations?' in path else []
    with pytest.raises(PersistentStoreFailure,match='incomplete'):
        Pruned().active_trainability_calendar('ath-test',date.min,date.max)
