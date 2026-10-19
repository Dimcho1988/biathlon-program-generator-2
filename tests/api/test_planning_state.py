from types import SimpleNamespace

import pytest

from apps.api.management_plan_store import PlanStore
from apps.api import management_lifecycle as lifecycle
from apps.api.oauth_store import PersistentStoreFailure


def test_poll_reads_only_pinned_metadata_and_detects_same_revision_input_changes(monkeypatch):
    paths = []
    def request(method, path):
        paths.append(path)
        assert method == "GET" and "athlete_alias=eq.ath%26one" in path
        assert path.startswith("/onflows_management_plan_state?select=revision,")
        assert "record:onflows_management_plan_revisions!onflows_management_plan_state_athlete_alias_revision_fkey(" in path
        assert "(revision,status:payload->status,fingerprint:payload->processed_input_fingerprint," in path
        assert "!inner" not in path and ",payload," not in path
        return [{"revision": 9, "record": {
            "revision": 9, "status": "REVIEW_REQUIRED", "fingerprint": "old"}}]
    repo = SimpleNamespace(_request=request, _json=lambda v: v)
    monkeypatch.setattr(lifecycle, "_context", lambda *a: (1, {}, {}, "old", "approval"))
    assert lifecycle.state(repo, "ath&one") == {"revision": 9, "stale": False}
    monkeypatch.setattr(lifecycle, "_context", lambda *a: (1, {}, {}, "new", "approval"))
    assert lifecycle.state(repo, "ath&one") == {"revision": 9, "stale": True}
    assert len(paths) == 2


def test_missing_or_invalid_metadata_is_not_a_false_unchanged_response():
    repo = SimpleNamespace(_request=lambda *a: [], _json=lambda v: v)
    assert lifecycle.state(repo, "athlete") == {"revision": 0, "stale": False}
    repo._request = lambda *a: [{"revision": 1}]
    with pytest.raises(PersistentStoreFailure):
        PlanStore(repo).current("athlete", metadata_only=True)


def test_current_poll_returns_optional_compact_analysis_identity_without_loading_plans():
    metadata = {"revision": 9, "status": "REVIEW_REQUIRED", "fingerprint": "old"}
    for prefix in ("plan", "proposal"):
        metadata.update({f"{prefix}_generation_id": f"{prefix}-generation",
                         f"{prefix}_analysis_revision": 50,
                         f"{prefix}_analysis_as_of": "2026-10-19T07:00:00Z",
                         f"{prefix}_snapshot_fingerprint": f"{prefix}-snapshot"})

    def request(method, path):
        for prefix in ("plan", "proposal"):
            for field in ("generation_id", "analysis_revision", "analysis_as_of", "snapshot_fingerprint"):
                assert f"{prefix}_{field}:payload->{prefix}->input_snapshot->{field}" in path
        assert ",payload," not in path
        return [{"revision": 9, "record": metadata}]

    repo = SimpleNamespace(_request=request, _json=lambda v: v)
    assert PlanStore(repo).current("athlete", metadata_only=True) == metadata


def test_current_returns_pointer_pinned_full_record_in_one_read():
    calls = []
    records = [
        {"revision": 9, "operation": "REVIEW", "payload": {"plan": "previous"},
         "recorded_at": "2026-10-19T07:00:00Z"},
        {"revision": 10, "operation": "ADAPT", "payload": {"plan": "next"},
         "recorded_at": "2026-10-19T08:00:00Z"},
    ]

    def request(method, path):
        calls.append((method, path))
        assert path.startswith("/onflows_management_plan_state?")
        assert "(revision,operation,payload,recorded_at)" in path
        assert "athlete_alias=eq.ath%26one%2Ftwo%3F" in path
        record = records[len(calls) - 1]
        return [{"revision": record["revision"], "record": record}]

    store = PlanStore(SimpleNamespace(_request=request, _json=lambda v: v))
    # A publication between reads is visible on the next call, while the
    # returned record always belongs to the pointer from that same read.
    assert store.current("ath&one/two?") == records[0]
    assert len(calls) == 1
    assert store.current("ath&one/two?") == records[1]
    assert len(calls) == 2


@pytest.mark.parametrize("rows", [
    None, {}, [None], [{"revision": 9}, {"revision": 10}],
    [{"revision": None}], [{"revision": "9"}], [{"revision": True}],
    [{"revision": 0}], [{"revision": -1}],
    [{"revision": 9, "record": None}],
    [{"revision": 9, "record": []}],
    [{"revision": 9, "record": {"revision": 10, "payload": {}}}],
    [{"revision": 9, "record": {"revision": "9", "payload": {}}}],
    [{"revision": 1, "record": {"revision": True, "payload": {}}}],
])
@pytest.mark.parametrize("metadata_only", [False, True])
def test_current_fails_closed_on_invalid_pointer_or_embedded_record(rows, metadata_only):
    repo = SimpleNamespace(_request=lambda *a: rows, _json=lambda v: v)
    with pytest.raises(PersistentStoreFailure):
        PlanStore(repo).current("athlete", metadata_only=metadata_only)


@pytest.mark.parametrize("record", [
    {"revision": 9}, {"revision": 9, "payload": None},
    {"revision": 9, "payload": []},
])
def test_current_rejects_missing_or_invalid_full_payload(record):
    repo = SimpleNamespace(_request=lambda *a: [{"revision": 9, "record": record}],
                           _json=lambda v: v)
    with pytest.raises(PersistentStoreFailure):
        PlanStore(repo).current("athlete")


@pytest.mark.parametrize("record", [
    {"revision": 9},
    {"revision": 9, "status": "ACTIVE", "fingerprint": None},
    {"revision": 9, "status": None, "fingerprint": "old"},
    {"revision": 9, "status": "ACTIVE", "fingerprint": {}},
    {"revision": 9, "status": False, "fingerprint": "old"},
])
def test_current_rejects_invalid_embedded_poll_metadata(record):
    repo = SimpleNamespace(_request=lambda *a: [{"revision": 9, "record": record}],
                           _json=lambda v: v)
    with pytest.raises(PersistentStoreFailure):
        PlanStore(repo).current("athlete", metadata_only=True)
