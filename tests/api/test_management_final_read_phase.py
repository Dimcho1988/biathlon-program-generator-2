"""Publication checks capture a new settings row after shared plan calculation."""
from copy import deepcopy
from datetime import datetime, timedelta
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api import dependencies, management_lifecycle as lifecycle, management_service as service
from apps.api.main import app
from apps.api.management_schemas import ManagementProfile
from apps.api.oauth_store import SupabasePilotRepository
from apps.api.read_session import ReadSession
from tests.api.test_management_api import ACTOR, HEADERS, PROFILE
from tests.api.test_planning_settings_batch import settings_row
from tests.api.test_supabase_generation_rpc_contract import ENCRYPTION_KEY
from tests.api.test_training_plan_engine import NOW, TODAY, Repository as HistoryRepository


class PublicationHTTP:
    """Exercise real repository parsers and route/lifecycle code at the HTTP boundary."""
    def __init__(self, *, shared_state="READY", bundle_supported=True):
        self.requests = []
        self.row, self.models, self.responses = settings_row(), [], []
        self.envelope = HistoryRepository().envelope
        self.envelope.update(analysis_as_of=TODAY.isoformat(), activated_at=NOW.isoformat())
        self.profile = ManagementProfile.model_validate(PROFILE).model_dump(mode="json")
        self.shared_state, self.bundle_supported = shared_state, bundle_supported
        self.on_claim = self.on_save = None
        self.key = self.record = None
        self.raw_plan = {
            "schema_version": "planning-draft-v1", "activation_eligible": True,
            "source": {"generation_id": self.envelope["generation_id"], "readiness_known": True},
            "days": [{"date": (TODAY+timedelta(days=i)).isoformat(), "status": "REST",
                      "explanation": "Synthetic fixture", "session": None, "sessions": []} for i in range(7)],
        }
        actual = SupabasePilotRepository(
            supabase_url="https://project.supabase.co", secret_key="sb_secret_server-key",
            encryption_key=ENCRYPTION_KEY, client=httpx.Client(transport=httpx.MockTransport(self.handler)),
            generation_reads=True)
        if bundle_supported:
            self.repository = actual
        else:
            class LegacyCapabilities:
                def __getattr__(self, name):
                    if name == "athlete_planning_settings_bundle":
                        raise AttributeError(name)
                    return getattr(actual, name)
            self.repository = LegacyCapabilities()

    def checkpoint(self):
        return {"generation_id": self.envelope["generation_id"], "models": deepcopy(self.models),
                "settings": deepcopy(self.row), "responses": deepcopy(self.responses)}

    def handler(self, request):
        path = request.url.path.removeprefix("/rest/v1")
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, dict(request.url.params), deepcopy(body)))
        if path == "/onflows_athlete_settings":
            data = [{key: deepcopy(self.row[key]) for key in request.url.params["select"].split(",") if key in self.row}]
        elif path == "/onflows_management_entries":
            data = [{"kind": "PROFILE", "entry_key": "management-profile-v1", "revision": 1, "payload": self.profile}]
        elif path in {"/onflows_model_entries", "/onflows_response_entries"}:
            data = deepcopy(self.models if path == "/onflows_model_entries" else self.responses)
        elif path == "/rpc/active_onflows_analysis":
            data = [deepcopy(self.envelope)]
        elif path == "/rpc/onflows_management_checkpoint":
            data = self.checkpoint()
        elif path == "/onflows_analysis_generations":
            data = [{"activity_set_generation_id": None, "activity_count": 1}]
        elif path == "/onflows_analysis_generation_activities":
            data = [{"activity_ref": "fixture-activity", "catalog_payload": {"sport": "Run", "duration_s": 3600},
                     "local_date": (TODAY-timedelta(days=2)).isoformat(), "start_at_utc": NOW.isoformat(),
                     "shadow_run_key": None, "canonical_run_key": None, "input_key": None}]
        elif path == "/onflows_management_plan_state":
            data = [{"revision": self.record["revision"], "record": deepcopy(self.record)}]
        elif path == "/onflows_management_plan_revisions":
            data = [{k: self.record[k] for k in ("revision", "operation", "recorded_at")}
                    | {k: self.record["payload"].get(k) for k in ("status", "changes", "reason")}]
        elif path == "/rpc/onflows_planning_computation":
            if body["p_operation"] == "CLAIM":
                assert body["p_key"] == self.key, "Fixture must join the exactly matching input key"
                if self.on_claim:
                    self.on_claim(self)
                data = {"state": self.shared_state}
                if self.shared_state == "READY":
                    data["result"] = deepcopy(self.raw_plan)
            else:
                assert body["p_operation"] == "COMPLETE"
                data = {"state": "READY"}
        elif path == "/rpc/save_onflows_management_plan_compact":
            if self.on_save:
                self.on_save(self)
            assert body["p_expected_revision"] == self.record["revision"]
            if body["p_checkpoint"] != self.checkpoint():
                data = {"conflict": True, "reason": "INPUTS_CHANGED"}
            else:
                self.record = {"revision": self.record["revision"]+1, "operation": body["p_operation"],
                               "recorded_at": NOW.isoformat(), "payload": deepcopy(body["p_payload"])}
                data = {"saved": True, "revision": self.record["revision"], "recorded_at": NOW.isoformat()}
        else:
            raise AssertionError((request.method, path))
        return httpx.Response(200, json=data)

    def run(self, monkeypatch, *, emulate_raw_after=False):
        _, _, _, fingerprint, approval = lifecycle._context(self.repository, "ath-test", NOW)
        before = service.input_state(self.repository, "ath-test", evaluated_at=NOW, include_response=True)
        self.key = service._hash({"athlete": "ath-test", "inputs": before, "profile": self.profile,
            "profile_revision": 1, "start_date": TODAY.isoformat(), "decisions": {}, "locked_day": None})
        self.record = {"revision": 1, "operation": "ACTIVATE", "recorded_at": NOW.isoformat(), "payload": {
            "status": "ACTIVE", "mode": "REVIEW", "approved_by": str(ACTOR), "approved_profile_revision": 1,
            "approved_settings_fingerprint": approval, "processed_input_fingerprint": fingerprint,
            "evaluated_on": TODAY.isoformat(), "plan": deepcopy(self.raw_plan), "proposal": None,
            "origin": {"start_date": TODAY.isoformat()}, "decisions": {}, "outcomes": [], "changes": [], "reason": "fixture"}}
        self.requests.clear()
        captured_scopes = []
        original_input_state = service.input_state

        def input_state(repository, *args, **kwargs):
            captured_scopes.append(repository)
            if len(captured_scopes) == 3:
                assert isinstance(repository, ReadSession)
                assert repository is not captured_scopes[0] and repository is not captured_scopes[1]
                assert repository._repository is self.repository
                if emulate_raw_after:
                    repository = self.repository  # Reproduce the former final-check implementation.
            return original_input_state(repository, *args, **kwargs)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW.astimezone(tz)

        def calculate(*args, **kwargs):
            assert self.shared_state == "OWNED", "Warm READY results must not trigger engine work"
            return deepcopy(self.raw_plan)

        with monkeypatch.context() as context:
            context.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
            context.setattr(dependencies, "repository", lambda: self.repository)
            context.setattr(service, "datetime", Clock)
            context.setattr(lifecycle, "datetime", Clock)
            context.setattr(service, "input_state", input_state)
            context.setattr(service.training_plan_engine, "generate_plan", calculate)
            return TestClient(app).post("/api/v2/athlete/management/action",
                json={"action": "REFRESH", "expected_revision": 1}, headers=HEADERS)


def test_fresh_final_settings_row_removes_three_requests_with_identical_complete_http_json(monkeypatch):
    former, current = PublicationHTTP(), PublicationHTTP()
    expected, actual = former.run(monkeypatch, emulate_raw_after=True), current.run(monkeypatch)
    assert actual.status_code == expected.status_code == 200
    assert actual.json() == expected.json()
    assert len(former.requests) == 26 and len(current.requests) == 23
    final = current.requests[11:15]
    assert [request[1] for request in final] == ["/onflows_athlete_settings", "/rpc/active_onflows_analysis",
        "/onflows_model_entries", "/onflows_response_entries"]
    assert set(final[0][2]["select"].split(",")) == set(settings_row())
    assert current.record["payload"]["status"] == "REVIEW_REQUIRED"
    assert actual.json()["active"]["revision"] == 2


@pytest.mark.parametrize("shared_state", ["READY", "OWNED"])
@pytest.mark.parametrize("field,value", [
    ("hrmax_bpm", 201), ("planning_calendar", None), ("planning_profile", None),
    ("mesocycle_accent_preferences", None), ("timezone", "Pacific/Honolulu"),
    ("hr_zone_bounds", [100, 120, 140, 160, 180, 195]),
])
def test_settings_edit_while_computing_or_joining_ready_result_prevents_publication(monkeypatch, shared_state, field, value):
    transport = PublicationHTTP(shared_state=shared_state)
    transport.on_claim = lambda state: state.row.update({field: value})
    response = transport.run(monkeypatch)
    assert response.status_code == 409
    assert "inputs changed" in response.json()["detail"]
    assert not any(path == "/rpc/save_onflows_management_plan_compact" for _, path, _, _ in transport.requests)
    assert transport.record["revision"] == 1


@pytest.mark.parametrize("kind", ["generation", "model", "response"])
def test_fresh_final_phase_still_reads_every_non_settings_input(monkeypatch, kind):
    transport = PublicationHTTP()
    def change(state):
        if kind == "generation":
            state.envelope.update(generation_id="new-generation", revision=2)
        elif kind == "model":
            state.models = [{"kind": "RECOVERY", "entry_key": "changed", "revision": 1, "payload": {}}]
        else:
            state.responses = [{"kind": "DAILY", "entry_key": "changed", "revision": 1, "payload": {}}]
    transport.on_claim = change
    response = transport.run(monkeypatch)
    assert response.status_code == 409 and transport.record["revision"] == 1
    assert not any(path == "/rpc/save_onflows_management_plan_compact" for _, path, _, _ in transport.requests)


def test_publication_checkpoint_remains_effective_after_the_fresh_final_read(monkeypatch):
    transport = PublicationHTTP()
    transport.on_save = lambda state: state.row.update(hrmax_bpm=201)
    response = transport.run(monkeypatch)
    assert response.status_code == 409 and transport.record["revision"] == 1
    assert response.json()["detail"] == "The plan or its inputs changed; reload and try again"


def test_final_read_falls_back_to_individual_readers_for_legacy_repository(monkeypatch):
    former, current = PublicationHTTP(bundle_supported=False), PublicationHTTP(bundle_supported=False)
    expected, actual = former.run(monkeypatch, emulate_raw_after=True), current.run(monkeypatch)
    assert actual.status_code == expected.status_code == 200
    assert actual.json() == expected.json()
    assert len(former.requests) == len(current.requests) == 32
