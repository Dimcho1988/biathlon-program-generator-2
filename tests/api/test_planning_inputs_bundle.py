"""Production-capability HTTP parity, phase isolation and bounded lazy inputs."""
from copy import deepcopy
from datetime import datetime
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from apps.api import dependencies, management_lifecycle as lifecycle, management_service as service
from apps.api.main import app
from apps.api.management_store import ManagementStore
from apps.api.model_service import ModelStore
from apps.api.oauth_store import PersistentStoreFailure, SupabasePilotRepository
from apps.api.planning_input_bundle import PlanningInputBundle
from apps.api.read_session import ReadSession
from apps.api.request_metrics import StoreMetrics, current_metrics
from apps.api.response_monitoring import latest_entries
from apps.api.response_service import ResponseStore
from tests.api.test_management_api import HEADERS
from tests.api.test_management_initial_read_phase import InitialPhaseHTTP
from tests.api.test_supabase_generation_rpc_contract import ENCRYPTION_KEY
from tests.api.test_training_plan_engine import NOW


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class BundleHTTP(InitialPhaseHTTP):
    def __init__(self, *, inputs_enabled=True, **kwargs):
        super().__init__(**kwargs)
        self.on_bundle, self.bundle_error, self.bundle_transform = None, None, None
        self.repository = SupabasePilotRepository(supabase_url="https://project.supabase.co",
            secret_key="sb_secret_server-key", encryption_key=ENCRYPTION_KEY,
            client=httpx.Client(transport=httpx.MockTransport(self.handler)), generation_reads=True,
            planning_inputs_reads=inputs_enabled)

    def checkpoint(self):
        return {**super().checkpoint(), "analysis_revision": self.envelope["revision"]}

    def value(self, alias, mode="FULL", hints=()):
        metadata = {k: self.envelope.get(k) for k in ("generation_id", "revision", "analysis_as_of", "activated_at")}
        matched = mode == "HINTED" and metadata["generation_id"] and metadata["revision"] > 0 and any(
            metadata["generation_id"] == h.get("generation_id") and metadata["revision"] == h.get("analysis_revision")
            and metadata["analysis_as_of"] == h.get("analysis_as_of") for h in hints)
        # The SQL contract separately proves these exact latest projections.
        models = {}
        for row in sorted(self.models, key=lambda r: (r["kind"], r["entry_key"], -r["revision"])):
            models.setdefault((row["kind"], row["entry_key"]), deepcopy(row))
        responses = sorted(self.responses, key=lambda r: (r["kind"], r["entry_key"], -r["revision"]))
        responses.sort(key=lambda r: r["recorded_at"], reverse=True)
        return {"schema_version": "planning-inputs-v1", "athlete_alias": alias,
            "settings_rows": [deepcopy(self.row)], "profile_rows": [{"kind": "PROFILE",
                "entry_key": "management-profile-v1", "revision": self.profile_revision, "payload": deepcopy(self.profile)}],
            "model_entries": list(models.values()), "model_history_exhausted": False,
            "response_entries": list(latest_entries(responses).values()), "response_history_exhausted": False,
            "analysis": {"mode": "METADATA" if matched else "FULL",
                         "row": metadata if matched else {**metadata,
                             "snapshot_payload": deepcopy(self.envelope["snapshot_payload"])}}}

    def handler(self, request):
        path = request.url.path.removeprefix("/rest/v1")
        if path == "/rpc/read_onflows_planning_inputs":
            self.requests.append((request.method, path, dict(request.url.params), None))
            if self.bundle_error:
                return httpx.Response(self.bundle_error, json={"code": "XX000", "message": "synthetic failure"})
            phase = sum(p == path for _, p, _, _ in self.requests)
            if self.on_bundle:
                self.on_bundle(self, phase)
            value = self.value(request.url.params["p_alias"], request.url.params["p_analysis_mode"],
                               json.loads(request.url.params["p_analysis_hints"]))
            return httpx.Response(200, json=self.bundle_transform(value) if self.bundle_transform else value)
        if path == "/onflows_management_plan_state" and "fingerprint:" in request.url.params.get("select", ""):
            payload = self.record["payload"]
            record = {"revision": self.record["revision"], "fingerprint": payload["processed_input_fingerprint"],
                      "status": payload["status"]}
            for prefix in ("plan", "proposal"):
                snapshot = (payload.get(prefix) or {}).get("input_snapshot") or {}
                record.update({f"{prefix}_{k}": snapshot.get(k) for k in
                    ("generation_id", "analysis_revision", "analysis_as_of", "snapshot_fingerprint")})
            self.requests.append((request.method, path, dict(request.url.params), None))
            return httpx.Response(200, json=[{"revision": self.record["revision"], "record": record}])
        if path == "/rpc/save_onflows_management_plan_compact" and self.on_save:
            callback, self.on_save = self.on_save, None
            callback(self)  # Before the independent publication profile CAS.
        return super().handler(request)

    def poll(self, monkeypatch):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW.astimezone(tz)
        with monkeypatch.context() as context:
            context.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
            context.setattr(dependencies, "repository", lambda: self.repository)
            context.setattr(lifecycle, "datetime", Clock)
            return TestClient(app).get("/api/v2/athlete/management/state", headers=HEADERS)


@pytest.mark.parametrize("shared_state", ["READY", "OWNED"])
def test_three_fresh_bundles_keep_complete_http_json_key_and_fingerprint_with_eleven_fewer_reads(monkeypatch, shared_state):
    old, new = BundleHTTP(inputs_enabled=False, shared_state=shared_state), BundleHTTP(shared_state=shared_state)
    expected, actual = old.run(monkeypatch), new.run(monkeypatch)
    assert expected.status_code == actual.status_code == 200
    assert canonical(actual.json()) == canonical(expected.json())
    assert new.key == old.key
    assert len(old.requests)-len(new.requests) == 11
    assert len(new.requests) == (9 if shared_state == "READY" else 10)
    assert sum(path == "/rpc/read_onflows_planning_inputs" for _, path, _, _ in new.requests) == 3
    assert not any(path in {"/onflows_athlete_settings", "/onflows_model_entries", "/onflows_response_entries",
                           "/onflows_management_entries", "/rpc/active_onflows_analysis"}
                   for _, path, _, _ in new.requests)


def test_poll_uses_metadata_for_matching_hint_and_same_single_full_bundle_when_analysis_changes(monkeypatch):
    old, harness = BundleHTTP(inputs_enabled=False), BundleHTTP()
    assert old.run(monkeypatch).status_code == 200
    assert harness.run(monkeypatch).status_code == 200
    old.requests.clear()
    harness.requests.clear()
    expected = old.poll(monkeypatch)
    response = harness.poll(monkeypatch)
    assert response.status_code == 200 and response.json() == {"revision": 2, "stale": False}
    assert canonical(response.json()) == canonical(expected.json())
    assert len(old.requests) == 6 and len(harness.requests) == 2
    call = harness.requests[-1]
    hints = json.loads(call[2]["p_analysis_hints"])
    assert call[2]["p_analysis_mode"] == "HINTED" and hints
    metadata = harness.value("ath-test", "HINTED", hints)["analysis"]
    assert metadata["mode"] == "METADATA" and "snapshot_payload" not in metadata["row"]
    harness.envelope["revision"] += 1
    harness.requests.clear()
    response = harness.poll(monkeypatch)
    assert response.status_code == 200 and response.json() == {"revision": 2, "stale": True}
    assert len(harness.requests) == 2
    assert harness.value("ath-test", "HINTED", hints)["analysis"]["mode"] == "FULL"


@pytest.mark.parametrize("kind", ["settings", "timezone", "calendar", "preferences", "accent",
                                  "generation", "model", "response", "profile"])
def test_edit_during_compute_is_seen_by_new_after_phase_and_cannot_publish_stale_inputs(monkeypatch, kind):
    harness = BundleHTTP()
    def change(state):
        if kind == "settings": state.row["hrmax_bpm"] = 201
        elif kind == "timezone": state.row["timezone"] = "Pacific/Honolulu"
        elif kind in {"calendar", "preferences", "accent"}:
            state.row[{"calendar": "planning_calendar", "preferences": "planning_profile",
                       "accent": "mesocycle_accent_preferences"}[kind]] = None
        elif kind == "generation": state.envelope["revision"] += 1
        elif kind == "profile": state.profile_revision += 1
        elif kind == "model": state.models = [{"kind": "SPEED_TEST", "entry_key": "new", "revision": 1,
                                               "payload": {}, "recorded_at": NOW.isoformat()}]
        else: state.responses = [{"kind": "DAILY", "entry_key": "new", "revision": 1, "payload": {},
                                  "recorded_at": NOW.isoformat(), "observed_date": NOW.date().isoformat()}]
    harness.on_claim = change
    response = harness.run(monkeypatch)
    assert response.status_code == 409 and harness.record["revision"] == 1
    writes = [path for _, path, _, _ in harness.requests if path == "/rpc/save_onflows_management_plan_compact"]
    assert len(writes) == (1 if kind == "profile" else 0)


def test_new_final_phase_observes_edit_after_publication_without_reusing_the_saved_candidate(monkeypatch):
    harness = BundleHTTP()
    def after_save(state, phase):
        if phase == 3:
            state.row["hrmax_bpm"] = 201
    harness.on_bundle = after_save
    response = harness.run(monkeypatch)
    assert response.status_code == 200 and response.json()["active"]["stale"] is True
    assert harness.record["revision"] == 2
    assert harness.record["payload"]["proposal"]["input_snapshot"]["physiology"]["hrmax_bpm"] != 201


@pytest.mark.parametrize("section", ["model", "response"])
def test_exhausted_history_fails_only_when_consumed_and_never_falls_back_to_raw_history(section):
    harness = BundleHTTP()
    harness.bundle_transform = lambda value: {**value, f"{section}_history_exhausted": True}
    phase = ReadSession.planning(harness.repository)
    phase.capture_planning_inputs("ath&one/two?")
    assert ManagementStore(phase).profile("ath&one/two?")["configured"]
    assert phase.athlete_settings("ath&one/two?")
    with pytest.raises(PersistentStoreFailure, match="bounded read"):
        (ModelStore(phase) if section == "model" else ResponseStore(phase)).entries("ath&one/two?")
    assert len(harness.requests) == 1
    assert harness.requests[0][2]["p_alias"] == "ath&one/two?"


@pytest.mark.parametrize("error", ["http", "shape", "foreign_alias"])
def test_real_bundle_failure_is_503_without_a_fallback_query_or_a_write(monkeypatch, error):
    harness = BundleHTTP()
    # Warm the parent fixture before breaking only the actual route's first
    # phase. The initial pointer/checkpoint calls establish that route boundary.
    def fail_at_checkpoint(request):
        if request.url.path.endswith("/rpc/onflows_management_checkpoint"):
            if error == "http": harness.bundle_error = 503
            elif error == "shape": harness.bundle_transform = lambda value: {}
            else: harness.bundle_transform = lambda value: {**value, "athlete_alias": "foreign"}
        return harness.handler(request)
    harness.repository._client = httpx.Client(transport=httpx.MockTransport(fail_at_checkpoint))
    response = harness.run(monkeypatch)
    assert response.status_code == 503 and harness.record["revision"] == 1
    assert [path for _, path, _, _ in harness.requests] == ["/onflows_management_plan_state",
        "/rpc/onflows_management_checkpoint", "/rpc/read_onflows_planning_inputs"]


def test_latest_semantic_sections_equal_existing_client_selection_and_do_not_replace_raw_history():
    old, new = BundleHTTP(inputs_enabled=False), BundleHTTP()
    model = lambda key, revision, stamp: {"kind": "SPEED_TEST", "entry_key": key, "revision": revision,
        "payload": {"nested": [key, revision]}, "recorded_at": stamp}
    response = lambda key, revision, stamp: {"kind": "DAILY", "entry_key": key, "revision": revision,
        "payload": {"nested": [key, revision]}, "recorded_at": stamp, "observed_date": "2026-09-21"}
    for harness in (old, new):
        harness.models = [model("a", 2, "2026-09-20T00:00:00Z"), model("a", 1, "2026-09-21T00:00:00Z"),
                          model("b", 1, "2026-09-21T00:00:00Z")]
        # Newer revision has an older recorded_at, but retains the identity's
        # first occurrence position before b. Sorting winner timestamps fails.
        harness.responses = [response("a", 1, "2026-09-22T00:00:00Z"), response("b", 1, "2026-09-21T00:00:00Z"),
                             response("a", 2, "2026-09-20T00:00:00Z")]
    phase = ReadSession.planning(new.repository)
    phase.capture_planning_inputs("ath-test")
    models, responses = ModelStore(phase).entries("ath-test"), ResponseStore(phase).entries("ath-test")
    assert canonical(models) == canonical(ModelStore(old.repository).entries("ath-test"))
    assert canonical(responses) == canonical(ResponseStore(old.repository).entries("ath-test"))
    assert [(r["entry_key"], r["revision"]) for r in responses] == [("a", 2), ("b", 1)]
    models[0]["payload"]["nested"].append("mutation")
    responses[0]["payload"]["nested"].append("mutation")
    assert "mutation" not in ModelStore(phase).entries("ath-test")[0]["payload"]["nested"]
    assert "mutation" not in ResponseStore(phase).entries("ath-test")[0]["payload"]["nested"]
    assert len(new.requests) == 1
    path = "/onflows_model_entries?select=kind,entry_key,revision,payload,recorded_at&athlete_alias=eq.ath-test"
    raw = phase._json(phase._request("GET", path+"&order=kind.asc,entry_key.asc,revision.desc&limit=1000&offset=0"))
    assert len(raw) == 3 and raw == new.models  # Original raw transport remains available.


def test_captured_bundle_is_separate_per_athlete_and_new_phase_and_protects_analysis_aliasing():
    harness = BundleHTTP()
    original = harness.value
    def value(alias, *args):
        result = original(alias, *args)
        if alias == "other":
            result["settings_rows"][0]["hrmax_bpm"] = 201
            result["analysis"]["row"]["generation_id"] = "other-generation"
        return result
    harness.value = value
    phase = ReadSession.planning(harness.repository)
    for alias in ("ath-test", "other"):
        phase.capture_planning_inputs(alias)
    assert phase.athlete_settings("ath-test").hrmax_bpm != phase.athlete_settings("other").hrmax_bpm
    assert phase.active_analysis("other")["generation_id"] == "other-generation"
    captured = phase.active_analysis("ath-test")
    captured["snapshot_payload"]["mutation"] = True
    harness.envelope["snapshot_payload"]["new-live-data"] = True
    assert "mutation" not in phase.active_analysis("ath-test")["snapshot_payload"]
    assert "new-live-data" not in phase.active_analysis("ath-test")["snapshot_payload"]
    fresh = ReadSession.planning(harness.repository)
    fresh.capture_planning_inputs("ath-test")
    assert fresh.active_analysis("ath-test")["snapshot_payload"]["new-live-data"] is True
    assert len(harness.requests) == 3


def test_runtime_environment_enables_bundle_capability_and_direct_constructor_keeps_explicit_legacy_opt_in():
    harness = BundleHTTP()
    runtime = SupabasePilotRepository.from_environment({"SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_server-key", "ONFLOWS_TOKEN_ENCRYPTION_KEY": ENCRYPTION_KEY},
        client=httpx.Client(transport=httpx.MockTransport(harness.handler)))
    phase = ReadSession.planning(runtime)
    phase.capture_planning_inputs("ath-test")
    assert phase.athlete_settings("ath-test")
    assert len(harness.requests) == 1 and harness.requests[0][1] == "/rpc/read_onflows_planning_inputs"


def test_bundle_metrics_use_a_fixed_label_without_alias_or_hint_values():
    harness = BundleHTTP()
    metrics = StoreMetrics()
    token = current_metrics.set(metrics)
    try:
        phase = ReadSession.planning(harness.repository)
        phase.capture_planning_inputs("synthetic-private-alias")
        assert phase.athlete_settings("synthetic-private-alias")
    finally:
        current_metrics.reset(token)
    assert metrics.calls == 1 and set(metrics.resources) == {"GET:planning_inputs"}
    assert "synthetic-private-alias" not in metrics.details()


def test_existing_http_authorization_denials_do_not_capture_any_planning_inputs(monkeypatch):
    harness = BundleHTTP()
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "service-secret")
    monkeypatch.setattr(dependencies, "repository", lambda: harness.repository)
    client = TestClient(app)
    route = "/api/v2/athlete/management/state"
    assert client.get(route).status_code == 401
    assert client.get(route, headers={"Authorization": "Bearer wrong-token",
                                    "X-OnFlows-Athlete-Alias": "ath-test"}).status_code == 401
    assert client.get(route, headers={"Authorization": "Bearer service-secret"}).status_code == 401
    headers = {k: v for k, v in HEADERS.items() if k != "X-OnFlows-Actor-Id"}
    assert client.post("/api/v2/athlete/management/action", headers=headers,
                       json={"action": "REFRESH", "expected_revision": 1}).status_code == 401
    assert harness.requests == []


@pytest.mark.parametrize("bad", ["wrong_hint", "bool_revision", "full_request", "unexpected_snapshot"])
def test_metadata_omission_requires_the_same_validated_immutable_hint(bad):
    harness = BundleHTTP()
    hint = {"generation_id": harness.envelope["generation_id"], "analysis_revision": harness.envelope["revision"],
            "analysis_as_of": harness.envelope["analysis_as_of"], "snapshot_fingerprint": "a"*64}
    value = harness.value("ath-test", "HINTED", [hint])
    assert value["analysis"]["mode"] == "METADATA"
    mode, hints = "HINTED", [hint]
    if bad == "wrong_hint": hints = [{**hint, "generation_id": "other"}]
    elif bad == "bool_revision": hints = [{**hint, "analysis_revision": True}]
    elif bad == "full_request": mode = "FULL"
    else: value["analysis"]["row"]["snapshot_payload"] = {}
    bundle = PlanningInputBundle(value, "ath-test", mode, hints)
    assert bundle.settings_bundle().athlete_settings()
    with pytest.raises(PersistentStoreFailure, match="Invalid planning input bundle"):
        bundle.active_analysis_metadata()


@pytest.mark.parametrize("hints", [None, False, 7, "not-a-list", {"generation_id": "other"}])
def test_invalid_hint_container_requires_full_analysis_in_the_same_bundle(hints):
    harness = BundleHTTP()
    phase = ReadSession.planning(harness.repository)
    phase.capture_planning_inputs("ath-test", analysis_hints=hints)
    assert phase.active_analysis("ath-test")["snapshot_payload"] == harness.envelope["snapshot_payload"]
    assert len(harness.requests) == 1
    assert json.loads(harness.requests[0][2]["p_analysis_hints"]) == []


def test_saved_hint_is_frozen_with_the_phase_before_the_caller_can_mutate_it():
    harness = BundleHTTP()
    hint = {"generation_id": harness.envelope["generation_id"], "analysis_revision": harness.envelope["revision"],
            "analysis_as_of": harness.envelope["analysis_as_of"], "snapshot_fingerprint": "a"*64}
    phase = ReadSession.planning(harness.repository)
    phase.capture_planning_inputs("ath-test", analysis_hints=[hint])
    hint["generation_id"] = "changed-by-caller"
    assert phase.active_analysis_metadata("ath-test")["generation_id"] == harness.envelope["generation_id"]
    assert len(harness.requests) == 1


@pytest.mark.parametrize("mode", [[], {}, None, 1])
def test_malformed_analysis_mode_is_a_store_failure_with_other_fields_still_lazy(mode):
    harness = BundleHTTP()
    value = harness.value("ath-test")
    value["analysis"]["mode"] = mode
    bundle = PlanningInputBundle(value, "ath-test", "FULL", [])
    assert bundle.settings_bundle().athlete_settings()
    with pytest.raises(PersistentStoreFailure, match="Invalid planning input bundle"):
        bundle.active_analysis()
