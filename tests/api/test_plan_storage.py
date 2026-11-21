from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from apps.api.plan_storage import pack, unpack, FORMAT
from apps.api.management_store import ManagementStore
from apps.api.management_plan_store import PlanStore
from apps.api.oauth_store import SupabasePilotRepository, PersistentStoreFailure, PlanStorageCapacityExceeded


def draft():
    session = {"title": "5 × 3 мин", "blocks": [{"zone": "Z4", "duration_min": 3.,
        "instructions": "Запази равномерна скорост."}], "dose_evidence": {"q": 15.125}}
    day = {"date": "2026-11-02", "locked": True, "session": deepcopy(session),
           "sessions": [session, {**session, "title": "Втора сесия"}]}
    return {"schema_version": "planning-draft-v1", "start_date": day["date"],
            "days": [day], "lookahead_days": [deepcopy(day)], "fingerprint": "frozen",
            "source": {"generation_id": "unchanged"}}


def test_frozen_sessions_and_future_days_roundtrip_without_mutating_inputs():
    plan = draft()
    original = deepcopy(plan)
    stored = pack(plan)
    assert stored["_storage"] == FORMAT
    assert stored["days"][0]["session"] == {"$session_ref": 0}
    assert stored["days"][0]["sessions"] == original["days"][0]["sessions"]
    assert stored["source"] == original["source"] and stored["fingerprint"] == original["fingerprint"]
    assert pack(stored) == stored
    assert unpack(stored) == original
    assert unpack(unpack(stored)) == original
    assert plan == original


def test_unequal_legacy_session_and_legacy_only_days_are_kept_exactly():
    plan = draft()
    plan["days"][0]["session"]["blocks"][0]["duration_min"] = 4.
    plan["lookahead_days"][0].pop("sessions")
    assert pack(plan) == plan
    assert unpack(pack(plan)) == plan


def test_json_types_and_independent_aliases_are_preserved():
    import json
    plan = draft()
    plan["days"][0]["session"]["dose_evidence"]["q"] = True
    plan["days"][0]["sessions"][0]["dose_evidence"]["q"] = 1
    restored = unpack(pack(plan))
    assert json.dumps(restored, sort_keys=True) == json.dumps(plan, sort_keys=True)
    normal = unpack(pack(draft()))
    normal["days"][0]["session"]["title"] = "Самостоятелен стар запис"
    assert normal["days"][0]["sessions"][0]["title"] == "5 × 3 мин"


@pytest.mark.parametrize("reference", [{"$session_ref": 1}, {"$session_ref": False},
    {"$session_ref": 0, "extra": True}])
def test_invalid_frozen_reference_fails_closed(reference):
    stored = pack(draft())
    stored["days"][0]["session"] = reference
    with pytest.raises(PersistentStoreFailure):
        unpack(stored)


def test_draft_history_exact_revision_and_save_echo_restore_public_contract():
    plan = draft()
    row = {"kind": "DRAFT", "entry_key": plan["start_date"], "revision": 1,
           "recorded_at": "2026-11-02T12:00:00Z", "payload": pack(plan)}
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {**row, "saved": True} if method == "POST" else [deepcopy(row)]
    store = ManagementStore(SimpleNamespace(_request=request, _json=lambda v: v))
    assert store.drafts("athlete")[0]["payload"] == plan
    assert store.draft_revision("athlete", date(2026,11,2), 1)[0]["payload"] == plan
    assert store.save_draft("athlete", plan, "actor", 2)["payload"] == plan
    assert calls[-1][2]["json"]["p_payload"] == pack(plan)


def test_active_plan_and_pending_proposal_keep_complete_independent_versions():
    plan, proposal = draft(), draft()
    proposal["days"][0]["sessions"][0]["title"] = "Нова задача"
    proposal["days"][0]["session"] = deepcopy(proposal["days"][0]["sessions"][0])
    payload = {"status": "REVIEW_REQUIRED", "plan": plan, "proposal": proposal}
    calls = []
    def request(method, path, **kwargs):
        calls.append(kwargs)
        return {"saved": True, "revision": 2, "payload": kwargs["json"]["p_payload"]}
    store = PlanStore(SimpleNamespace(_request=request, _json=lambda v: v))
    assert store.save("athlete", payload, 1, "actor", "REVIEW", 2, {})["payload"] == payload
    assert calls[0]["json"]["p_payload"]["plan"]["_storage"] == FORMAT
    assert calls[0]["json"]["p_payload"]["proposal"]["_storage"] == FORMAT


def test_background_writer_compacts_and_size_rejection_is_permanent():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(400, json={"code": "23514", "message":
            'new row violates check constraint "onflows_management_entries_check"',
            "details": "private training data must not appear in errors"})
    repo = SupabasePilotRepository(supabase_url="https://offline.invalid", secret_key="offline",
        encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PlanStorageCapacityExceeded) as exc:
        repo.planning_job("COMPLETE", job="job", token="token", payload=draft())
    import json
    assert json.loads(requests[0].content)["p_payload"]["_storage"] == FORMAT
    assert len(requests) == 1 and "private training" not in str(exc.value)
