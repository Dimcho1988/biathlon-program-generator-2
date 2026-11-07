"""Reuse only the initial read phase; computation and publication stay live."""
from collections import Counter
from copy import deepcopy
from datetime import timedelta
import json

import httpx
import pytest

from apps.api import management_lifecycle as lifecycle, management_service as service
from apps.api.read_session import ReadSession
from tests.api.test_management_final_read_phase import PublicationHTTP
from tests.api.test_training_plan_engine import NOW, TODAY


class InitialPhaseHTTP(PublicationHTTP):
    """The real HTTP adapter with a profile CAS independent of ordinary inputs."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.profile_revision = 1
        self.before_build = None
        self.initial_phase = None

    def handler(self, request):
        path = request.url.path.removeprefix("/rest/v1")
        body = json.loads(request.content) if request.content else None
        if path == "/onflows_management_entries":
            self.requests.append((request.method, path, dict(request.url.params), deepcopy(body)))
            return httpx.Response(200, json=[{"kind": "PROFILE", "entry_key": "management-profile-v1",
                "revision": self.profile_revision, "payload": deepcopy(self.profile)}])
        if path == "/rpc/save_onflows_management_plan_compact" and body["p_expected_profile_revision"] != self.profile_revision:
            self.requests.append((request.method, path, dict(request.url.params), deepcopy(body)))
            return httpx.Response(200, json={"conflict": True, "reason": "PROFILE_CHANGED"})
        return super().handler(request)


def run_refresh(monkeypatch, harness, *, separate_initial_phase=False, expect_initial_phase=True):
    build = service.build_draft
    def build_draft(repository, *args, **kwargs):
        assert repository is harness.repository, "Computation/publication must retain the original live repository"
        harness.initial_phase = kwargs.get("_initial_read_phase")
        assert (harness.initial_phase is not None) is expect_initial_phase
        if harness.before_build:
            harness.before_build(harness)
        if separate_initial_phase:
            kwargs.pop("_initial_read_phase")  # Reproduce the former independent draft read phase.
        return build(repository, *args, **kwargs)
    with monkeypatch.context() as context:
        context.setattr(service, "build_draft", build_draft)
        return harness.run(context)


@pytest.mark.parametrize("calendar_resolves_horizon", [False, True])
def test_warm_refresh_reuses_five_reads_with_identical_full_json_and_computation_key(monkeypatch, calendar_resolves_horizon):
    previous, current = InitialPhaseHTTP(), InitialPhaseHTTP()
    if calendar_resolves_horizon:
        for harness in (previous, current):
            harness.row["planning_calendar"]["events"] = [{
                "event_id": "main-race", "event_type": "MAIN_RACE", "name": "Synthetic main race",
                "start_date": (TODAY+timedelta(days=60)).isoformat(),
                "end_date": (TODAY+timedelta(days=60)).isoformat(),
            }]
        resolved, _ = service.training_plan_engine.planning_schedule.horizon(
            current.profile, current.row["planning_calendar"]["events"])
        assert resolved["program_end"] != current.profile["program_end"]
    expected = run_refresh(monkeypatch, previous, separate_initial_phase=True)
    actual = run_refresh(monkeypatch, current)
    assert actual.status_code == expected.status_code == 200
    assert actual.json() == expected.json()
    assert len(previous.requests) == 25 and len(current.requests) == 20
    before = Counter(path for _, path, _, _ in previous.requests)
    after = Counter(path for _, path, _, _ in current.requests)
    assert before-after == Counter({
        "/onflows_management_entries": 1, "/onflows_athlete_settings": 1,
        "/rpc/active_onflows_analysis": 1, "/onflows_model_entries": 1, "/onflows_response_entries": 1})
    def computation_keys(harness):
        return [body["p_key"] for _, path, _, body in harness.requests
                if path == "/rpc/onflows_planning_computation" and body["p_operation"] == "CLAIM"]
    assert computation_keys(previous) == computation_keys(current) == [current.key]
    saved_profile = actual.json()["active"]["payload"]["proposal"]["input_snapshot"]["management_profile"]
    assert saved_profile == current.profile
    assert saved_profile["program_end"] == current.profile["program_end"]


@pytest.mark.parametrize("field,value", [
    ("hrmax_bpm", 201), ("planning_calendar", None), ("planning_profile", None),
    ("mesocycle_accent_preferences", None), ("timezone", "Pacific/Honolulu"),
])
def test_settings_edit_after_initial_capture_before_build_is_rejected_by_fresh_final_phase(monkeypatch, field, value):
    harness = InitialPhaseHTTP()
    harness.before_build = lambda state: state.row.update({field: value})
    response = run_refresh(monkeypatch, harness)
    assert response.status_code == 409
    assert "inputs changed" in response.json()["detail"]
    assert harness.record["revision"] == 1
    assert not any(path == "/rpc/save_onflows_management_plan_compact" for _, path, _, _ in harness.requests)


@pytest.mark.parametrize("kind", ["generation", "model", "response"])
def test_non_settings_edit_after_initial_capture_cannot_publish_a_stale_candidate(monkeypatch, kind):
    harness = InitialPhaseHTTP()
    def change(state):
        if kind == "generation":
            state.envelope.update(generation_id="new-generation", revision=2)
        elif kind == "model":
            state.models = [{"kind": "RECOVERY", "entry_key": "changed", "revision": 1, "payload": {}}]
        else:
            state.responses = [{"kind": "DAILY", "entry_key": "changed", "revision": 1, "payload": {}}]
    harness.before_build = change
    response = run_refresh(monkeypatch, harness)
    assert response.status_code == 409 and harness.record["revision"] == 1
    assert not any(path == "/rpc/save_onflows_management_plan_compact" for _, path, _, _ in harness.requests)


def test_profile_edit_after_initial_capture_is_still_rejected_by_publication_profile_cas(monkeypatch):
    harness = InitialPhaseHTTP()
    def edit_profile(state):
        state.profile_revision += 1
        state.profile["discipline"] = "3000 m"
    harness.before_build = edit_profile
    response = run_refresh(monkeypatch, harness)
    assert response.status_code == 409 and harness.record["revision"] == 1
    assert response.json()["detail"] == "The plan or its inputs changed; reload and try again"
    writes = [body for _, path, _, body in harness.requests if path == "/rpc/save_onflows_management_plan_compact"]
    assert len(writes) == 1 and writes[0]["p_expected_profile_revision"] == 1


def test_initial_phase_from_another_repository_is_rejected_before_any_http_read():
    first, second = InitialPhaseHTTP(), InitialPhaseHTTP()
    phase = ReadSession.planning(first.repository)
    with pytest.raises(ValueError, match="another repository"):
        service.build_draft(second.repository, "ath-test", TODAY, 1, now=NOW, _initial_read_phase=phase)
    assert not first.requests and not second.requests


@pytest.mark.parametrize("invalid_kind", ["not_a_phase", "no_bundle"])
def test_initial_phase_requires_the_planning_read_configuration_before_any_http_read(invalid_kind):
    harness = InitialPhaseHTTP()
    phase = object() if invalid_kind == "not_a_phase" else ReadSession(harness.repository)
    with pytest.raises(ValueError, match="configuration"):
        service.build_draft(harness.repository, "ath-test", TODAY, 1, now=NOW, _initial_read_phase=phase)
    assert not harness.requests


def test_same_initial_phase_keeps_profiles_settings_sources_and_keys_separate_by_athlete(monkeypatch):
    class TwoAthletesHTTP(InitialPhaseHTTP):
        def __init__(self):
            self.other = InitialPhaseHTTP()
            self.other.profile_revision = 2
            self.other.profile["discipline"] = "3000 m"
            self.other.row["hrmax_bpm"] = 201
            self.other.envelope.update(generation_id="other-generation", revision=2)
            self.other.envelope["snapshot_payload"]["other_athlete_marker"] = True
            self.other.raw_plan["source"]["generation_id"] = "other-generation"
            super().__init__()

        def handler(self, request):
            body = json.loads(request.content) if request.content else {}
            alias = body.get("p_alias") or body.get("p_athlete_alias") or request.url.params.get("athlete_alias", "").removeprefix("eq.")
            if alias == "ath-other":
                return self.other.handler(request)
            return super().handler(request)

    harness = TwoAthletesHTTP()
    phase = ReadSession.planning(harness.repository)
    lifecycle._context(harness.repository, "ath-test", NOW, _initial_read_phase=phase)
    other_inputs = service.input_state(harness.repository, "ath-other", evaluated_at=NOW, include_response=True)
    harness.other.key = service._hash({"athlete": "ath-other", "inputs": other_inputs,
        "profile": harness.other.profile, "profile_revision": 2,
        "start_date": TODAY.isoformat(), "decisions": {}, "locked_day": None})
    harness.requests.clear()
    harness.other.requests.clear()
    monkeypatch.setattr(service.training_plan_engine, "generate_plan",
        lambda *args, **kwargs: pytest.fail("The other athlete must join its own READY computation"))
    payload = service.build_draft(harness.repository, "ath-other", TODAY, 2, now=NOW, _initial_read_phase=phase)
    assert payload["source"]["generation_id"] == "other-generation"
    assert payload["input_snapshot"]["analysis_revision"] == 2
    assert payload["input_snapshot"]["physiology"]["hrmax_bpm"] == 201
    assert payload["input_snapshot"]["management_profile"] == harness.other.profile
    assert payload["input_snapshot"]["snapshot_fingerprint"] == service._hash(harness.other.envelope["snapshot_payload"])
    assert harness.requests == [] and len(harness.other.requests) == 10


def test_legacy_snapshot_keeps_the_original_independent_before_phase(monkeypatch):
    class LegacySnapshotHTTP(InitialPhaseHTTP):
        def __init__(self):
            super().__init__()
            self.envelope.update(generation_id=None, revision=0)
            self.raw_plan["source"]["generation_id"] = None

        def handler(self, request):
            if request.url.path.endswith("/rpc/active_onflows_activity_calendar"):
                self.requests.append((request.method, "/rpc/active_onflows_activity_calendar",
                    dict(request.url.params), json.loads(request.content)))
                return httpx.Response(200, json=[{**self.envelope, "activities": []}])
            return super().handler(request)

    previous, current = LegacySnapshotHTTP(), LegacySnapshotHTTP()
    expected = run_refresh(monkeypatch, previous, separate_initial_phase=True, expect_initial_phase=False)
    actual = run_refresh(monkeypatch, current, expect_initial_phase=False)
    assert actual.status_code == expected.status_code == 200
    assert actual.json() == expected.json()
    assert len(current.requests) == len(previous.requests) == 27


def test_new_generation_during_reconciliation_cannot_publish_the_old_capture(monkeypatch):
    harness = InitialPhaseHTTP()
    reconcile = lifecycle.reconcile
    def activate_new_generation(plan, source, *args, **kwargs):
        harness.envelope.update(generation_id="new-generation", revision=2)
        return reconcile(plan, source, *args, **kwargs)
    monkeypatch.setattr(lifecycle, "reconcile", activate_new_generation)
    response = run_refresh(monkeypatch, harness)
    assert response.status_code == 409 and "inputs changed" in response.json()["detail"]
    assert harness.envelope["generation_id"] == "new-generation" and harness.envelope["revision"] == 2
    assert harness.record["revision"] == 1
    assert not any(path == "/rpc/save_onflows_management_plan_compact" for _, path, _, _ in harness.requests)
