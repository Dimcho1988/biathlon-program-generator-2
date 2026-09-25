"""Regression gates for read optimizations: identical results, fewer reads."""
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api import main, management_service, management_lifecycle, http_runtime
from apps.api.management_plan_store import PlanStore
from apps.api.management_store import PROFILE_KEY
from apps.api.read_session import ReadSession
from apps.api.oauth_store import SupabasePilotRepository
from tests.api.test_management_api import PROFILE, HEADERS
from tests.api.test_training_plan_engine import Repository as HistoryRepository


class Repository:
    def __init__(self):
        self.calls = Counter()
        self.envelope = HistoryRepository().envelope
        for activity in self.envelope['snapshot_payload']['load_history']['activities']:
            for zone in activity['zones']:
                zone['equivalent_time_min'] = zone['raw_time_min']
        self.profile = {"kind": "PROFILE", "entry_key": PROFILE_KEY, "revision": 1,
                        "payload": deepcopy(PROFILE)}
        self.record = {"revision": 1, "operation": "ACTIVATE", "recorded_at": "2026-09-21T12:00:00Z",
                       "payload": {"status": "ACTIVE", "processed_input_fingerprint": "old",
                                   "changes": [], "reason": "test"}}

    def athlete_settings(self, alias):
        self.calls['settings'] += 1
        return HistoryRepository().settings

    def active_analysis(self, alias):
        self.calls['analysis'] += 1
        return deepcopy(self.envelope)

    def active_trainability_calendar(self, alias, start, end):
        self.calls['trainability_calendar'] += 1
        return {**deepcopy(self.envelope), 'activities': []}

    def athlete_planning_calendar(self, alias):
        self.calls['calendar'] += 1
        return None

    def athlete_planning_profile(self, alias):
        self.calls['preferences'] += 1
        return None

    def athlete_mesocycle_accent_preferences(self, alias):
        self.calls['accents'] += 1
        return None

    def sync_state(self, alias):
        self.calls['sync'] += 1
        return {"state": "SUCCEEDED", "active_revision": 1}

    def _request(self, method, path, **kwargs):
        assert method == 'GET' and not kwargs
        self.calls[path] += 1
        if path.startswith('/onflows_management_entries?'):
            rows = [self.profile] if 'kind=eq.PROFILE' in path else [{
                "kind": "DRAFT", "entry_key": "2026-09-22", "revision": 1,
                "payload": {"input_fingerprint": "old"}}]
        elif path.startswith('/onflows_management_plan_state?'):
            rows = [{"revision": 1}]
        elif path.startswith('/onflows_management_plan_revisions?'):
            if 'status:payload->status' in path:
                rows = [{k: self.record[k] for k in ('revision', 'operation', 'recorded_at')}
                        | {k: self.record['payload'][k] for k in ('status', 'changes', 'reason')}]
            else:
                rows = [self.record]
        elif path.startswith('/onflows_model_entries?'):
            rows = []
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=deepcopy(rows))

    @staticmethod
    def _json(response):
        return response.json()


@pytest.mark.parametrize("view", ["week", "overview"])
def test_management_batch_equals_individual_resources_and_removes_repeat_reads(monkeypatch, view):
    fixed_now = datetime.now(timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz)
    monkeypatch.setattr(management_service, 'datetime', Clock)
    plain = Repository()
    expected = {
        'profile': management_service.profile_view(plain, 'ath-test'),
        'active': management_lifecycle.current(plain, 'ath-test'),
        'drafts': management_service.history(plain, 'ath-test') if view == 'week' else {'drafts': []},
        'outlook': management_service.outlook(plain, 'ath-test') if view == 'overview' else None,
        'sync': main._public_sync_state(plain.sync_state('ath-test')).model_dump(mode='json'),
    }
    batched = Repository()
    monkeypatch.setenv('ONFLOWS_SERVICE_TOKEN', 'service-secret')
    monkeypatch.setattr(main, '_repository', lambda: batched)
    response = TestClient(main.app).get(f'/api/v2/athlete/management/view?view={view}', headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert plain.calls['analysis'] >= 2 and plain.calls['settings'] >= 3
    assert batched.calls['analysis'] == batched.calls['settings'] == 1
    assert sum(batched.calls.values()) < sum(plain.calls.values())
    assert max(batched.calls.values()) == 1


def test_read_session_is_request_and_athlete_scoped_and_never_caches_writes():
    repository = Repository()
    first = ReadSession(repository)
    loaded = first.active_analysis('ath-one')
    loaded['revision'] = 99
    assert first.active_analysis('ath-one')['revision'] == 1
    first.active_analysis('ath-two')
    second = ReadSession(repository)
    second.active_analysis('ath-one')
    assert repository.calls['analysis'] == 3
    with pytest.raises(ValueError):
        first._request('POST', '/rpc/save_something', json={})
    with pytest.raises(AttributeError):
        first.save_connection


def test_read_session_does_not_hide_failed_reads():
    calls = []
    def read(alias):
        calls.append(alias)
        if len(calls) == 1:
            raise RuntimeError('temporary')
        return {'revision': 2}
    scope = ReadSession(SimpleNamespace(active_analysis=read))
    with pytest.raises(RuntimeError):
        scope.active_analysis('ath-one')
    assert scope.active_analysis('ath-one') == {'revision': 2}
    assert len(calls) == 2


def test_plan_history_fetches_only_fields_shown_in_the_journal():
    repo = Repository()
    assert PlanStore(repo).history('ath-test') == [{
        'revision': 1, 'operation': 'ACTIVATE', 'recorded_at': '2026-09-21T12:00:00Z',
        'status': 'ACTIVE', 'changes': [], 'reason': 'test'}]
    path = next(iter(repo.calls))
    assert 'recorded_at,payload' not in path
    assert 'status:payload->status,changes:payload->changes,reason:payload->reason' in path


def test_successful_void_plan_deferral_is_not_decoded_as_json():
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return httpx.Response(204)
    repository = SimpleNamespace(_request=request, _json=SupabasePilotRepository._json)
    assert PlanStore(repository).defer_check('ath-test', 7) is None
    assert calls == [('POST', '/rpc/defer_onflows_management_check',
                      {'json': {'p_alias': 'ath-test', 'p_revision': 7}})]


def test_http_pool_is_reused_and_closed_at_application_shutdown(monkeypatch):
    clients = []
    class Client:
        is_closed = False
        def __init__(self, **kwargs):
            clients.append(self)
        def close(self):
            self.is_closed = True
    monkeypatch.setattr(http_runtime, '_client', None)
    monkeypatch.setattr(http_runtime.httpx, 'Client', Client)
    async def run():
        async with http_runtime.lifespan(None):
            first = http_runtime.store_client()
            assert http_runtime.store_client() is first
            assert len(clients) == 1
        assert first.is_closed
        async with http_runtime.lifespan(None):
            assert http_runtime.store_client() is not first
        assert len(clients) == 2 and all(c.is_closed for c in clients)
    asyncio.run(run())


def test_batch_requires_the_same_explicit_athlete_authorization(monkeypatch):
    monkeypatch.setenv('ONFLOWS_SERVICE_TOKEN', 'service-secret')
    client = TestClient(main.app)
    assert client.get('/api/v2/athlete/management/view').status_code == 401
    assert client.get('/api/v2/athlete/management/view', headers={'Authorization': 'Bearer service-secret'}).status_code == 401
    assert client.get('/api/v2/athlete/management/view?view=invalid', headers=HEADERS).status_code == 422
