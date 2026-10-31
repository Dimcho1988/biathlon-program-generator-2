"""A cached immutable analysis can supply the same generation-pinned catalog."""
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest

from apps.api import management_service as service
from apps.api.oauth_store import PersistentStoreFailure
from apps.api.read_session import ReadSession
from tests.api.test_management_overview_projection import OverviewHTTP, request_overview
from tests.api.test_outlook_calendar_reuse import OriginalReaderOnly, PlanningHTTP, fixed_clocks, read_page
from tests.api.test_supabase_generation_rpc_contract import GENERATION_ID
from tests.api.test_training_plan_engine import TODAY


@pytest.mark.parametrize("compact,before_count,after_count", [(False, 18, 17), (True, 17, 16)])
def test_entire_overview_json_is_identical_with_one_fewer_full_analysis_read(monkeypatch, compact, before_count, after_count):
    from apps.api import management_lifecycle
    monkeypatch.setattr(management_lifecycle, "datetime", service.datetime)
    before, after = (OverviewHTTP(), OverviewHTTP()) if compact else (PlanningHTTP(), PlanningHTTP())
    before.repo = OriginalReaderOnly(before.repo)
    def request(harness):
        if compact:
            response = request_overview(monkeypatch, harness)
            assert response.status_code == 200, response.text
            return response.json()
        return read_page(monkeypatch, harness)
    expected, actual = request(before), request(after)
    assert actual == expected
    assert len(before.requests) == before_count and len(after.requests) == after_count
    assert before.analysis_reads == 2 and after.analysis_reads == 1
    assert before.response_bytes["/rpc/active_onflows_analysis"] == 2 * after.response_bytes["/rpc/active_onflows_analysis"]
    assert after.response_bytes["/rpc/active_onflows_analysis"] > 0
    difference = before.counts()-after.counts()
    assert difference == {"/rpc/active_onflows_analysis": 1}
    # The catalog keeps the exact athlete and captured generation filters.
    catalog = [r for r in after.requests if r.url.path.endswith("/onflows_analysis_generations")]
    assert len(catalog) == 1
    assert catalog[0].url.params["athlete_alias"] == "eq.ath-test"
    assert catalog[0].url.params["generation_id"] == f"eq.{GENERATION_ID}"


@pytest.mark.parametrize("nested", [False, True])
def test_capture_is_shared_by_nested_sessions_and_isolated_from_returned_mutation(nested):
    harness = PlanningHTTP()
    inner = ReadSession(harness.repo)
    scope = ReadSession(inner) if nested else inner
    returned = scope.active_analysis("ath-test")
    original = deepcopy(returned)
    returned["revision"] = 999
    returned["snapshot_payload"]["tampered"] = True
    calendar = scope.active_trainability_calendar("ath-test", date.min, TODAY)
    assert calendar["generation_id"] == original["generation_id"]
    assert calendar["revision"] == original["revision"]
    assert calendar["snapshot_payload"] == original["snapshot_payload"]
    calendar["snapshot_payload"]["tampered"] = True
    assert scope.active_analysis("ath-test") == original
    assert "tampered" not in scope.active_trainability_calendar("ath-test", date.min, TODAY)["snapshot_payload"]
    assert harness.analysis_reads == 1
    assert len(harness.requests) == 3


def test_uncaptured_calendar_uses_the_original_single_capture_without_an_optimization_probe():
    harness = PlanningHTTP()
    scope = ReadSession(harness.repo)
    result = scope.active_trainability_calendar("ath-test", date.min, TODAY)
    assert result["generation_id"] == GENERATION_ID
    assert harness.analysis_reads == 1 and len(harness.requests) == 3


@pytest.mark.parametrize("analysis", [None, {"generation_id": None, "revision": 0, "snapshot_payload": {}}])
def test_no_immutable_capture_does_not_probe_the_optional_capability(analysis):
    class Repository:
        @property
        def trainability_calendar_from_analysis(self):
            pytest.fail("There is no immutable capture to reuse")
        def active_trainability_calendar(self, *args):
            return {"original_reader": True}
    scope = ReadSession(Repository())
    if analysis is not None:
        scope._values[("active_analysis", "ath-test")] = analysis
    assert scope.active_trainability_calendar("ath-test", date.min, TODAY) == {"original_reader": True}


def test_capture_is_keyed_by_exact_athlete_and_calendar_dates():
    harness = PlanningHTTP()
    scope = ReadSession(harness.repo)
    scope.active_analysis("one&two/three?")
    scope.active_trainability_calendar("another", date.min, TODAY)
    scope.active_trainability_calendar("one&two/three?", TODAY-timedelta(days=5), TODAY)
    scope.active_trainability_calendar("one&two/three?", TODAY-timedelta(days=2), TODAY)
    assert harness.analysis_reads == 2  # The other athlete must capture independently.
    queries = [r.url.params for r in harness.requests if r.url.path.endswith("/onflows_analysis_generations")]
    assert [q["athlete_alias"] for q in queries] == ["eq.another", "eq.one&two/three?", "eq.one&two/three?"]
    assert all(q["generation_id"] == f"eq.{GENERATION_ID}" for q in queries)
    assert len(harness.requests) == 8


@pytest.mark.parametrize("change", [
    {"generation_id": None}, {"generation_id": ""}, {"generation_id": 7},
    {"revision": True}, {"revision": 0}, {"revision": -1}, {"revision": "7"},
    {"snapshot_payload": None}, {"snapshot_payload": []},
])
def test_invalid_or_legacy_capture_uses_original_reader_with_unchanged_failure(change):
    calls = []
    analysis = {"generation_id": "captured", "revision": 7, "snapshot_payload": {}, **change}
    def old_reader(*args):
        calls.append(args)
        raise PersistentStoreFailure("Original calendar failure")
    repository = SimpleNamespace(active_analysis=lambda alias: deepcopy(analysis),
        active_trainability_calendar=old_reader,
        trainability_calendar_from_analysis=lambda *args: pytest.fail("Unsupported capture must not be reused"))
    scope = ReadSession(repository)
    scope.active_analysis("ath-test")
    with pytest.raises(PersistentStoreFailure, match="Original calendar failure"):
        scope.active_trainability_calendar("ath-test", date.min, TODAY)
    assert calls == [("ath-test", date.min, TODAY)]


def test_custom_repository_without_optional_reader_keeps_its_own_behavior():
    calls = []
    repository = SimpleNamespace(
        active_analysis=lambda alias: {"generation_id": "captured", "revision": 7, "snapshot_payload": {}},
        active_trainability_calendar=lambda *args: calls.append(args) or {"custom": True})
    scope = ReadSession(repository)
    scope.active_analysis("ath-test")
    assert scope.active_trainability_calendar("ath-test", date.min, TODAY) == {"custom": True}
    assert calls == [("ath-test", date.min, TODAY)]


def test_real_legacy_repository_keeps_its_original_activity_calendar_rpc():
    class LegacyHTTP(PlanningHTTP):
        def handle(self, request):
            if request.url.path.endswith("/rpc/active_onflows_activity_calendar"):
                self.requests.append(request)
                return httpx.Response(200, json=[{**self.envelope, "activities": []}])
            return super().handle(request)
    harness = LegacyHTTP()
    harness.envelope.update(generation_id=None, revision=0)
    scope = ReadSession(harness.repo)
    scope.active_analysis("ath-test")
    result = scope.active_trainability_calendar("ath-test", date.min, TODAY)
    assert result["generation_id"] is None and result["revision"] == 0
    assert harness.analysis_reads == 2
    assert harness.counts()["/rpc/active_onflows_activity_calendar"] == 1
    assert harness.counts()["/onflows_analysis_generations"] == 0


def test_activation_after_capture_uses_old_coherent_generation_until_the_next_read_phase(monkeypatch):
    class ActivatingHTTP(PlanningHTTP):
        def handle(self, request):
            if request.url.path.endswith("/onflows_analysis_generations"):
                self.envelope.update(generation_id="33333333-3333-4333-8333-333333333333", revision=8)
            return super().handle(request)
    harness = ActivatingHTTP()
    captured = deepcopy(harness.envelope)
    result = read_page(monkeypatch, harness)
    assert harness.envelope["generation_id"] != GENERATION_ID
    assert harness.analysis_reads == 1
    assert result["outlook"]["outlook"]["source"]["generation_id"] == GENERATION_ID
    assert result["outlook"]["outlook"]["source"]["revision"] == captured["revision"]
    generation_requests = [r for r in harness.requests if r.url.path.endswith("/onflows_analysis_generations")]
    assert generation_requests[0].url.params["generation_id"] == f"eq.{GENERATION_ID}"
    assert ReadSession(harness.repo).active_analysis("ath-test")["revision"] == 8


@pytest.mark.parametrize("problem", ["generation_missing", "page_missing", "count_changed", "duplicate_identity"])
def test_known_capture_catalog_failure_is_propagated_without_switching_generation(problem):
    class FailingHTTP(PlanningHTTP):
        def handle(self, request):
            response = super().handle(request)
            path = request.url.path
            if path.endswith("/onflows_analysis_generations") and problem == "generation_missing":
                return httpx.Response(200, json=[])
            if path.endswith("/onflows_analysis_generation_activities"):
                rows = response.json()
                if problem == "page_missing":
                    rows = []
                elif problem == "count_changed":
                    rows.append(deepcopy(rows[0]))
                elif problem == "duplicate_identity":
                    rows[1] = deepcopy(rows[0])
                return httpx.Response(200, json=rows)
            return response
    harness = FailingHTTP()
    scope = ReadSession(harness.repo)
    scope.active_analysis("ath-test")
    with pytest.raises(PersistentStoreFailure):
        scope.active_trainability_calendar("ath-test", date.min, TODAY)
    assert harness.analysis_reads == 1  # A missing G1 catalog must not retry against G2.


def test_snapshot_mismatch_still_prevents_outlook_calendar_reuse():
    harness = PlanningHTTP()
    scope = ReadSession(harness.repo)
    analysis = scope.active_analysis("ath-test")
    calendar = scope.active_trainability_calendar("ath-test", date.min, TODAY)
    calendar["snapshot_payload"]["changed"] = True
    assert service._planning_calendar_from_speed_inputs(
        {"alias": "ath-test", "today": TODAY, "calendar": calendar}, "ath-test", analysis, TODAY) is None
