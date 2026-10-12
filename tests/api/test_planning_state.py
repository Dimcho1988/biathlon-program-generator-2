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
        if "plan_state?" in path:
            return [{"revision": 9}]
        assert "revision=eq.9" in path
        assert "select=revision,status:payload->status,fingerprint:payload->processed_input_fingerprint" in path
        return [{"revision": 9, "status": "REVIEW_REQUIRED", "fingerprint": "old"}]
    repo = SimpleNamespace(_request=request, _json=lambda v: v)
    monkeypatch.setattr(lifecycle, "_context", lambda *a: (1, {}, {}, "old", "approval"))
    assert lifecycle.state(repo, "ath&one") == {"revision": 9, "stale": False}
    monkeypatch.setattr(lifecycle, "_context", lambda *a: (1, {}, {}, "new", "approval"))
    assert lifecycle.state(repo, "ath&one") == {"revision": 9, "stale": True}
    assert len(paths) == 4


def test_missing_or_invalid_metadata_is_not_a_false_unchanged_response():
    repo = SimpleNamespace(_request=lambda *a: [], _json=lambda v: v)
    assert lifecycle.state(repo, "athlete") == {"revision": 0, "stale": False}
    repo._request = lambda *a: [{"revision": 1}]
    with pytest.raises(PersistentStoreFailure):
        PlanStore(repo).current("athlete", metadata_only=True)
