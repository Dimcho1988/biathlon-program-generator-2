"""Large recordings are decoded in order, once consumed, without a history cache."""
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import pytest

from apps.api.oauth_store import SupabasePilotRepository, PersistentStoreFailure
from apps.api.recording_reads import iter_samples
from apps.api.read_session import ReadSession
from apps.api.speed_load import activity_samples


def repository(monkeypatch, count=12):
    keys = tuple(f'{i:064x}' for i in range(count))
    calls, decoded = [], []
    repo = SupabasePilotRepository(supabase_url='https://offline.invalid', secret_key='offline',
        encryption_key='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))), generation_reads=True)
    def request(method, path):
        calls.append(path)
        batch = path.split('run_key=in.(')[1].split(')')[0].split(',')
        return httpx.Response(200, json=[{'run_key':k, 'activity_ref':k, 'timeseries':[{'elapsed_s':int(k,16)}]}
                                       for i,k in reversed(list(enumerate(dict.fromkeys(batch))))])
    monkeypatch.setattr(repo, '_request', request)
    def decode(payload, key):
        decoded.append(key)
        return payload
    monkeypatch.setattr(repo, '_decode_shadow', decode)
    return repo, keys, calls, decoded


def test_streaming_reads_compressed_batches_and_decodes_only_consumed_runs(monkeypatch):
    repo, keys, calls, decoded = repository(monkeypatch)
    stream = iter_samples(ReadSession(repo), 'athlete', keys)
    assert calls == decoded == []
    key, first = next(stream)
    assert key == keys[0] and first['activity_ref'] == key
    assert len(calls) == 1 and decoded == [keys[0]]
    rest = list(stream)
    assert [key for key, _ in rest] == list(keys[1:])
    assert len(calls) == 2 and decoded == list(keys)
    assert all('athlete_alias=eq.athlete' in path for path in calls)


def test_streaming_preserves_input_order_duplicates_and_plain_reader_values(monkeypatch):
    repo, keys, _, _ = repository(monkeypatch, 3)
    requested = (keys[2], keys[0], keys[2], keys[1])
    expected = repo.activity_speed_history_samples('athlete', requested)
    assert list(iter_samples(repo, 'athlete', requested)) == [(key, expected[key]) for key in requested]


def test_missing_batch_fails_before_decoding_any_of_its_rows(monkeypatch):
    repo, keys, _, decoded = repository(monkeypatch, 3)
    monkeypatch.setattr(repo, '_request', lambda *args: httpx.Response(200, json=[]))
    with pytest.raises(PersistentStoreFailure):
        list(iter_samples(repo, 'athlete', keys))
    assert not decoded


def test_speed_reports_consume_streams_in_original_activity_order(monkeypatch):
    repo, keys, calls, decoded = repository(monkeypatch, 3)
    activities = [{'latest_shadow_run_key':key} for key in keys]
    rows = activity_samples(ReadSession(repo), 'athlete', activities, include_raw=True)
    assert next(rows)[1]['run_key'] == keys[0]
    assert len(decoded) == 1
    assert [row['run_key'] for _, row in rows] == list(keys[1:])
    assert len(calls) == 1


def test_legacy_recording_repositories_keep_the_same_contract():
    reader = Mock(side_effect=lambda alias,keys: {key:{'activity_ref':key} for key in keys})
    keys = tuple(str(i) for i in range(23))
    repo = SimpleNamespace(activity_speed_history_samples=reader)
    assert list(iter_samples(repo, 'athlete', keys)) == [(key,{'activity_ref':key}) for key in keys]
    assert reader.call_count == 3


def test_parallel_reports_do_not_expand_recordings_together():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock
    from time import sleep
    barrier, lock = Barrier(3), Lock()
    active, maximum = 0, 0
    def reader(alias, keys):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            sleep(.02)
            for key in keys:
                yield key, {'activity_ref':key}
        finally:
            with lock:
                active -= 1
    repo = SimpleNamespace(iter_activity_speed_history_samples=reader)
    def run(alias):
        barrier.wait()
        return list(iter_samples(repo, alias, ('pinned',)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(run,'one'), pool.submit(run,'two')
        barrier.wait()
        assert one.result() == two.result() == [('pinned',{'activity_ref':'pinned'})]
    assert maximum == 1


def test_plan_waiting_for_a_recording_cache_owner_yields_the_shared_lane():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from apps.api.memory_budget import MODEL_WORK
    from apps.api.speed_load_cache import SpeedLoadCache
    cache=SpeedLoadCache()
    claimed, plan_running = Event(), Event()
    def record():
        def calculate():
            claimed.set()
            assert plan_running.wait(2.)
            with MODEL_WORK:
                return {'value':42}
        return cache.get_or_compute('shared',calculate)
    def plan():
        assert claimed.wait(2.)
        with MODEL_WORK:
            with MODEL_WORK:
                plan_running.set()
                return cache.get_or_compute('shared',lambda:pytest.fail('duplicate recording calculation'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        one,two=pool.submit(record),pool.submit(plan)
        assert one.result(timeout=3.)==two.result(timeout=3.)=={'value':42}
