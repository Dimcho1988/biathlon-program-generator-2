"""Exact freshness with compact immutable analysis identity; writes stay live."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from apps.api import management_lifecycle as lifecycle, management_service as service
from apps.api.oauth_store import PersistentStoreFailure
from tests.api.test_read_performance import Repository
from tests.api.test_training_plan_engine import NOW
from tests.api.test_supabase_generation_rpc_contract import (
    ALIAS, GENERATION_ID, SupabaseHTTPHarness, repository, rpc_path,
)


class ProjectionRepository(Repository):
    def __init__(self):
        super().__init__()
        self.envelope.update(analysis_as_of="2026-09-21", activated_at="2026-09-21T08:00:00Z")
        self.settings = super().athlete_settings("ath-test")
        self.calendar = None
        self.preferences = None
        self.accents = None
        self.models = []
        self.responses = []

    def athlete_settings(self, alias):
        self.calls["settings"] += 1
        return self.settings

    def athlete_planning_calendar(self, alias):
        self.calls["calendar"] += 1
        return self.calendar

    def athlete_planning_profile(self, alias):
        self.calls["preferences"] += 1
        return self.preferences

    def athlete_mesocycle_accent_preferences(self, alias):
        self.calls["accents"] += 1
        return self.accents

    def active_analysis_metadata(self, alias):
        self.calls["analysis_metadata"] += 1
        return {k: self.envelope.get(k) for k in
                ("generation_id", "revision", "analysis_as_of", "activated_at")}

    def _request(self, method, path, **kwargs):
        if path.startswith(("/onflows_model_entries?", "/onflows_response_entries?")):
            import httpx
            self.calls[path] += 1
            return httpx.Response(200, json=deepcopy(self.models if "model_entries" in path else self.responses))
        response = super()._request(method, path, **kwargs)
        if "fingerprint:payload->processed_input_fingerprint" in path:
            import httpx
            row = response.json()[0]
            for prefix in ("proposal", "plan"):
                frozen = (self.record["payload"].get(prefix) or {}).get("input_snapshot", {})
                row["record"].update({f"{prefix}_{k}": frozen.get(k) for k in
                    ("generation_id", "analysis_revision", "analysis_as_of", "snapshot_fingerprint")})
            return httpx.Response(200, json=[row])
        return response


def saved_repository(prefix="proposal"):
    repo = ProjectionRepository()
    _, _, frozen, fingerprint, _ = lifecycle._context(repo, "ath-test", NOW)
    repo.record["payload"].update(processed_input_fingerprint=fingerprint,
                                 **{prefix: {"input_snapshot": frozen}})
    repo.calls.clear()
    return repo


@pytest.mark.parametrize("prefix", ["proposal", "plan"])
def test_unchanged_generation_reuses_exact_saved_digest_without_full_snapshot(prefix):
    repo = saved_repository(prefix)
    assert lifecycle.state(repo, "ath-test", now=NOW) == {"revision": 1, "stale": False}
    assert repo.calls["analysis_metadata"] == 1 and repo.calls["analysis"] == 0
    assert repo.calls["settings"] == repo.calls["calendar"] == 1
    assert repo.calls["preferences"] == repo.calls["accents"] == 1
    frozen = repo.record["payload"][prefix]["input_snapshot"]
    assert service.input_state(repo, "ath-test", evaluated_at=NOW, include_response=True,
                               analysis_hints=[frozen]) == service.input_state(
                                   repo, "ath-test", evaluated_at=NOW, include_response=True)


@pytest.mark.parametrize("change", ["date", "physiology", "profile", "model", "response", "calendar", "preferences", "accents"])
def test_same_plan_revision_still_detects_every_other_input_change(change):
    repo = saved_repository()
    now = NOW
    if change == "date":
        now += timedelta(days=1)
    elif change == "physiology":
        repo.settings.hrmax_bpm += 1
    elif change == "profile":
        repo.profile["revision"] += 1
    elif change == "model":
        repo.models = [{"kind": "OBSERVATION", "entry_key": "new", "revision": 1, "payload": {}}]
    elif change == "response":
        repo.responses = [{"kind": "DAILY", "entry_key": "new", "revision": 1, "payload": {}}]
    else:
        setattr(repo, change, SimpleNamespace(to_payload=lambda: {"changed": True}))
    assert lifecycle.state(repo, "ath-test", now=now) == {"revision": 1, "stale": True}
    assert repo.calls["analysis"] == 0 and repo.calls["analysis_metadata"] == 1


@pytest.mark.parametrize("change", ["generation", "revision", "as_of", "legacy", "missing_hint"])
def test_changed_or_legacy_identity_falls_back_to_full_snapshot(change):
    repo = saved_repository()
    if change == "generation":
        repo.envelope["generation_id"] = "generation-two"
    elif change == "revision":
        repo.envelope["revision"] += 1
    elif change == "as_of":
        repo.envelope["analysis_as_of"] = "2026-09-22"
    elif change == "legacy":
        repo.envelope["generation_id"] = None
    else:
        repo.record["payload"]["proposal"].clear()
    if change != "missing_hint":
        repo.envelope["snapshot_payload"]["changed"] = 1
    assert lifecycle.state(repo, "ath-test", now=NOW) == {"revision": 1, "stale": change != "missing_hint"}
    assert repo.calls["analysis"] == 1


def test_activation_between_metadata_and_full_read_uses_full_reads_own_identity():
    repo = saved_repository()
    frozen = repo.record["payload"]["proposal"]["input_snapshot"]
    def activate(alias):
        repo.envelope.update(generation_id="generation-three", revision=3, analysis_as_of="2026-09-23")
        repo.envelope["snapshot_payload"]["new"] = True
        return {"generation_id": "generation-two", "revision": 2, "analysis_as_of": "2026-09-22"}
    repo.active_analysis_metadata = activate
    state = service.input_state(repo, "ath-test", evaluated_at=NOW, analysis_hints=[frozen])
    assert (state["generation_id"], state["analysis_revision"], state["analysis_as_of"]) == (
        "generation-three", 3, "2026-09-23")
    assert state["snapshot_fingerprint"] == service._hash(repo.envelope["snapshot_payload"])


def test_write_context_never_uses_saved_hint_or_compact_identity():
    repo = saved_repository()
    lifecycle._context(repo, "ath-test", NOW)
    assert repo.calls["analysis"] == 1 and repo.calls["analysis_metadata"] == 0


def test_mutable_legacy_snapshot_never_reuses_a_digest_even_at_same_revision():
    repo = saved_repository()
    repo.envelope.update(generation_id=None, revision=0)
    _, _, frozen, fingerprint, _ = lifecycle._context(repo, "ath-test", NOW)
    repo.record["payload"].update(proposal={"input_snapshot": frozen},
                                 processed_input_fingerprint=fingerprint)
    repo.envelope["snapshot_payload"]["changed"] = True
    repo.calls.clear()
    assert lifecycle.state(repo, "ath-test", now=NOW) == {"revision": 1, "stale": True}
    assert repo.calls["analysis"] == 1 and repo.calls["analysis_metadata"] == 0


@pytest.mark.parametrize("fingerprint", [None, "", "not-a-digest", "g"*64])
def test_invalid_saved_digest_falls_back_without_a_false_fresh_result(fingerprint):
    repo = saved_repository()
    repo.record["payload"]["proposal"]["input_snapshot"]["snapshot_fingerprint"] = fingerprint
    repo.envelope["snapshot_payload"]["changed"] = True
    assert lifecycle.state(repo, "ath-test", now=NOW) == {"revision": 1, "stale": True}
    assert repo.calls["analysis"] == 1 and repo.calls["analysis_metadata"] == 0


def test_repository_projects_coherent_rpc_metadata_without_snapshot():
    harness = SupabaseHTTPHarness()
    metadata = {"generation_id": GENERATION_ID, "revision": 7,
                "analysis_as_of": "2026-08-27", "activated_at": "2026-08-27T06:30:00+00:00"}
    harness.respond(rpc_path("active_onflows_analysis"), [metadata])
    assert repository(harness).active_analysis_metadata(ALIAS) == metadata
    assert harness.requests == [{"method": "POST", "path": rpc_path("active_onflows_analysis"),
        "query": "select=generation_id,revision,analysis_as_of,activated_at",
        "json": {"p_athlete_alias": ALIAS}, "prefer": None}]


@pytest.mark.parametrize("rows", [None, {}, [None], [{"revision": True}], [{"revision": -1}],
    [{"revision": 1, "generation_id": ""}], [{"revision": 1, "analysis_as_of": []}],
    [{"revision": 1}, {"revision": 2}]])
def test_repository_rejects_invalid_compact_identity(rows):
    harness = SupabaseHTTPHarness()
    harness.respond(rpc_path("active_onflows_analysis"), rows)
    with pytest.raises(PersistentStoreFailure):
        repository(harness).active_analysis_metadata(ALIAS)
