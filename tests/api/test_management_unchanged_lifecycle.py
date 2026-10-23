"""A manual unchanged check preserves the entire approved/review envelope."""
from copy import deepcopy
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from apps.api import management_lifecycle as lifecycle, management_service
from apps.api.management_schemas import ManagementDayAction, ManagementPlanAction
from apps.api.oauth_store import PersistentStoreFailure
from tests.api.test_management_lifecycle import system, activate, advance, NOW, TODAY, ACTOR


def receipt_store(monkeypatch, system):
    receipts = []

    def record(alias, *, expected_revision, actor_id, expected_profile_revision, checkpoint):
        assert alias == "athlete" and actor_id == ACTOR
        assert expected_revision == system.plans.records[-1]["revision"]
        assert expected_profile_revision == system.profiles.revision
        assert checkpoint == system.plans.checkpoint(alias)
        receipt = {"unchanged": True, "checked_revision": expected_revision,
                   "check_id": str(UUID(int=len(receipts)+1)), "recorded_at": NOW.isoformat(), "replayed": False}
        receipts.append(deepcopy(receipt))
        return receipt

    monkeypatch.setattr(system.plans, "record_unchanged_check", record, raising=False)
    return receipts


def refresh(system, *, enabled=True, revision=None, now=NOW):
    body = ManagementPlanAction(action="REFRESH", expected_revision=revision or len(system.plans.records),
                                record_unchanged_check=enabled)
    return lifecycle.action(system.repo, "athlete", body, ACTOR, now=now)


def normalized(monkeypatch, system):
    """Use a real generated draft, then isolate lifecycle envelope decisions."""
    activated = activate(system)
    draft = deepcopy(activated["payload"]["plan"])
    draft.update(review_required=True, automatically_published=False)
    monkeypatch.setattr(management_service, "build_draft", lambda *args, **kwargs: deepcopy(draft))
    receipts = receipt_store(monkeypatch, system)
    result = refresh(system)
    assert result["revision"] == 2 and not receipts
    return receipts


@pytest.mark.parametrize("mode", ["AUTO", "REVIEW"])
def test_two_real_manual_recalculations_write_once_then_only_record_the_check(monkeypatch, system, mode):
    system.profiles.body.adaptation_mode = mode
    activated = activate(system)
    approved = deepcopy(activated["payload"]["plan"])
    receipts = receipt_store(monkeypatch, system)
    first = refresh(system)
    assert first["revision"] == 2 and len(system.plans.records) == 2 and not receipts
    assert system.plans.records[0] == activated
    frozen = deepcopy(system.plans.records)
    second = refresh(system)
    assert second["revision"] == first["revision"]
    assert second["payload"] == first["payload"]
    assert system.plans.records == frozen
    assert second["unchanged_check"] == receipts[0]
    assert second == {**first, "unchanged_check": receipts[0]}
    assert "unchanged_check" not in first and "unchanged_check" not in second["payload"]
    assert len(receipts) == 1 and receipts[0]["checked_revision"] == 2
    if mode == "REVIEW":
        assert second["payload"]["status"] == "ACTIVE"
        assert second["payload"]["proposal"] is None
        assert second["payload"]["plan"] == approved
        assert second["payload"]["approved_by"] == ACTOR
    else:
        assert second["payload"]["status"] == "ACTIVE"


def test_legacy_refresh_still_appends_an_identical_full_revision(monkeypatch, system):
    receipts = normalized(monkeypatch, system)
    before = deepcopy(system.plans.records[-1]["payload"])
    request = ManagementPlanAction(action="REFRESH", expected_revision=2)
    assert request.record_unchanged_check is False
    result = lifecycle.action(system.repo, "athlete", request, ACTOR, now=NOW)
    assert result["revision"] == 3 and result["payload"] == before
    assert len(system.plans.records) == 3 and not receipts


@pytest.mark.parametrize("difference", ["evidence", "boolean_number", "missing_null", "null_missing", "reason", "outcomes", "input"])
def test_any_envelope_difference_requires_a_full_revision_even_when_workouts_match(monkeypatch, system, difference):
    receipts = normalized(monkeypatch, system)
    old = system.plans.records[-1]["payload"]
    candidate = deepcopy(old["plan"])
    workouts = deepcopy(candidate["days"])
    if difference == "evidence":
        candidate["parameters"]["diagnostic_note"] = "Additional dosage evidence"
    elif difference == "boolean_number":
        old["plan"]["diagnostic_flag"] = True
        candidate["diagnostic_flag"] = 1
    elif difference == "missing_null":
        candidate["diagnostic_note"] = None
    elif difference == "null_missing":
        old["plan"]["diagnostic_note"] = None
    elif difference == "reason":
        old["reason"] = "A previously stored explanation"
    elif difference == "outcomes":
        old["outcomes"] = [{"date": (TODAY-timedelta(days=1)).isoformat(), "status": "MISSED",
                            "planned_title": "Earlier run", "planned_sport": "Run", "planned_minutes": 30.,
                            "actual_minutes": 999., "planned_load": None}]
    else:
        old["processed_input_fingerprint"] = "previous-input-fingerprint"
    previous = deepcopy(system.plans.records)
    monkeypatch.setattr(management_service, "build_draft", lambda *args, **kwargs: deepcopy(candidate))
    result = refresh(system)
    assert result["revision"] == 3 and not receipts
    assert system.plans.records[:-1] == previous
    assert result["payload"]["plan"]["days"] == workouts
    assert lifecycle._hash(result["payload"]) != lifecycle._hash(previous[-1]["payload"])


@pytest.mark.parametrize("decision", ["REST", "CLEAR"])
def test_a_day_decision_is_a_full_write_even_if_the_builder_keeps_the_workouts(monkeypatch, system, decision):
    receipts = normalized(monkeypatch, system)
    original = deepcopy(system.plans.records[-1])
    action = ManagementDayAction(date=TODAY+timedelta(days=1), action=decision, note="Coach decision", expected_revision=2)
    result = lifecycle.refresh(system.repo, "athlete", ACTOR, expected_revision=2, now=NOW,
                               force=True, day_action=action, record_unchanged_check=True)
    assert result["revision"] == 3 and result["operation"] == "DAY" and not receipts
    assert result["payload"]["plan"]["days"] == original["payload"]["plan"]["days"]
    if decision == "REST":
        assert result["payload"]["decisions"][action.date.isoformat()] == {"action": "REST", "note": "Coach decision"}
    else:
        assert result["payload"] == original["payload"]


def test_new_analysis_identity_gets_a_full_revision_even_when_real_recalculation_keeps_the_workouts(monkeypatch, system):
    real_build = management_service.build_draft
    receipts = normalized(monkeypatch, system)
    previous = deepcopy(system.plans.records[-1])
    monkeypatch.setattr(management_service, "build_draft", real_build)
    now = advance(system, 0)
    result = refresh(system, now=now)

    def workouts(plan):
        return [(day["date"], day["status"], [
            {key: session.get(key) for key in ("method_id", "purpose", "blocks", "total_minutes",
                                              "canonical_effective_load", "direct_equivalent_minutes")}
            for session in lifecycle.day_sessions(day)]) for day in plan["days"]]

    assert result["revision"] == 3 and not receipts
    assert workouts(result["payload"]["plan"]) == workouts(previous["payload"]["plan"])
    assert result["payload"]["processed_input_fingerprint"] != previous["payload"]["processed_input_fingerprint"]
    assert result["payload"]["plan"]["source"]["generation_id"] == system.repo.envelope["generation_id"]
    assert system.plans.records[-2] == previous


@pytest.mark.parametrize("scope", ["automatic", "resume", "not_forced", "complete"])
def test_opt_in_flag_does_not_enable_unchanged_receipts_outside_manual_forced_refresh(monkeypatch, system, scope):
    receipts = normalized(monkeypatch, system)
    previous = deepcopy(system.plans.records[-1])
    if scope == "automatic":
        system.plans.records[-1]["payload"]["plan"]["automatically_published"] = True
        candidate = deepcopy(system.plans.records[-1]["payload"]["plan"])
        monkeypatch.setattr(management_service, "build_draft", lambda *args, **kwargs: deepcopy(candidate))
        previous = deepcopy(system.plans.records[-1])
        result = lifecycle.refresh(system.repo, "athlete", ACTOR, now=NOW, force=True,
                                   automatic=True, record_unchanged_check=True)
        assert result["operation"] == "ADAPT" and result["revision"] == 3
        assert result["payload"] == previous["payload"]
    elif scope == "resume":
        result = lifecycle.refresh(system.repo, "athlete", ACTOR, now=NOW, force=True,
                                   resume=True, record_unchanged_check=True)
        assert result["operation"] == "RESUME" and result["revision"] == 3
        assert result["payload"] == previous["payload"]
    elif scope == "not_forced":
        result = lifecycle.refresh(system.repo, "athlete", ACTOR, now=NOW, record_unchanged_check=True)
        assert result == previous and len(system.plans.records) == 2
        assert system.plans.deferred == 1
    else:
        system.profiles.body.horizon_mode = "MANUAL"
        system.profiles.body.program_end = TODAY-timedelta(days=1)
        system.profiles.revision += 1
        completed = refresh(system)
        assert completed["operation"] == "COMPLETE"
        result = refresh(system)
        assert result["operation"] == "COMPLETE" and result["revision"] == 4
        assert result["payload"] == completed["payload"]
    assert not receipts


def test_receipt_failure_never_falls_back_to_a_full_plan_write(monkeypatch, system):
    normalized(monkeypatch, system)
    before = deepcopy(system.plans.records)

    def unavailable(*args, **kwargs):
        raise PersistentStoreFailure("Check storage unavailable")

    monkeypatch.setattr(system.plans, "record_unchanged_check", unavailable)
    with pytest.raises(PersistentStoreFailure, match="Check storage unavailable"):
        refresh(system)
    assert system.plans.records == before


def test_stale_expected_revision_is_rejected_before_any_unchanged_receipt(monkeypatch, system):
    receipts = normalized(monkeypatch, system)
    before = deepcopy(system.plans.records)
    with pytest.raises(HTTPException) as error:
        refresh(system, revision=1)
    assert error.value.status_code == 409 and not receipts
    assert system.plans.records == before


@pytest.mark.parametrize("before,after", [({"value": True}, {"value": 1}), ({"value": False}, {"value": 0}),
                                         ({}, {"value": None}), ({"value": 1}, {"value": 1.0})])
def test_complete_json_hash_keeps_semantic_type_and_presence_differences(before, after):
    assert lifecycle._hash(before) != lifecycle._hash(after)


def test_complete_json_hash_ignores_only_object_key_order():
    assert lifecycle._hash({"b": 2, "a": {"y": False, "x": None}}) == lifecycle._hash(
        {"a": {"x": None, "y": False}, "b": 2})
    assert lifecycle._hash({"days": [1, 2]}) != lifecycle._hash({"days": [2, 1]})


@pytest.mark.parametrize("action", ["PAUSE", "RESUME", "REFRESH", "APPROVE"])
def test_action_opt_in_defaults_false_for_every_legacy_action(action):
    assert ManagementPlanAction(action=action, expected_revision=1).record_unchanged_check is False


@pytest.mark.parametrize("action", ["PAUSE", "RESUME", "APPROVE"])
def test_action_opt_in_is_accepted_only_for_refresh(action):
    with pytest.raises(ValidationError):
        ManagementPlanAction(action=action, expected_revision=1, record_unchanged_check=True)


@pytest.mark.parametrize("value", [0, 1, "true", "false", None, [], {}])
def test_action_opt_in_requires_an_actual_boolean(value):
    with pytest.raises(ValidationError):
        ManagementPlanAction(action="REFRESH", expected_revision=1, record_unchanged_check=value)
