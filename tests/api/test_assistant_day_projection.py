"""A chat day read is complete, but transfers only one immutable day."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.api import assistant_context as context
from apps.api.management_plan_store import PlanStore
from apps.api.oauth_store import PersistentStoreFailure
from apps.api.plan_storage import pack, unpack

ALIAS = "athlete-test"
SELECTED = date(2026, 11, 13)
ACCESS = {"can_view_plan": True, "can_view_recovery": True}


def record():
    sessions = [{"title": f"Session {number}", "sport": "Run", "method_id": f"m-{number}",
        "total_minutes": 30 + number, "zone": "Z4", "blocks": [
            {"kind": "WARMUP", "duration_min": 10, "zone": "Z1", "target_hr_bpm": 130},
            {"kind": "WORK", "duration_s": 300, "target_speed_kmh": 18 + number,
             "instructions": "Preserve the full frozen instructions."}],
        "dose_evidence": {"capacity_minutes": 20, "explanation": "Frozen decision",
            "limits": [{"code": "RECOVERY", "limit_minutes": 10}]},
        "method_library": {"id": f"m-{number}", "title": "Frozen card", "guidance": {"execution": "Exact saved guidance"}}}
        for number in (1, 2)]
    return {"revision": 7, "recorded_at": "2026-11-12T12:00:00Z", "payload": {
        "schema_version": "active-plan-v2", "status": "REVIEW_REQUIRED",
        "plan": {"schema_version": "planning-draft-v1", "engine_version": "engine-v1", "fingerprint": "plan-fingerprint",
            "generated_at": "2026-11-12T11:00:00Z", "start_date": "2026-11-12", "end_date": "2026-11-18",
            "input_snapshot": {"generation_id": "g-old", "analysis_revision": 5, "profile_revision": 4, "private_heavy": "x" * 100000},
            "source": {"analysis_as_of": "2026-11-11", "private_heavy": "x" * 100000},
            "days": [{"date": "2026-11-12", "status": "REST", "sessions": []},
                     {"date": "2026-11-13", "status": "TRAINING", "sessions": sessions, "session": deepcopy(sessions[0]),
                      "explanation": "Both sessions have their own frozen evidence."}]},
        "proposal": {"schema_version": "planning-draft-v1", "days": [{"date": "2026-11-13", "sessions": [{"title": "Unapproved alternative"}]}]}}}


def projected_repository(full, selected=SELECTED):
    payload = full["payload"]
    plan = payload.get("plan")
    overview = {"revision": full["revision"], "record_revision": full["revision"], "status": payload["status"],
        "plan_type": "object" if isinstance(plan, dict) else "null",
        "proposal_type": "object" if isinstance(payload.get("proposal"), dict) else "null",
        "has_proposal": isinstance(payload.get("proposal"), dict),
        "days": [{"date": day["date"]} for day in (plan or {}).get("days", [])]}
    row = {"revision": full["revision"], "recorded_at": full["recorded_at"], "status": payload["status"]}
    for name in ("schema_version", "_storage", "engine_version", "fingerprint", "generated_at", "start_date", "end_date"):
        row["plan_" + name] = (plan or {}).get(name)
    for prefix, field in (("input", "input_snapshot"), ("source", "source")):
        row.update({prefix + "_" + name: value for name, value in (plan or {}).get(field, {}).items() if name in context.SNAPSHOT_FIELDS})
    row["selected_day"] = next((deepcopy(day) for day in (plan or {}).get("days", []) if day["date"] == selected.isoformat()), None)
    repo = SimpleNamespace(_request=Mock(side_effect=[deepcopy(overview), [deepcopy(row)]]), _json=lambda value: value)
    return repo, overview, row


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("selected", [SELECTED, date(2026, 11, 20)])
def test_projected_day_matches_full_record_without_reading_the_full_plan(monkeypatch, compressed, selected):
    full = pack(record()) if compressed else record()
    monkeypatch.setattr(context, "PlanStore", lambda _: SimpleNamespace(current=lambda _: unpack(deepcopy(full))))
    expected = context._plan_day(None, ALIAS, ACCESS, selected, date(2026, 11, 12))
    monkeypatch.setattr(context, "PlanStore", PlanStore)
    repo, _, _ = projected_repository(full, selected)
    result = context._plan_day(repo, ALIAS, ACCESS, selected, date(2026, 11, 12))
    assert result == expected
    assert repo._request.call_count == 2
    first, second = repo._request.call_args_list
    assert first.args[1].startswith("/rpc/read_onflows_management_overview?")
    path = second.args[1]
    assert "&revision=eq.7" in path and "&athlete_alias=eq.athlete-test" in path
    assert "select=revision,recorded_at,status:payload->status" in path
    assert "payload->proposal" not in path and "input_snapshot," not in path
    assert ("selected_day:payload->plan->days->1" in path) is (selected == SELECTED)
    if selected == SELECTED:
        assert len(result["sessions"]) == 2
        assert result["sessions"][1]["blocks"][1]["target_speed_kmh"] == 20
        assert result["sessions"][1]["dose_evidence"]["explanation"] == "Frozen decision"
        assert result["proposal_available"] is True


def test_compressed_selected_day_restores_independent_legacy_primary_alias():
    repo, _, _ = projected_repository(pack(record()))
    selected = PlanStore(repo).assistant_day(ALIAS, SELECTED)["payload"]["plan"]["days"][0]
    assert selected["session"] == selected["sessions"][0]
    assert selected["session"] is not selected["sessions"][0]
    assert "$session_ref" not in selected["session"]


def test_second_read_is_pinned_even_when_the_active_pointer_has_advanced():
    repo, overview, row = projected_repository(record())
    calls = []
    def request(method, path):
        calls.append(path)
        if path.startswith("/rpc/"):
            return overview
        assert "revision=eq.7" in path  # current pointer is now 8; revision 7 is immutable
        return [row]
    repo._request = request
    assert PlanStore(repo).assistant_day(ALIAS, SELECTED)["revision"] == 7
    assert len(calls) == 2 and sum("/rpc/" in path for path in calls) == 1


@pytest.mark.parametrize("change", [{"revision": 8}, {"status": "PAUSED"}, {"selected_day": {"date": "2026-11-14"}}])
def test_changed_or_mismatched_immutable_record_fails_closed(change):
    repo, overview, row = projected_repository(record())
    repo._request.side_effect = [overview, [{**row, **change}]]
    with pytest.raises(PersistentStoreFailure):
        PlanStore(repo).assistant_day(ALIAS, SELECTED)


def test_missing_plan_preserves_unavailable_metadata(monkeypatch):
    full = record()
    full["payload"]["plan"] = None
    repo, _, _ = projected_repository(full)
    monkeypatch.setattr(context, "PlanStore", PlanStore)
    result = context._plan_day(repo, ALIAS, ACCESS, SELECTED, date(2026, 11, 12))
    assert result["reason"] == "NO_SAVED_PLAN" and result["sessions"] == []
    assert result["plan_revision"] == 7 and result["recorded_at"] == full["recorded_at"]


def test_absent_pointer_never_reads_revision_or_invents_rest():
    repo = SimpleNamespace(_request=Mock(return_value=None), _json=lambda value: value)
    assert PlanStore(repo).assistant_day(ALIAS, SELECTED) is None
    assert repo._request.call_count == 1
