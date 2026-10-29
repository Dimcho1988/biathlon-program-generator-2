"""The selected speed evidence must survive all the way to real draft doses."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from apps.api import management_service, model_service, training_plan_engine as engine
from biathlon import speed_anchor_policy
from tests.api.test_load_progression import configured
from tests.api.test_planning_history_estimate import incomplete
from tests.api.test_readiness_adaptive_plan_v2 import assert_readiness_dose, assert_rolling_budgets
from tests.api.test_speed_history_window import index
from tests.api.test_training_plan_engine import NOW, TODAY
from tests.api.test_training_plan_engine import Repository as PlanningRepository, supported_speed


@pytest.mark.parametrize("test_count", [0, 1, 2])
def test_stale_hr_then_no_hr_uses_the_available_speed_basis_for_a_bounded_draft(monkeypatch, test_count):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(model_service, "datetime", FixedDatetime)
    repo, source = incomplete(30)
    paired = next(a for a in repo.envelope["activities"]
                  if a["date"] == (TODAY-timedelta(days=28)).isoformat())
    paired["latest_shadow_run_key"] = "last-paired-observation"
    repo.trainability_summaries = lambda _, keys: {
        "last-paired-observation": {"activity_ref": paired["activity_ref"], "trainability_index": index()}}
    # A real recording with no usable HR remains explicitly incomplete in the
    # measured ledger. Planning can use the existing, disclosed estimate path.
    latest = {"activity_ref": "no-hr-recent", "date": (TODAY-timedelta(days=1)).isoformat(),
              "sport": "Run", "duration_min": 45., "quality_status": "limited", "hr_coverage_percent": 0.,
              "zones": [{"zone": f"Z{i}", "raw_time_min": 0., "equivalent_time_min": 0., "effective_load": 0.}
                        for i in range(1, 6)]}
    source["activities"].append(latest)
    source["quality"]["limited_activities"] += 1
    repo.envelope["activities"].append({**deepcopy(latest), "local_date": latest["date"]})
    by_ref = {a["activity_ref"]: a for a in source["activities"]}
    for activity in source["activities"]:
        if activity.get("quality_status") != "limited":
            activity["duration_min"] = sum(z["raw_time_min"] for z in activity["zones"])
    for activity in repo.envelope["activities"]:
        activity["duration_min"] = by_ref[activity["activity_ref"]]["duration_min"]
        activity.update(recording_time_s=activity["duration_min"]*60,
                        moving_time_s=activity["duration_min"]*60,
                        distance_m=activity["duration_min"]*10000/60,
                        quality_status=by_ref[activity["activity_ref"]].get("quality_status", "valid"),
                        hr_coverage_percent=by_ref[activity["activity_ref"]].get("hr_coverage_percent", 100.))
    body = configured(availability_mode="AUTO_HISTORY", training_experience_years=6.)
    repo.entries = [{"kind": "SPEED_TEST", "entry_key": str(n), "revision": 1,
        "payload": {"source": "MANUAL", "sport": "Run", "day": (TODAY-timedelta(days=2)).isoformat(),
                    "enabled": True, "maximal": True, "comparable": True,
                    "duration_s": duration, "speed_kmh": speed}}
        for n, (duration, speed) in enumerate([(145., 800/145*3.6), (1000., 14.4)][:test_count])]
    def request(method, path, **kwargs):
        assert method == "GET"
        if path.startswith("/onflows_management_entries?"):
            return [{"kind": "PROFILE", "entry_key": "management-profile-v1", "revision": 1, "payload": deepcopy(body)}]
        return deepcopy(repo.entries) if path.startswith("/onflows_model_entries?") else []
    repo._request = request
    original = deepcopy(repo.envelope)
    speed = model_service.speed_view(repo, "athlete", "Run")
    assert speed["index_freshness"]["bands"]["Z3"]["status"] == "STALE"
    assert speed["index_window"]["last_activity_date"] is None
    assert speed["active_test_count"] == test_count
    if test_count:
        assert all(z["source"] == "INITIAL_V50_V6_ESTIMATE" for z in speed["speed_zones"]["zones"][:4])
    else:
        assert speed["status"] == "INITIAL_HEURISTIC", speed["initial_threshold"]
        assert speed["speed_role"] == "RAW_MOVING_SPEED"

    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY+timedelta(days=1), now=NOW)
    sessions = [s for day in plan["days"] for s in day["sessions"]]
    assert plan["status"] == "DRAFT" and sessions, [w["code"] for w in plan["warnings"]]
    expected_capacity = "TEST_CURVE_INITIAL_HR_ESTIMATE" if test_count else "INITIAL_HEURISTIC"
    assert any(s["dose_evidence"]["capacity_source"] == expected_capacity for s in sessions)
    assert all("STALE_HR_SPEED_INDEX" not in s["dose_evidence"].get("fallback_reasons", []) for s in sessions)
    assert 0 < plan["summary"]["planned_minutes"] <= plan["parameters"]["time_budget"]["period_limit_minutes"]+.002
    assert any(w["code"] == "ESTIMATED_LOAD_HISTORY" for w in plan["warnings"])
    assert_rolling_budgets(plan)
    for session in sessions:
        assert_readiness_dose(session)
    assert repo.envelope == original


@pytest.mark.parametrize("future_days", [1, 6])
def test_freshness_boundary_is_evaluated_when_model_is_selected(future_days):
    settings = PlanningRepository().settings
    speed = supported_speed(settings)
    speed["index_window"]["last_activity_date"] = (TODAY-timedelta(days=14)).isoformat()
    speed["index_freshness"] = {"version": speed_anchor_policy.VERSION, "as_of": TODAY.isoformat()}
    method = next(m for m in engine.METHODS if m["zone"] == "Z3")
    context = engine._capacity_context(speed, settings)
    target_day = TODAY+timedelta(days=future_days)
    capacity = engine.capacity_for(method, settings, speed, context, target_day)
    assert capacity is not None and capacity["capacity_source"] == "SPEED_DURATION"
    # Legacy metadata cannot bypass the existing stale-observation gate.
    speed["index_freshness"]["version"] = "unknown-selector"
    assert engine.capacity_for(method, settings, speed, context, target_day) is None


@pytest.mark.parametrize("policy", ["freshness", "historical"])
def test_speed_anchor_policy_change_invalidates_frozen_management_inputs(monkeypatch, policy):
    repository = SimpleNamespace(
        athlete_settings=lambda _: None, active_analysis=lambda _: None,
        athlete_planning_calendar=lambda _: None, athlete_planning_profile=lambda _: None,
        athlete_mesocycle_accent_preferences=lambda _: None,
    )
    monkeypatch.setattr(management_service, "ModelStore", lambda _: SimpleNamespace(entries=lambda _: []))
    before = management_service.input_state(repository, "athlete", evaluated_at=NOW)
    assert before["rule_versions"]["speed_anchor_policy"] == speed_anchor_policy.VERSION
    assert before["rule_versions"]["historical_speed_policy"] == model_service.HISTORICAL_SPEED_POLICY
    if policy == "freshness":
        monkeypatch.setattr(speed_anchor_policy, "VERSION", "next-speed-anchor-policy")
    else:
        monkeypatch.setattr(model_service, "HISTORICAL_SPEED_POLICY", "next-historical-speed-policy")
    after = management_service.input_state(repository, "athlete", evaluated_at=NOW)
    assert before["generation_id"] == after["generation_id"]
    assert before["rule_versions"]["engine"] == after["rule_versions"]["engine"]
    assert management_service._hash(before) != management_service._hash(after)
