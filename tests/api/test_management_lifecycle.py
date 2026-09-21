from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from apps.api import management_lifecycle as lifecycle, management_service, training_plan_engine
from apps.api.management_schemas import ManagementProfile, ManagementActivateRequest, ManagementPlanAction, ManagementDayAction
from tests.api.test_training_plan_engine import Repository, NOW, TODAY, profile, reference_speed

ACTOR = "11111111-1111-4111-8111-111111111111"

class ActiveRepository(Repository):
    def active_analysis(self, alias):
        return deepcopy(self.envelope)
    def athlete_planning_calendar(self, alias):
        return SimpleNamespace(to_payload=lambda: {"events": deepcopy(self.events)})
    def athlete_planning_profile(self, alias):
        return SimpleNamespace(to_payload=lambda: deepcopy(self.preferences))
    def athlete_mesocycle_accent_preferences(self, alias):
        return SimpleNamespace(to_payload=lambda: deepcopy(self.accents))

class MemoryProfiles:
    def __init__(self):
        self.body = ManagementProfile.model_validate(profile(discipline="5000 m", age_years=30, training_experience_years=5))
        self.revision = 1
        self.records = []
    def profile(self, alias):
        return {"configured": True, "revision": self.revision, "profile": self.body.model_dump(mode="json")}
    def drafts(self, alias, **kwargs):
        return deepcopy(self.records)

class MemoryPlans:
    def __init__(self, repo):
        self.repo = repo
        self.records = []
        self.deferred = 0
    def checkpoint(self, alias):
        return {"generation_id": self.repo.envelope["generation_id"], "revision": self.repo.envelope["revision"]}
    def current(self, alias):
        return deepcopy(self.records[-1]) if self.records else None
    def history(self, alias):
        return deepcopy(self.records)
    def defer_check(self, alias, revision):
        self.deferred += 1
    def save(self, alias, payload, revision, actor, operation, profile_revision, checkpoint, **kwargs):
        if revision != len(self.records) or checkpoint != self.checkpoint(alias):
            raise HTTPException(409, "Changed")
        row = {"revision": revision+1, "payload": deepcopy(payload), "operation": operation, "recorded_at": NOW.isoformat()}
        self.records.append(row)
        return deepcopy(row)

@pytest.fixture
def system(monkeypatch):
    repo, profiles = ActiveRepository(), MemoryProfiles()
    plans = MemoryPlans(repo)
    monkeypatch.setattr(lifecycle, "ManagementStore", lambda r: profiles)
    monkeypatch.setattr(management_service, "ManagementStore", lambda r: profiles)
    monkeypatch.setattr(lifecycle, "PlanStore", lambda r: plans)
    monkeypatch.setattr(training_plan_engine.model_service, "speed_view", reference_speed)
    return SimpleNamespace(repo=repo, profiles=profiles, plans=plans)

def activate(system):
    draft = management_service.build_draft(system.repo, "athlete", TODAY+timedelta(days=1), 1, now=NOW)
    system.profiles.records = [{"revision": 1, "payload": draft}]
    request = ManagementActivateRequest(start_date=TODAY+timedelta(days=1), draft_revision=1, expected_revision=0)
    return lifecycle.activate(system.repo, "athlete", request, ACTOR, now=NOW)

def advance(system, days):
    source = system.repo.envelope["snapshot_payload"]["load_history"]
    source["period_end"] = (TODAY+timedelta(days=days)).isoformat()
    for n in range(1, days+1):
        day = (TODAY+timedelta(days=n)).isoformat()
        if not any(r["date"] == day for r in source["daily"]):
            source["daily"] += [{"date": day, "zone": z, "effective_load": 0.} for z in ("Z1", "Z2", "Z3", "Z4", "Z5")]
            source["strength"]["daily"].append({"date": day, "effective_load": 0.})
    system.repo.envelope.update(generation_id=f"generation-{days}", revision=days+1)
    return NOW+timedelta(days=days)

def test_activation_rollforward_reconciliation_and_idempotence(system):
    activated = activate(system)
    before = deepcopy(activated)
    assert activated["payload"]["status"] == "ACTIVE"
    now = advance(system, 2)
    updated = lifecycle.refresh(system.repo, "athlete", automatic=True, now=now)
    assert updated["revision"] == 2
    assert updated["payload"]["plan"]["start_date"] == now.date().isoformat()
    missed = next(o for o in updated["payload"]["outcomes"] if o["date"] == (TODAY+timedelta(days=1)).isoformat())
    assert missed["status"] == "MISSED" and missed["actual_minutes"] == 0 and not missed["catchup_required"]
    assert system.plans.records[0] == before
    lifecycle.refresh(system.repo, "athlete", automatic=True, now=now)
    assert len(system.plans.records) == 2 and system.plans.deferred == 1

def test_pause_survives_worker_and_manual_refresh(system):
    activate(system)
    lifecycle.action(system.repo, "athlete", ManagementPlanAction(action="PAUSE", expected_revision=1), ACTOR, now=NOW)
    now = advance(system, 1)
    lifecycle.refresh(system.repo, "athlete", automatic=True, now=now)
    lifecycle.action(system.repo, "athlete", ManagementPlanAction(action="REFRESH", expected_revision=2), ACTOR, now=now)
    assert len(system.plans.records) == 2
    assert system.plans.records[-1]["payload"]["status"] == "PAUSED"

def test_review_preserves_plan_until_explicit_approval(system):
    system.profiles.body.adaptation_mode = "REVIEW"
    original = activate(system)["payload"]["plan"]
    now = advance(system, 1)
    updated = lifecycle.refresh(system.repo, "athlete", automatic=True, now=now)
    assert updated["payload"]["status"] == "REVIEW_REQUIRED"
    assert updated["payload"]["plan"] == original and updated["payload"]["proposal"] is not None
    approved = lifecycle.action(system.repo, "athlete", ManagementPlanAction(action="APPROVE", expected_revision=2), ACTOR, now=now)
    assert approved["payload"]["status"] == "ACTIVE" and approved["payload"]["proposal"] is None

def test_changed_profile_requires_approval_in_auto_mode(system):
    activate(system)
    system.profiles.body.progression_percent = 7
    system.profiles.revision = 2
    updated = lifecycle.refresh(system.repo, "athlete", automatic=True, now=NOW)
    assert updated["payload"]["status"] == "REVIEW_REQUIRED"
    assert updated["payload"]["approved_profile_revision"] == 1
    assert updated["payload"]["proposal"]["input_snapshot"]["profile_revision"] == 2

def test_skip_does_not_invent_activity_and_stale_revision_conflicts(system):
    activate(system)
    before = deepcopy(system.repo.envelope)
    day = TODAY+timedelta(days=1)
    request = ManagementDayAction(date=day, action="SKIP", expected_revision=1)
    updated = lifecycle.refresh(system.repo, "athlete", ACTOR, expected_revision=1, now=NOW, day_action=request)
    skipped = next(d for d in updated["payload"]["plan"]["days"] if d["date"] == day.isoformat())
    assert skipped["status"] == "SKIPPED" and skipped["session"] is None
    assert system.repo.envelope == before
    with pytest.raises(HTTPException) as exc:
        lifecycle.refresh(system.repo, "athlete", ACTOR, expected_revision=1, now=NOW, day_action=request)
    assert exc.value.status_code == 409

def test_stale_history_suspends_future_prescription(system):
    activate(system)
    updated = lifecycle.refresh(system.repo, "athlete", automatic=True, now=NOW+timedelta(days=3))
    assert updated["payload"]["status"] == "REVIEW_REQUIRED"
    assert not updated["payload"]["proposal"]["activation_eligible"]
    assert all(d["session"] is None for d in updated["payload"]["proposal"]["days"])

def test_different_sport_and_unknown_day_do_not_claim_completion():
    day = (TODAY-timedelta(days=1)).isoformat()
    planned = {"days": [{"date": day, "session": {"sport": "Run", "title": "Run", "total_minutes": 30}}]}
    unknown = lifecycle.reconcile(planned, {}, TODAY, {})[0]
    assert unknown["status"] == "UNKNOWN" and unknown["actual_minutes"] is None
    recorded = lifecycle.reconcile(planned, {"activities": [{"date": day, "sport": "Ride", "duration_min": 60}]}, TODAY, {})[0]
    assert recorded["status"] == "DIFFERENT_ACTIVITY" and recorded["actual_minutes"] == 60


def test_late_import_corrects_an_old_missed_outcome():
    day = (TODAY-timedelta(days=5)).isoformat()
    previous = [{"date": day, "status": "MISSED", "planned_title": "Бягане", "planned_sport": "Run", "planned_minutes": 30, "actual_minutes": 0}]
    result = lifecycle.reconcile({"days": []}, {"activities": [{"date": day, "sport": "Run", "duration_min": 28}]}, TODAY, {}, previous)
    assert result[0]["status"] == "RECORDED" and result[0]["actual_minutes"] == 28


def test_approval_cannot_silently_regenerate_after_new_inputs(system):
    system.profiles.body.adaptation_mode = "REVIEW"
    activate(system)
    now = advance(system, 1)
    updated = lifecycle.refresh(system.repo, "athlete", automatic=True, now=now)
    system.repo.envelope.update(generation_id="another-generation", revision=99)
    with pytest.raises(HTTPException) as exc:
        lifecycle.action(system.repo, "athlete", ManagementPlanAction(action="APPROVE", expected_revision=2), ACTOR, now=now)
    assert exc.value.status_code == 409
    assert system.plans.records[-1] == updated
