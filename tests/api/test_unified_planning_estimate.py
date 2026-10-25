"""Unknown selected time can support planning without rewriting observations."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import planning_history_estimate as estimate, training_plan_engine as engine
from apps.api.management_lifecycle import _eligible
from tests.api.test_management_lifecycle import system, activate, ACTOR
from tests.api.test_load_progression import configured, observed
from tests.api.test_training_plan_engine import TODAY, NOW, reference_speed


def selected_history(*, unknown=60., rest=120., fallback=180.):
    repo, source, _ = observed()
    # Real ledgers contain all five zones; use conserved raw time, independent
    # of Q. Moving duration and HR coverage deliberately disagree with elapsed.
    for a in source["activities"]:
        a["quality_status"] = "valid"
    activity = source["activities"][-1]
    active = sum(z["raw_time_min"] for z in activity["zones"]) * 60
    activity.update(quality_status="limited" if unknown else "valid", hr_coverage_percent=0.)
    activity["load_source"] = {
        "version": "unified-test", "recorded_seconds": active+rest+unknown,
        "active_seconds": active, "rest_seconds": rest, "unknown_seconds": unknown,
        "source_seconds": {"HEART_RATE": active+rest-fallback, "SPEED": fallback},
        "fallback_seconds": fallback, "leading_indicator": "HEART_RATE"}
    source["load_selection"] = {"version": "unified-test", "unknown_seconds": unknown}
    source["quality"]["limited_activities"] = int(unknown > 0)
    return repo, source, activity


def test_only_unknown_selected_time_is_estimated_and_source_is_immutable():
    repo, source, activity = selected_history()
    original = deepcopy(source)
    # Any legacy speed estimate is already superseded by measured fallback.
    speed = {activity["activity_ref"]: {"covered_missing_minutes": 99.}}
    ledger, info = estimate.prepare(source, repo.envelope["activities"], configured(), TODAY, speed_estimates=speed)
    assert info["supported"] and info["estimated"]
    assert info["policy"] == estimate.UNIFIED_POLICY
    assert info["estimated_minutes"] == 1.
    assert info["speed_estimated_activity_count"] == 0
    assert sum(info["activities"][0]["estimated_q"].values()) == pytest.approx(1.)
    addition = next(a for a in ledger["activities"] if a["activity_ref"] == activity["activity_ref"])
    assert addition["load_source"] == activity["load_source"]
    assert addition["duration_min"] == activity["duration_min"]
    assert sum(z["raw_time_min"] for z in addition["zones"]) == pytest.approx(51.)
    assert source == original
    assert ledger["quality"] == source["quality"]
    assert ledger["load_selection"] == source["load_selection"]
    assert info["load_coverage"]["unknown_seconds"] == 60.
    with pytest.raises(ValueError, match="cannot become"):
        estimate.prepare(ledger, [], configured(), TODAY)


def test_known_rest_and_speed_fallback_never_trigger_estimation():
    repo, source, _ = selected_history(unknown=0.)
    ledger, info = estimate.prepare(source, repo.envelope["activities"], configured(), TODAY)
    assert info["supported"] and not info["estimated"]
    assert info["estimated_minutes"] == 0
    assert ledger["activities"] == source["activities"]


@pytest.mark.parametrize("change", [
    {"recorded_seconds": None}, {"unknown_seconds": None}, {"unknown_seconds": -1.},
    {"active_seconds": 99.}, {"recorded_seconds": 1.},
])
def test_missing_or_inconsistent_coverage_keeps_programme_blocked(change):
    repo, source, activity = selected_history()
    activity["load_source"].update(change)
    _, info = estimate.prepare(source, repo.envelope["activities"], configured(), TODAY)
    assert not info["supported"]
    assert info["estimated_minutes"] == 0
    assert info["unresolved_activity_refs"] == [activity["activity_ref"]]


def test_missing_provider_record_or_calendar_day_is_not_completed():
    repo, source, activity = selected_history()
    catalog = [{"activity_ref": "missing", "local_date": activity["date"],
                "sport": "Run", "duration_min": 60., "quality_status": "provider_missing"}]
    _, info = estimate.prepare(source, catalog, configured(), TODAY)
    assert not info["supported"] and "missing" in info["unresolved_activity_refs"]
    source["daily"] = [r for r in source["daily"] if r["date"] != activity["date"]]
    _, info = estimate.prepare(source, [], configured(), TODAY)
    assert not info["supported"] and activity["date"] in info["missing_calendar_days"]


def test_unidentified_excluded_record_is_not_hidden_by_known_estimates():
    repo, source, _ = selected_history()
    source["quality"]["excluded_activities"] = 1
    _, info = estimate.prepare(source, repo.envelope["activities"], configured(), TODAY)
    assert not info["supported"]
    assert "UNIDENTIFIED_INCOMPLETE_ACTIVITIES" in info["unresolved_activity_refs"]


@pytest.mark.parametrize("mode", ["AUTO", "REVIEW"])
def test_unified_partial_history_supports_reviewable_plan_in_both_week_modes(monkeypatch, mode):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    repo, source, _ = selected_history()
    original = deepcopy(source)
    plan = engine.generate_plan(repo, "athlete", configured(adaptation_mode=mode),
                                start_date=TODAY+timedelta(days=1), now=NOW)
    assert plan["activation_eligible"] and _eligible(plan)
    assert plan["source"]["readiness_basis"] == "ESTIMATED_LOAD"
    assert plan["source"]["readiness_known"] is False
    assert plan["source"]["planning_history"]["policy"] == estimate.UNIFIED_POLICY
    assert not any(w["code"] == "LIMITED_LOAD_HISTORY" for w in plan["warnings"])
    assert source == original


def test_fixed_to_adaptive_proposal_can_be_approved_with_separate_estimated_load(system):
    from apps.api import management_lifecycle as lifecycle
    from apps.api.management_schemas import ManagementPlanAction

    system.profiles.body.adaptation_mode = "REVIEW"
    approved_before = activate(system)["payload"]["plan"]
    _, selected, _ = selected_history()
    source = system.repo.envelope["snapshot_payload"]["load_history"]
    source["load_selection"] = deepcopy(selected["load_selection"])
    partial = source["activities"][-1]
    partial.update(quality_status="limited", load_source=deepcopy(selected["activities"][-1]["load_source"]))
    source["quality"]["limited_activities"] = 1
    measured = deepcopy(source)
    system.profiles.body.adaptation_mode = "AUTO"
    system.profiles.revision = 2

    pending = lifecycle.refresh(system.repo, "athlete", automatic=True, now=NOW)
    assert pending["payload"]["status"] == "REVIEW_REQUIRED"
    assert pending["payload"]["mode"] == "REVIEW"
    assert pending["payload"]["proposal_mode"] == "AUTO"
    assert pending["payload"]["plan"] == approved_before
    assert _eligible(pending["payload"]["proposal"])
    result = lifecycle.action(system.repo, "athlete",
        ManagementPlanAction(action="APPROVE", expected_revision=pending["revision"]), ACTOR, now=NOW)
    assert result["payload"]["status"] == "ACTIVE"
    assert result["payload"]["mode"] == "AUTO"
    assert result["payload"]["proposal"] is None
    assert result["payload"]["plan"]["source"]["readiness_basis"] == "ESTIMATED_LOAD"
    assert source == measured

