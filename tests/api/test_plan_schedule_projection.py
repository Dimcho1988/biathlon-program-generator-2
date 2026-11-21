"""Plan summaries retain both immutable schedules without full model snapshots."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.api.management_plan_store import PlanStore, SCHEDULE_FIELDS, PLAN_PROVENANCE_FIELDS
from apps.api.oauth_store import PersistentStoreFailure
from apps.api.plan_storage import pack, unpack
from tests.api.test_assistant_day_projection import record, ALIAS


def project(full):
    payload = full["payload"]
    row = {key: value for key, value in full.items() if key != "payload"}
    row.update(status=payload.get("status"), reason=payload.get("reason"))
    for name in ("plan", "proposal"):
        plan = payload.get(name) or {}
        row.update({f"{name}_{field}": plan.get(field) for field in SCHEDULE_FIELDS})
        row.update({f"{name}_input_{field}": (plan.get("input_snapshot") or {}).get(field) for field in PLAN_PROVENANCE_FIELDS})
    return {"revision": full["revision"], "record": row}


@pytest.mark.parametrize("compressed", [False, True])
def test_schedule_projection_preserves_complete_plan_and_candidate_days(compressed):
    full = record()
    full["payload"]["plan"]["lookahead_days"] = [{"private": "not a current day"}]
    saved = pack(full) if compressed else full
    repo = SimpleNamespace(_request=Mock(return_value=[project(saved)]), _json=lambda value: value)
    result = PlanStore(repo).current(ALIAS, schedule_only=True)
    original = unpack(saved)
    assert result["revision"] == original["revision"]
    for name in ("plan", "proposal"):
        assert result["payload"][name]["days"] == original["payload"][name]["days"]
        assert "lookahead_days" not in result["payload"][name]
        assert "private_heavy" not in result["payload"][name]["input_snapshot"]
    assert result["payload"]["plan"]["input_snapshot"]["generation_id"] == "g-old"
    assert result["payload"]["proposal"]["days"][0]["sessions"][0]["title"] == "Unapproved alternative"
    path = repo._request.call_args.args[1]
    assert "plan_days:payload->plan->days" in path and "proposal_days:payload->proposal->days" in path
    assert "input_snapshot," not in path and "operation,payload," not in path
    assert repo._request.call_count == 1  # atomic pointer + revision join


def test_absent_and_missing_plan_remain_distinct():
    repo = SimpleNamespace(_request=Mock(return_value=[]), _json=lambda value: value)
    assert PlanStore(repo).current(ALIAS, schedule_only=True) is None
    full = record()
    full["payload"]["plan"] = None
    repo._request.return_value = [project(full)]
    result = PlanStore(repo).current(ALIAS, schedule_only=True)
    assert result["payload"]["plan"] is None and result["payload"]["proposal"] is not None


@pytest.mark.parametrize("change", [{"revision": 9}, {"plan__storage": "unknown-format"}, {"plan_schema_version": None}, {"plan_days": None}])
def test_invalid_projection_or_pointer_fails_closed(change):
    projected = project(record())
    projected["record"].update(change)
    repo = SimpleNamespace(_request=Mock(return_value=[projected]), _json=lambda value: value)
    with pytest.raises(PersistentStoreFailure):
        PlanStore(repo).current(ALIAS, schedule_only=True)
