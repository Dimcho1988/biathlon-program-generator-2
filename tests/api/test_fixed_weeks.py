"""A frozen prescription and a fresh monitoring record are separate contracts."""
from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi import HTTPException

from apps.api import management_lifecycle as lifecycle, management_service
from apps.api.management_schemas import ManagementPlanAction, ManagementDayAction
from tests.api.test_management_lifecycle import system, activate, advance, ACTOR, TODAY, NOW
from tests.api.test_training_plan_engine import append_complete_activity


def fixed(system):
    system.profiles.body.adaptation_mode = "REVIEW"
    return activate(system)


def approve(system, record, now):
    return lifecycle.action(system.repo, "athlete", ManagementPlanAction(
        action="APPROVE", expected_revision=record["revision"]), ACTOR, now=now)


def test_fixed_week_same_tasks_are_fresh_without_reapproval_or_rolling(system):
    initial = fixed(system)
    now = advance(system, 1)
    updated = lifecycle.refresh(system.repo, "athlete", automatic=True, now=now)
    p = updated["payload"]
    assert p["plan"] == initial["payload"]["plan"]
    assert p["week_window"] == initial["payload"]["week_window"]
    assert p["status"] == "ACTIVE" and p["proposal"] is None
    assert p["monitoring"]["evaluated_on"] == now.date().isoformat()
    current = lifecycle.current(system.repo, "athlete", now=now)["active"]
    assert not current["stale"] and current["actionable"]
    assert lifecycle.refresh(system.repo, "athlete", automatic=True, now=now)["revision"] == updated["revision"]


def test_extra_unplanned_load_changes_monitoring_and_proposal_not_fixed_doses(system):
    initial = fixed(system)
    now = advance(system, 2)
    source = system.repo.envelope["snapshot_payload"]["load_history"]
    day = (TODAY+timedelta(days=1)).isoformat()
    activity = deepcopy(source["activities"][0])
    activity.update(activity_ref="extra-unplanned", date=day, duration_min=180)
    for row in activity["zones"]:
        row["equivalent_time_min"] = 240. if row["zone"] == "Z3" else 0.
    append_complete_activity(source, activity)
    changed = lifecycle.refresh(system.repo, "athlete", automatic=True, now=now)
    p = changed["payload"]
    assert p["plan"] == initial["payload"]["plan"]
    assert p["status"] == "REVIEW_REQUIRED" and p["changes"]
    assert p["proposal"]["end_date"] == initial["payload"]["plan"]["end_date"]
    assert all(d["date"] <= p["week_window"]["end_date"] for d in p["proposal"]["days"])
    outcome = next(o for o in p["outcomes"] if o["date"] == day)
    assert outcome["actual_minutes"] == 180 and outcome["actual_load"]["Z3"] == 240
    assert outcome["load_source"] == "IMPORTED_ACTUAL_ONLY" and not outcome["catchup_required"]
    assert any(d["status"] == "REVIEW_REQUIRED" for d in p["monitoring"]["days"])


def test_fixed_day_decision_requires_approval_and_does_not_roll_week(system):
    initial = fixed(system)
    day = TODAY+timedelta(days=2)
    changed = lifecycle.refresh(system.repo, "athlete", ACTOR, now=NOW, expected_revision=1,
        day_action=ManagementDayAction(date=day, action="REST", expected_revision=1))
    assert changed["payload"]["plan"] == initial["payload"]["plan"]
    assert changed["payload"]["proposal"] is not None
    approved = approve(system, changed, NOW)
    expected = deepcopy(changed["payload"]["proposal"])
    expected.update(review_required=False, automatically_published=False)
    assert approved["payload"]["plan"] == expected
    assert all(d["status"] != "REVIEW_REQUIRED" for d in approved["payload"]["monitoring"]["days"])
    assert approved["payload"]["week_window"] == initial["payload"]["week_window"]
    assert next(d for d in expected["days"] if d["date"] == day.isoformat())["session"] is None


@pytest.mark.parametrize("before,after", [("AUTO", "REVIEW"), ("REVIEW", "AUTO")])
def test_mode_switch_previews_before_replacing_future_prescriptions(system, before, after):
    system.profiles.body.adaptation_mode = before
    initial = activate(system)
    system.profiles.body.adaptation_mode = after
    system.profiles.revision += 1
    now = advance(system, 2)
    changed = lifecycle.refresh(system.repo, "athlete", automatic=True, now=now)
    assert changed["payload"]["mode"] == before
    assert changed["payload"]["proposal_mode"] == after
    assert changed["payload"]["plan"] == initial["payload"]["plan"]
    assert changed["payload"]["status"] == "REVIEW_REQUIRED"
    assert all(d["date"] >= now.date().isoformat() for d in changed["payload"]["proposal"]["days"])
    approved = approve(system, changed, now)
    assert approved["payload"]["mode"] == after
    if after == "REVIEW":
        assert approved["payload"]["week_window"] == initial["payload"]["week_window"]
    else:
        assert approved["payload"]["plan"]["end_date"] == (now.date()+timedelta(days=6)).isoformat()


def test_next_fixed_week_is_anchored_and_waits_for_approval(system):
    initial = fixed(system)
    now = advance(system, 8)
    source = system.repo.envelope["snapshot_payload"]["load_history"]
    for n in (1, 4, 7):
        activity = deepcopy(source["activities"][0])
        activity.update(activity_ref=f"recent-{n}", date=(TODAY+timedelta(days=n)).isoformat(), duration_min=30)
        for row in activity["zones"]:
            row["equivalent_time_min"] = 20. if row["zone"] == "Z1" else 0.
        append_complete_activity(source, activity)
    changed = lifecycle.refresh(system.repo, "athlete", automatic=True, now=now)
    assert changed["payload"]["plan"] == initial["payload"]["plan"]
    assert changed["payload"]["status"] == "REVIEW_REQUIRED"
    assert changed["payload"]["proposal_week_window"] == {
        "start_date": (TODAY+timedelta(days=8)).isoformat(), "end_date": (TODAY+timedelta(days=14)).isoformat()}
    now = advance(system, 9)
    later = lifecycle.refresh(system.repo, "athlete", automatic=True, now=now)
    assert later["payload"]["proposal_week_window"] == changed["payload"]["proposal_week_window"]
    assert later["payload"]["proposal"]["end_date"] == (TODAY+timedelta(days=14)).isoformat()
    approved = approve(system, later, now)
    assert approved["payload"]["week_window"] == later["payload"]["proposal_week_window"]
    assert approved["payload"]["plan"]["start_date"] == now.date().isoformat()


def test_missing_history_does_not_validate_the_frozen_week(system):
    initial = fixed(system)
    changed = lifecycle.refresh(system.repo, "athlete", automatic=True, now=NOW+timedelta(days=3))
    assert changed["payload"]["plan"] == initial["payload"]["plan"]
    assert not changed["payload"]["proposal"]["activation_eligible"]
    assert not lifecycle.current(system.repo, "athlete", now=NOW+timedelta(days=3))["active"]["actionable"]
    with pytest.raises(HTTPException) as exc:
        approve(system, changed, NOW+timedelta(days=3))
    assert exc.value.status_code == 409


def test_today_recording_updates_execution_without_claiming_missed_or_erasing_original():
    day = TODAY.isoformat()
    original = {"days": [{"date": day, "session": {"sport": "Run", "title": "approved", "total_minutes": 30}}]}
    assert lifecycle.reconcile(original, {}, TODAY, {}) == []
    source = {"activities": [{"date": day, "sport": "Run", "duration_min": 65, "activity_ref": "actual"}]}
    outcomes = lifecycle.reconcile(original, source, TODAY, {})
    assert outcomes[0]["planned_minutes"] == 30 and outcomes[0]["actual_minutes"] == 65
    assert outcomes[0]["day_complete"] is False
    remaining = {"days": [{"date": day, "status": "EXISTING_ACTIVITY", "session": None}]}
    next_day = lifecycle.reconcile(remaining, source, TODAY+timedelta(days=1), {}, outcomes)
    assert next_day[0]["planned_minutes"] == 30 and next_day[0]["actual_minutes"] == 65


def test_generator_respects_explicit_remaining_week_and_cache_key(system):
    first = TODAY+timedelta(days=1)
    bounded = management_service.build_draft(system.repo, "athlete", first, 1, now=NOW,
                                              window_end=first+timedelta(days=2))
    rolling = management_service.build_draft(system.repo, "athlete", first, 1, now=NOW)
    assert len(bounded["days"]) == 3 and len(rolling["days"]) == 7
    assert bounded["end_date"] == (first+timedelta(days=2)).isoformat()
    assert bounded["operational_context"]["window_end"] == bounded["end_date"]
    assert bounded["fingerprint"] != rolling["fingerprint"]
