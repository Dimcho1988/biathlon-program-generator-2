"""The route carries the analysis revision through its publication boundary."""
from copy import deepcopy
import json

import pytest

from tests.api.test_management_final_read_phase import PublicationHTTP


class RevisionCheckpointHTTP(PublicationHTTP):
    """Represent the SQL projection while exercising the actual HTTP adapter."""
    def checkpoint(self):
        return {**super().checkpoint(), "analysis_revision": self.envelope["revision"]}


def late_monotonic_rollback(state):
    generation, revision = state.envelope["generation_id"], state.envelope["revision"]
    state.envelope.update(generation_id="synthetic-intermediate-generation", revision=revision+1)
    state.envelope.update(generation_id=generation, revision=revision+2)


@pytest.mark.parametrize("shared_state", ["READY", "OWNED"])
def test_late_same_generation_rollback_rejects_the_candidate_instead_of_saving_an_immediately_stale_revision(
        monkeypatch, shared_state):
    previous = PublicationHTTP(shared_state=shared_state)
    current = RevisionCheckpointHTTP(shared_state=shared_state)
    for harness in (previous, current):
        harness.on_save = late_monotonic_rollback
    old_response, response = previous.run(monkeypatch), current.run(monkeypatch)

    # This reproduces the former externally observable failure, rather than
    # inventing a rollback which lowers the monotonically increasing revision.
    assert old_response.status_code == 200
    assert previous.record["revision"] == 2 and old_response.json()["active"]["stale"] is True
    assert response.status_code == 409
    assert response.json()["detail"] == "The plan or its inputs changed; reload and try again"
    assert current.record["revision"] == 1
    assert current.envelope["generation_id"] == previous.envelope["generation_id"]
    assert current.envelope["revision"] == previous.envelope["revision"]
    writes = [body for _, path, _, body in current.requests if path == "/rpc/save_onflows_management_plan_compact"]
    assert len(writes) == 1  # No automatic retry or alternative publication.
    assert writes[0]["p_checkpoint"]["analysis_revision"] == current.envelope["revision"]-2


@pytest.mark.parametrize("shared_state", ["READY", "OWNED"])
def test_unchanged_inputs_keep_the_complete_http_json_and_request_count(monkeypatch, shared_state):
    previous = PublicationHTTP(shared_state=shared_state)
    current = RevisionCheckpointHTTP(shared_state=shared_state)
    old_response, response = previous.run(monkeypatch), current.run(monkeypatch)
    assert old_response.status_code == response.status_code == 200
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False, allow_nan=False)
    assert canonical(old_response.json()) == canonical(response.json())
    assert len(previous.requests) == len(current.requests)
    writes = [body for _, path, _, body in current.requests if path == "/rpc/save_onflows_management_plan_compact"]
    assert len(writes) == 1
    assert writes[0]["p_checkpoint"] == current.checkpoint()


@pytest.mark.parametrize("kind", ["generation", "settings", "model", "response"])
def test_other_late_input_changes_still_conflict_without_a_plan_write(monkeypatch, kind):
    harness = RevisionCheckpointHTTP()
    original_plan = deepcopy(harness.raw_plan)
    def change(state):
        if kind == "generation":
            state.envelope.update(generation_id="synthetic-new-generation", revision=state.envelope["revision"]+1)
        elif kind == "settings":
            state.row["hrmax_bpm"] = 201
        else:
            entry = {"kind": "RECOVERY" if kind == "model" else "DAILY", "entry_key": "late-input",
                     "revision": 1, "payload": {}}
            setattr(state, "models" if kind == "model" else "responses", [entry])
    harness.on_save = change
    response = harness.run(monkeypatch)
    assert response.status_code == 409 and harness.record["revision"] == 1
    assert harness.record["payload"]["plan"] == original_plan
    assert sum(path == "/rpc/save_onflows_management_plan_compact" for _, path, _, _ in harness.requests) == 1
