"""Generated programs become current only through the fenced canonical result."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.api import management_service, management_lifecycle as lifecycle, planning_jobs
from apps.api.management_schemas import ManagementGenerateRequest
from tests.api.test_management_lifecycle import system, activate, ACTOR, NOW, TODAY


def request():
    return ManagementGenerateRequest(start_date=TODAY+timedelta(days=1), expected_profile_revision=1,
                                     expected_draft_revision=3)


def test_first_valid_generated_program_prepares_atomic_current_publication(system):
    prepared = management_service.build_generated_plan(system.repo, "athlete", request(), ACTOR, now=NOW)
    assert prepared["publication"]["expected_revision"] == 0
    current = prepared["publication"]["payload"]
    assert current["status"] == "ACTIVE" and current["proposal"] is None
    assert current["plan"] == prepared["draft"]
    assert current["plan"]["review_required"] is False
    assert current["plan"]["automatically_published"] is True
    assert current["origin"] == {"start_date": request().start_date.isoformat(), "draft_revision": 4}
    assert current["approved_by"] == ACTOR
    assert not system.plans.records  # Calculation itself never publishes a pointer.


def test_generated_replacement_preserves_decisions_outcomes_and_q_history(system):
    activate(system)
    existing = system.plans.records[-1]["payload"]
    selected = request().start_date.isoformat()
    existing["decisions"] = {selected: {"action": "SKIP", "note": "Saved coach decision"}}
    existing["outcomes"] = [{"date": TODAY.isoformat(), "status": "RECORDED", "actual_q": {"Z1": 25}}]
    historical = {"date": (TODAY-timedelta(days=150)).isoformat(), "q": {"Z1": 12.5}}
    existing["q_execution"] = {"days": [historical]}
    before = deepcopy(system.plans.records)
    new_actor = "22222222-2222-4222-8222-222222222222"
    prepared = management_service.build_generated_plan(system.repo, "athlete", request(), new_actor, now=NOW)
    current = prepared["publication"]["payload"]
    assert prepared["publication"]["expected_revision"] == 1
    assert current["approved_by"] == new_actor
    assert current["decisions"] == existing["decisions"]
    assert current["outcomes"] == existing["outcomes"]
    assert historical in current["q_execution"]["days"]
    day = next(row for row in current["plan"]["days"] if row["date"] == selected)
    assert day["status"] == "SKIPPED" and day["session"] is None
    assert system.plans.records == before


def test_generation_cannot_resume_a_paused_program(system):
    activate(system)
    system.plans.records[-1]["payload"]["status"] = "PAUSED"
    prepared = management_service.build_generated_plan(system.repo, "athlete", request(), ACTOR, now=NOW)
    assert lifecycle._eligible(prepared["draft"])
    assert prepared["publication"] is None
    assert system.plans.records[-1]["payload"]["status"] == "PAUSED"


def test_explicit_generation_can_start_new_period_after_completed_program(system):
    activate(system)
    system.plans.records[-1]["payload"]["status"] = "COMPLETED"
    prepared = management_service.build_generated_plan(system.repo, "athlete", request(), ACTOR, now=NOW)
    assert prepared["publication"]["payload"]["status"] == "ACTIVE"
    assert prepared["publication"]["expected_revision"] == 1
    assert lifecycle.refresh(system.repo, "athlete", automatic=True, now=NOW)["payload"]["status"] == "COMPLETED"


def test_ineligible_generated_result_is_not_an_approvable_prescription(system, monkeypatch):
    payload = management_service.build_draft(system.repo, "athlete", request().start_date, 1, now=NOW)
    payload["activation_eligible"] = False
    monkeypatch.setattr(management_service, "build_draft", lambda *a, **k: deepcopy(payload))
    prepared = management_service.build_generated_plan(system.repo, "athlete", request(), ACTOR, now=NOW)
    assert prepared["publication"] is None
    assert prepared["draft"]["review_required"] is False


def test_profile_change_during_generation_cannot_publish(system, monkeypatch):
    original = management_service.build_draft
    def changed(*a, **k):
        result = original(*a, **k)
        system.profiles.revision += 1
        return result
    monkeypatch.setattr(management_service, "build_draft", changed)
    with pytest.raises(HTTPException) as exc:
        management_service.build_generated_plan(system.repo, "athlete", request(), ACTOR, now=NOW)
    assert exc.value.status_code == 409
    assert not system.plans.records


def test_worker_adopts_legacy_unpublished_programs_before_claiming():
    calls = []
    repo = SimpleNamespace(queue_unpublished_plans=lambda: calls.append("adopt"),
        planning_job=lambda operation, **kw: calls.append(operation))
    assert planning_jobs.process_next(repo) is False
    assert calls == ["adopt", "CLAIM"]


def test_generated_store_publishes_one_compressed_atomic_envelope():
    from apps.api.management_store import ManagementStore
    from apps.api.plan_storage import REFERENCE
    session = {"title": "Synthetic session", "blocks": [{"seconds": 30}]}
    draft = {"schema_version": "planning-draft-v1", "start_date": "2026-09-22",
             "days": [{"date": "2026-09-22", "session": deepcopy(session), "sessions": [session]}]}
    prepared = {"draft": draft, "publication": {"payload": {"schema_version": "active-plan-v2", "plan": draft},
        "expected_revision": 2, "checkpoint": {"generation_id": "synthetic"}}}
    calls = []
    def write(method, path, **kwargs):
        calls.append((method, path, deepcopy(kwargs["json"])))
        body = kwargs["json"]
        assert body["p_draft"] == body["p_publication"]["payload"]["plan"]
        assert body["p_draft"]["days"][0]["session"] == REFERENCE
        return {"saved": True, "revision": 4, "entry_key": "2026-09-22", "payload": body["p_draft"],
            "active_revision": 3, "recorded_at": NOW.isoformat()}
    repo = SimpleNamespace(_request=write, _json=lambda value: value)
    result = ManagementStore(repo).save_generated_plan("athlete", prepared, ACTOR, 1, 3)
    assert calls[0][:2] == ("POST", "/rpc/save_onflows_generated_plan")
    assert len(calls) == 1 and result["payload"] == draft and result["active_revision"] == 3


def test_generated_store_cannot_report_publication_after_a_conflict():
    from apps.api.management_store import ManagementStore
    calls = []
    def write(*a, **k):
        calls.append((a, k))
        return {"conflict": True, "reason": "REVISION_CHANGED"}
    repo = SimpleNamespace(_request=write, _json=lambda value: value)
    with pytest.raises(HTTPException) as exc:
        ManagementStore(repo).save_generated_plan("athlete", {"draft": {}, "publication": None}, ACTOR, 1)
    assert exc.value.status_code == 409 and len(calls) == 1


def test_job_receipt_reports_exact_published_revision_without_exposing_payload():
    result = planning_jobs.public_job({"job_id": "synthetic", "status": "SUCCEEDED", "stage": "READY",
        "result_revision": 4, "result_active_revision": 8, "published": True, "payload": {"private": True}})
    assert result["result_revision"] == 4 and result["result_active_revision"] == 8
    assert result["published"] is True and "payload" not in result


def test_unpublished_plan_scan_is_immediate_then_throttled(monkeypatch):
    import apps.api.oauth_store as storage
    repo = object.__new__(storage.SupabasePilotRepository)
    calls = []
    monkeypatch.setattr(repo, "_request", lambda *a, **k: calls.append((a, k)) or {"queued": 1})
    monkeypatch.setattr(repo, "_json", lambda value: value)
    clock = iter([100., 100.1, 159., 160.])
    monkeypatch.setattr(storage, "perf_counter", lambda: next(clock))
    assert repo.queue_unpublished_plans() == {"queued": 1}
    assert repo.queue_unpublished_plans()["deferred"] is True
    assert repo.queue_unpublished_plans()["deferred"] is True
    assert repo.queue_unpublished_plans() == {"queued": 1}
    assert len(calls) == 2


def test_generated_program_omits_obsolete_approval_warning(system):
    prepared = management_service.build_generated_plan(system.repo, "athlete", request(), ACTOR, now=NOW)
    assert all(w["code"] != "DRAFT_REQUIRES_COACH_REVIEW" for w in prepared["draft"]["warnings"])
    assert any(w["code"] == "CALENDAR_DAY_RESOLUTION" for w in prepared["draft"]["warnings"])


def test_worker_rpc_packs_both_draft_and_current_plan_identically():
    import json
    import httpx
    from apps.api.oauth_store import SupabasePilotRepository
    from apps.api.plan_storage import REFERENCE
    session = {"title": "Synthetic", "blocks": [{"seconds": 60}]}
    draft = {"schema_version": "planning-draft-v1", "days": [
        {"date": "2026-09-22", "session": session, "sessions": [session]}]}
    prepared = {"draft": draft, "publication": {"payload": {"schema_version": "active-plan-v2", "plan": draft},
        "expected_revision": 0, "checkpoint": {"generation_id": "synthetic"}}}
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "SUCCEEDED"})
    repo = SupabasePilotRepository(supabase_url="https://offline.invalid", secret_key="offline",
        encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        client=httpx.Client(transport=httpx.MockTransport(handler)))
    repo.planning_job("COMPLETE", job="synthetic", token="synthetic", payload=prepared)
    sent = bodies[0]["p_payload"]
    assert sent["draft"] == sent["publication"]["payload"]["plan"]
    assert sent["draft"]["days"][0]["session"] == REFERENCE
    assert len(bodies) == 1


def test_manual_refresh_transfers_background_ownership_to_current_authorized_coach(system):
    activate(system)
    new_actor = "22222222-2222-4222-8222-222222222222"
    result = lifecycle.refresh(system.repo, "athlete", new_actor, expected_revision=1, force=True, now=NOW)
    assert result["payload"]["approved_by"] == new_actor
    assert result["payload"]["status"] == "ACTIVE"
    assert system.plans.records[0]["payload"]["approved_by"] == ACTOR
