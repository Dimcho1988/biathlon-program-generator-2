"""Outlook reuses one coherent calendar without changing any published field."""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api import dependencies, main, management_service as service, model_service
from apps.api.management_schemas import LoadProgression, ManagementProfile, PlanningControls
from apps.api.management_store import PROFILE_KEY
from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from apps.api.read_session import ReadSession
from tests.api.test_management_api import HEADERS
from tests.api.test_supabase_generation_rpc_contract import ENCRYPTION_KEY, GENERATION_ID
from tests.api.test_training_plan_engine import NOW, TODAY, Repository, profile


class PlanningHTTP:
    """Generation-backed production adapter with 133 activities and 112 TI keys."""

    def __init__(self):
        self.requests = []
        self.analysis_reads = 0
        self.activate_on_read = None
        self.activate_after_catalog = False
        self.envelope = deepcopy(Repository().envelope)
        self.envelope.update(generation_id=GENERATION_ID, revision=7,
                             analysis_as_of=NOW.isoformat(), activated_at=NOW.isoformat())
        self.envelope.pop("activities")
        self.saved_profile = ManagementProfile.model_validate(profile(
            discipline="5000 m", planning_controls=PlanningControls().model_dump(mode="json"),
            load_progression=LoadProgression().model_dump(mode="json"),
        )).model_dump(mode="json")
        self.record = {"revision": 9, "operation": "REVIEW", "recorded_at": NOW.isoformat(),
                       "payload": {"status": "REVIEW_REQUIRED", "processed_input_fingerprint": "old",
                                   "changes": [], "reason": "awaiting review"}}
        self.catalog = []
        self.summaries = {}
        for i in range(133):
            day = TODAY-timedelta(days=89-i % 89)
            key = f"{i+1:064x}" if i < 112 else None
            reference = f"activity-{i}"
            self.catalog.append({
                "activity_ref": reference, "catalog_payload": {"sport": "Run", "name": reference,
                    "duration_min": 20., "quality_status": "valid", "hr_coverage_percent": 100.},
                "shadow_run_key": key, "canonical_run_key": None, "input_key": None,
                "local_date": day.isoformat(), "start_at_utc": f"{day}T10:00:00Z",
            })
            if key:
                self.summaries[key] = {"run_key": key, "activity_ref": reference,
                                       "trainability_index": None}
        self.catalog.sort(key=lambda row: (row["start_at_utc"], row["activity_ref"]))
        self.repo = SupabasePilotRepository(
            supabase_url="https://outlook-test.supabase.co", secret_key="sb_secret_test-only",
            encryption_key=ENCRYPTION_KEY,
            client=httpx.Client(transport=httpx.MockTransport(self.handle)), generation_reads=True,
        )

    def handle(self, request):
        self.requests.append(request)
        path, query = request.url.path.removeprefix("/rest/v1"), request.url.params
        if path == "/rpc/active_onflows_analysis":
            self.analysis_reads += 1
            if self.analysis_reads == self.activate_on_read:
                self.envelope.update(generation_id="33333333-3333-4333-8333-333333333333", revision=8)
            rows = [self.envelope]
        elif path == "/onflows_athlete_settings":
            rows = [{"hr_zone_bounds": [100, 125, 145, 160, 175, 190], "hrmax_bpm": 190,
                     "timezone": "Europe/Sofia", "hr_zone_source": "MANUAL", "hr_zone_percentages": None,
                     "planning_profile": None, "planning_calendar": None, "mesocycle_accent_preferences": None}]
        elif path == "/onflows_management_entries":
            rows = [{"kind": "PROFILE", "entry_key": PROFILE_KEY, "revision": 1,
                     "payload": self.saved_profile}] if query.get("kind") == "eq.PROFILE" else []
        elif path == "/onflows_management_plan_state":
            rows = [{"revision": self.record["revision"], "record": self.record}]
        elif path == "/onflows_management_plan_revisions":
            rows = [{k: self.record[k] for k in ("revision", "operation", "recorded_at")}
                    | {k: self.record["payload"][k] for k in ("status", "changes", "reason")}]
            if "proposal_anchor:" in query["select"] or "proposal_memory:" in query["select"]:
                rows = []
        elif path in ("/onflows_model_entries", "/onflows_response_entries"):
            rows = []
        elif path == "/onflows_analysis_generations":
            rows = [{"activity_count": len(self.catalog), "activity_set_generation_id": GENERATION_ID}]
        elif path == "/onflows_analysis_generation_activities":
            offset = int(query["offset"])
            rows = self.catalog[offset:offset+int(query["limit"])]
            if self.activate_after_catalog:
                self.envelope.update(generation_id="33333333-3333-4333-8333-333333333333", revision=8)
        elif path == "/onflows_activity_run_summaries":
            keys = query["run_key"].removeprefix("in.(").removesuffix(")").split(",")
            rows = [self.summaries[key] for key in keys]
        elif path == "/rpc/onflows_sync_state":
            rows = [{"status": "SUCCEEDED", "active_revision": 7, "request_sequence": 1,
                     "active_generation_id": GENERATION_ID}]
        else:
            raise AssertionError(f"Unexpected read: {request.method} {path}")
        assert request.method == ("POST" if path.startswith("/rpc/") else "GET")
        return httpx.Response(200, json=deepcopy(rows))

    def counts(self):
        return Counter(request.url.path.removeprefix("/rest/v1") for request in self.requests)


@pytest.fixture(autouse=True)
def fixed_clocks(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(service, "datetime", Clock)
    monkeypatch.setattr(model_service, "datetime", Clock)


def read_page(monkeypatch, harness):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(dependencies, "repository", lambda: harness.repo)
    response = TestClient(main.app).get("/api/v2/athlete/management/view?view=overview", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def test_full_overview_is_identical_with_eighteen_instead_of_twenty_one_store_reads(monkeypatch):
    baseline = PlanningHTTP()
    reuse = service._planning_calendar_from_speed_inputs
    monkeypatch.setattr(service, "_planning_calendar_from_speed_inputs", lambda *args: None)
    expected = read_page(monkeypatch, baseline)
    assert len(baseline.requests) == 21
    monkeypatch.setattr(service, "_planning_calendar_from_speed_inputs", reuse)
    optimized = PlanningHTTP()
    actual = read_page(monkeypatch, optimized)
    assert actual == expected
    assert len(optimized.requests) == 18
    assert baseline.counts()-optimized.counts() == Counter({
        "/rpc/active_onflows_analysis": 1, "/onflows_analysis_generations": 1,
        "/onflows_analysis_generation_activities": 1})
    assert optimized.counts()["/onflows_activity_run_summaries"] == 3
    assert actual["outlook"]["outlook"]["source"]["generation_id"] == GENERATION_ID


def captured_inputs(activities=None):
    harness = PlanningHTTP()
    envelope = {**deepcopy(harness.envelope), "activities": [] if activities is None else activities}
    return {"alias": "ath-test", "today": TODAY, "calendar": envelope}, deepcopy(harness.envelope)


def test_reuse_keeps_inclusive_date_boundaries_full_metadata_order_and_inputs():
    activities = [{"activity_ref": name, "local_date": (TODAY-timedelta(days=age)).isoformat(),
                   "sport": "Run", "extra_metadata": {"nested": [name]}}
                  for name, age in [("old", 90), ("first", 89), ("middle", 40), ("today", 0)]]
    inputs, analysis = captured_inputs(activities)
    before = deepcopy((inputs, analysis))
    result = service._planning_calendar_from_speed_inputs(inputs, "ath-test", analysis, TODAY)
    assert result == {**inputs["calendar"], "activities": activities[1:]}
    assert (inputs, analysis) == before
    assert result["activities"][0]["extra_metadata"] == {"nested": ["first"]}


def test_empty_calendar_is_valid_reusable_evidence():
    inputs, analysis = captured_inputs()
    result = service._planning_calendar_from_speed_inputs(inputs, "ath-test", analysis, TODAY)
    assert result is not None and result["activities"] == []


@pytest.mark.parametrize("change", [
    "alias", "local_day", "legacy", "revision", "boolean_revision", "analysis_as_of", "activated_at",
    "snapshot", "source_date", "future_source", "source_order", "missing_activities",
    "invalid_date", "noncanonical_date", "future_activity", "missing_reference", "duplicate_reference",
])
def test_unreliable_capture_is_never_reused(change):
    inputs, analysis = captured_inputs([{"activity_ref": "one", "local_date": TODAY.isoformat()}])
    calendar = inputs["calendar"]
    if change == "alias": inputs["alias"] = "another-athlete"
    elif change == "local_day": inputs["today"] = TODAY-timedelta(days=1)
    elif change == "legacy":
        calendar["generation_id"] = analysis["generation_id"] = None
    elif change == "revision": calendar["revision"] += 1
    elif change == "boolean_revision": calendar["revision"] = analysis["revision"] = True
    elif change in ("analysis_as_of", "activated_at"): calendar[change] = "changed"
    elif change == "snapshot": calendar["snapshot_payload"]["changed"] = True
    elif change in ("source_date", "future_source", "source_order"):
        source = calendar["snapshot_payload"]["load_history"]
        source["period_end"] = ("invalid" if change == "source_date" else
                                (TODAY+timedelta(days=1)).isoformat() if change == "future_source" else
                                (date.fromisoformat(source["period_start"])-timedelta(days=1)).isoformat())
        analysis["snapshot_payload"] = deepcopy(calendar["snapshot_payload"])
    elif change == "missing_activities": calendar.pop("activities")
    elif change == "invalid_date": calendar["activities"][0]["local_date"] = "2026-02-30"
    elif change == "noncanonical_date": calendar["activities"][0]["local_date"] = TODAY.strftime("%Y%m%d")
    elif change == "future_activity": calendar["activities"][0]["local_date"] = (TODAY+timedelta(days=1)).isoformat()
    elif change == "missing_reference": calendar["activities"][0].pop("activity_ref")
    elif change == "duplicate_reference": calendar["activities"].append(deepcopy(calendar["activities"][0]))
    assert service._planning_calendar_from_speed_inputs(inputs, "ath-test", analysis, TODAY) is None


def test_generation_activation_between_profile_and_speed_inputs_keeps_original_fallback(monkeypatch):
    baseline = PlanningHTTP()
    baseline.activate_on_read = 2
    reuse = service._planning_calendar_from_speed_inputs
    monkeypatch.setattr(service, "_planning_calendar_from_speed_inputs", lambda *args: None)
    expected = read_page(monkeypatch, baseline)
    monkeypatch.setattr(service, "_planning_calendar_from_speed_inputs", reuse)
    optimized = PlanningHTTP()
    optimized.activate_on_read = 2
    assert read_page(monkeypatch, optimized) == expected
    # The old profile analysis and newly activated speed calendar are never
    # combined. Both paths perform the original fallback read and discard it.
    assert optimized.counts() == baseline.counts()
    assert optimized.analysis_reads == 3


def test_source_day_change_uses_original_fallback_and_propagates_its_failure(monkeypatch):
    harness = PlanningHTTP()
    inputs_reader = model_service.speed_view_inputs

    def yesterday(repository, alias):
        inputs = inputs_reader(repository, alias)
        inputs["today"] -= timedelta(days=1)
        return inputs

    monkeypatch.setattr(model_service, "speed_view_inputs", yesterday)
    calls = []

    def unavailable(*args):
        calls.append(args)
        raise PersistentStoreFailure("Planning catalog unavailable")

    monkeypatch.setattr(harness.repo, "active_planning_calendar", unavailable)
    with pytest.raises(PersistentStoreFailure, match="Planning catalog unavailable"):
        service.outlook(ReadSession(harness.repo, planning_settings_bundle=True), "ath-test", now=NOW)
    assert calls == [("ath-test", TODAY-timedelta(days=89), TODAY)]


def test_activation_after_coherent_capture_returns_one_pinned_generation(monkeypatch):
    harness = PlanningHTTP()
    harness.activate_after_catalog = True
    observed = []
    planning_source = service.training_plan_engine.planning_source_details

    def capture(repository, alias, profile, settings, envelope, today, **kwargs):
        observed.append(deepcopy(envelope))
        return planning_source(repository, alias, profile, settings, envelope, today, **kwargs)

    monkeypatch.setattr(service.training_plan_engine, "planning_source_details", capture)
    result = read_page(monkeypatch, harness)
    assert harness.envelope["generation_id"] != GENERATION_ID
    assert result["outlook"]["outlook"]["source"]["generation_id"] == GENERATION_ID
    assert observed[0]["generation_id"] == GENERATION_ID
    assert observed[0]["snapshot_payload"] == Repository().envelope["snapshot_payload"]
    assert len(observed[0]["activities"]) == 133
    assert len(harness.requests) == 18 and harness.analysis_reads == 2


def test_same_speed_inputs_are_used_once_and_stale_model_still_uses_manual_race_duration(monkeypatch):
    from tests.api.test_training_plan_engine import supported_speed

    harness = PlanningHTTP()
    harness.saved_profile["race_duration_min"] = 25.
    inputs_reader, model_reader = model_service.speed_view_inputs, model_service.speed_view
    inputs_calls = []

    def read_inputs(repository, alias):
        inputs = inputs_reader(repository, alias)
        inputs_calls.append(inputs)
        return inputs

    def stale_view(repository, alias, sport, *, _inputs):
        assert _inputs is inputs_calls[0]
        view = model_reader(repository, alias, sport, _inputs=_inputs)
        return {**view, **supported_speed(_inputs["settings"]), "active_test_count": 2,
                "dosing_model": None, "preliminary_capacity": None,
                "source_generation_id": "older-generation", "source_revision": 6,
                "points": [{"distance_m": 1000., "duration_s": 240.},
                           {"distance_m": 10000., "duration_s": 3000.}]}

    monkeypatch.setattr(model_service, "speed_view_inputs", read_inputs)
    monkeypatch.setattr(model_service, "speed_view", stale_view)
    result = read_page(monkeypatch, harness)["outlook"]["outlook"]["race_duration"]
    assert len(inputs_calls) == 1
    assert result["source"] == "MANUAL" and result["duration_min"] == 25.
    assert result["reason"] == "MODEL_UPDATED"


def test_custom_repository_keeps_its_original_speed_view_and_calendar_contract(monkeypatch):
    from types import SimpleNamespace
    from tests.api.test_training_plan_engine import reference_speed

    repo = Repository()
    repo.active_analysis = lambda _: deepcopy(repo.envelope)
    saved = PlanningHTTP().saved_profile
    monkeypatch.setattr(service, "ManagementStore", lambda _: SimpleNamespace(
        profile=lambda _: {"configured": True, "revision": 1, "profile": saved},
        progression_reference=lambda _: None, learning_memory=lambda _: None))
    monkeypatch.setattr(model_service, "speed_view", reference_speed)
    monkeypatch.setattr(model_service, "speed_view_inputs", lambda *args: pytest.fail(
        "Custom speed models must not acquire additional prerequisites"))
    assert service.outlook(repo, "athlete", now=NOW)["configured"] is True
