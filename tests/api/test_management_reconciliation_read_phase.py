"""Reconciliation uses captured load history without an unused session catalog."""
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from apps.api import management_lifecycle as lifecycle, management_service as service
from apps.api.oauth_store import PersistentStoreFailure
from apps.api.read_session import ReadSession
from tests.api.test_management_initial_read_phase import InitialPhaseHTTP, run_refresh
from tests.api.test_training_plan_engine import NOW, TODAY


def original_reconciliation_analysis(repository, alias, today, frozen, initial_read_phase):
    reader = getattr(repository, "active_planning_calendar", None) or repository.active_activity_calendar
    return reader(alias, today-timedelta(days=89), today) or {}


class HistoricalOutcomesHTTP(InitialPhaseHTTP):
    def __init__(self):
        super().__init__()
        self.prepared_record = False
        self.dates = {name: (TODAY-timedelta(days=age)).isoformat() for name, age in
                      (("skipped", 1), ("recorded", 2), ("missed", 3), ("different", 4), ("unknown", 5), ("archived", 100))}
        self.decisions = {self.dates["skipped"]: {"action": "SKIP", "note": "Coach decision retained"}}
        self.archived = {"date": self.dates["archived"], "status": "RECORDED", "planned_title": "Older preserved task",
                         "planned_sport": "Run", "planned_minutes": 70., "actual_minutes": 66.,
                         "matching": "PRESERVED_ARCHIVE", "activity_refs": ["preserved-activity"]}
        source = self.envelope["snapshot_payload"]["load_history"]
        source["activities"].extend([
            {"activity_ref": "recorded-run", "date": self.dates["recorded"], "sport": "Run", "duration_min": 40.},
            {"activity_ref": "different-run", "date": self.dates["different"], "sport": "Run", "duration_min": 25.},
        ])
        for row in source["daily"]:
            if row["date"] == self.dates["recorded"] and row["zone"] == "Z2":
                row["effective_load"] = 12.
        source["strength"]["daily"] = [row for row in source["strength"]["daily"] if row["date"] != self.dates["unknown"]]

    def run(self, monkeypatch, **kwargs):
        # Prepare the exact expected cache key before the request spy starts.
        self.expected_before = service.input_state(self.repository, "ath-test", evaluated_at=NOW, include_response=True)
        return super().run(monkeypatch, **kwargs)

    def handler(self, request):
        if request.url.path.endswith("/onflows_management_plan_state") and not self.prepared_record:
            self.prepared_record = True
            payload = self.record["payload"]
            payload["decisions"] = deepcopy(self.decisions)
            payload["outcomes"] = [deepcopy(self.archived)]
            for name, minutes, sport in (("skipped", 30., "Run"), ("recorded", 45., "Run"),
                                         ("missed", 35., "Run"), ("different", 20., "WeightTraining"), ("unknown", 18., "Run")):
                session = {"title": f"Planned {name}", "total_minutes": minutes, "sport": sport,
                           "canonical_effective_load": {"Z2": 8.}, "blocks": []}
                payload["plan"]["days"].append({"date": self.dates[name], "status": "TRAINING",
                    "explanation": "Historical task", "session": session, "sessions": [session]})
            self.key = service._hash({"athlete": "ath-test", "inputs": self.expected_before, "profile": self.profile,
                "profile_revision": 1, "start_date": TODAY.isoformat(), "decisions": self.decisions, "locked_day": None})
        return super().handler(request)


def test_complete_http_record_key_and_historical_outcomes_match_with_three_fewer_reads(monkeypatch):
    previous, current = HistoricalOutcomesHTTP(), HistoricalOutcomesHTTP()
    helper = lifecycle._reconciliation_analysis
    monkeypatch.setattr(lifecycle, "_reconciliation_analysis", original_reconciliation_analysis)
    expected = run_refresh(monkeypatch, previous)
    monkeypatch.setattr(lifecycle, "_reconciliation_analysis", helper)
    original_source = deepcopy(current.envelope)
    actual = run_refresh(monkeypatch, current)
    assert actual.status_code == expected.status_code == 200
    assert actual.json() == expected.json() and current.record == previous.record
    assert current.envelope == original_source
    assert len(previous.requests) == 23 and len(current.requests) == 20
    before, after = (Counter(path for _, path, _, _ in harness.requests) for harness in (previous, current))
    assert before-after == Counter({"/rpc/active_onflows_analysis": 1, "/onflows_analysis_generations": 1,
                                   "/onflows_analysis_generation_activities": 1})
    def keys(harness):
        return [body["p_key"] for _, path, _, body in harness.requests
                if path == "/rpc/onflows_planning_computation" and body["p_operation"] == "CLAIM"]
    assert keys(previous) == keys(current) == [current.key]
    payload = actual.json()["active"]["payload"]
    outcomes = {row["date"]: row for row in payload["outcomes"]}
    assert payload["decisions"] == current.decisions
    assert outcomes[current.dates["skipped"]]["status"] == "SKIPPED"
    assert outcomes[current.dates["recorded"]]["status"] == "RECORDED"
    assert outcomes[current.dates["recorded"]]["actual_minutes"] == 40.
    assert outcomes[current.dates["recorded"]]["actual_load"]["Z2"] == 12.
    assert outcomes[current.dates["recorded"]]["activity_refs"] == ["recorded-run"]
    assert outcomes[current.dates["missed"]]["status"] == "MISSED"
    assert outcomes[current.dates["different"]]["status"] == "DIFFERENT_ACTIVITY"
    assert outcomes[current.dates["unknown"]]["status"] == "UNKNOWN"
    assert outcomes[current.dates["unknown"]]["actual_minutes"] is None
    assert outcomes[current.dates["archived"]] == current.archived


@pytest.mark.parametrize("shared_state", ["READY", "OWNED"])
@pytest.mark.parametrize("rollback", [False, True])
def test_monotonic_activation_or_rollback_after_capture_is_rejected_by_the_live_after_phase(monkeypatch, shared_state, rollback):
    harness = InitialPhaseHTTP(shared_state=shared_state)
    original_snapshot = deepcopy(harness.envelope["snapshot_payload"])
    original_generation = harness.envelope["generation_id"]
    transitions = []
    def activate(state):
        transitions.append((state.envelope["generation_id"], state.envelope["revision"]))
        state.envelope.update(generation_id="new-generation", revision=2)
        transitions.append((state.envelope["generation_id"], state.envelope["revision"]))
        if rollback:
            # A rollback restores the earlier generation, not the earlier
            # publication revision: the source sequence still advances.
            state.envelope.update(generation_id=original_generation, revision=3)
            transitions.append((state.envelope["generation_id"], state.envelope["revision"]))
    harness.on_claim = activate
    response = run_refresh(monkeypatch, harness)
    assert response.status_code == 409 and harness.record["revision"] == 1
    assert "inputs changed" in response.json()["detail"]
    assert not any(path == "/rpc/save_onflows_management_plan_compact" for _, path, _, _ in harness.requests)
    assert transitions[:2] == [(original_generation, 1), ("new-generation", 2)]
    assert harness.envelope["snapshot_payload"] == original_snapshot
    if rollback:
        assert transitions[-1] == (original_generation, 3)
    else:
        assert transitions[-1] == ("new-generation", 2)


@pytest.mark.parametrize("captured,frozen", [
    (None, {"generation_id": "G1", "analysis_revision": 1}),
    ({"generation_id": "G2", "revision": 1, "snapshot_payload": {}}, {"generation_id": "G1", "analysis_revision": 1}),
    ({"generation_id": "G1", "revision": 2, "snapshot_payload": {}}, {"generation_id": "G1", "analysis_revision": 1}),
    ({"generation_id": "G1", "revision": True, "snapshot_payload": {}}, {"generation_id": "G1", "analysis_revision": 1}),
    ({"generation_id": "G1", "revision": 1, "snapshot_payload": []}, {"generation_id": "G1", "analysis_revision": 1}),
    ({"generation_id": None, "revision": 0, "snapshot_payload": {}}, {"generation_id": None, "analysis_revision": 0}),
    ({"generation_id": "G1", "revision": 1, "snapshot_payload": {}}, {"generation_id": "G1", "analysis_revision": True}),
])
def test_unreliable_or_legacy_capture_preserves_the_original_reader_and_window(captured, frozen):
    calls = []
    expected = {"generation_id": "fallback", "revision": 3, "snapshot_payload": {"load_history": {"marker": "original"}}}
    def reader(*args):
        calls.append(args)
        return deepcopy(expected)
    repository = SimpleNamespace(active_planning_calendar=reader,
        active_activity_calendar=lambda *args: pytest.fail("The preferred reader must remain preferred"))
    initial = SimpleNamespace(active_analysis=lambda alias: deepcopy(captured))
    assert lifecycle._reconciliation_analysis(repository, "ath-test", TODAY, frozen, initial) == expected
    assert calls == [("ath-test", TODAY-timedelta(days=89), TODAY)]


@pytest.mark.parametrize("preferred", [True, False])
def test_no_analysis_capability_keeps_the_existing_calendar_fallback(preferred):
    calls = []
    def reader(*args):
        calls.append(args)
        return {"original_reader": True}
    repository = SimpleNamespace(active_activity_calendar=reader)
    if preferred:
        repository.active_planning_calendar = reader
    assert lifecycle._reconciliation_analysis(repository, "ath-test", TODAY,
        {"generation_id": "G1", "analysis_revision": 1}, SimpleNamespace()) == {"original_reader": True}
    assert calls == [("ath-test", TODAY-timedelta(days=89), TODAY)]


def test_analysis_read_failure_is_not_hidden_by_another_generation_or_calendar():
    def unavailable(alias):
        raise PersistentStoreFailure("Captured analysis unavailable")
    phase = SimpleNamespace(active_analysis=unavailable)
    repository = SimpleNamespace(active_planning_calendar=lambda *args: pytest.fail("No fallback after a failed read"))
    with pytest.raises(PersistentStoreFailure, match="Captured analysis unavailable"):
        lifecycle._reconciliation_analysis(repository, "ath-test", TODAY,
            {"generation_id": "G1", "analysis_revision": 1}, phase)


def test_fallback_failure_retains_its_original_store_error():
    def unavailable(*args):
        raise PersistentStoreFailure("Original planning calendar unavailable")
    repository = SimpleNamespace(active_planning_calendar=unavailable,
        active_activity_calendar=lambda *args: pytest.fail("A failed preferred reader must not switch readers"))
    with pytest.raises(PersistentStoreFailure, match="Original planning calendar unavailable"):
        lifecycle._reconciliation_analysis(repository, "ath-test", TODAY,
            {"generation_id": None, "analysis_revision": 0}, SimpleNamespace())


def test_reconciliation_capture_is_scoped_to_the_requested_athlete():
    calls = []
    def analysis(alias):
        calls.append(alias)
        return {"generation_id": alias, "revision": 1, "snapshot_payload": {"load_history": {"athlete": alias}}}
    repository = SimpleNamespace(active_analysis=analysis,
        active_planning_calendar=lambda *args: pytest.fail("Matching immutable captures need no catalog"))
    phase = ReadSession.planning(repository)
    phase.active_analysis("ath-first")
    result = lifecycle._reconciliation_analysis(repository, "ath-second", TODAY,
        {"generation_id": "ath-second", "analysis_revision": 1}, phase)
    assert result["snapshot_payload"]["load_history"]["athlete"] == "ath-second"
    assert calls == ["ath-first", "ath-second"]
