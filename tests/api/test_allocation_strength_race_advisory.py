"""A remainder is not a new quota; race readiness warnings never veto doses."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import training_plan_engine as engine
from apps.api.planning_search import improve
from biathlon import planning_allocation
from tests.api.test_management_schedule import body, run
from tests.api.test_training_plan_engine import Repository, TODAY


def test_next_microcycle_cannot_authorize_balancing_today_into_two_doses():
    tomorrow = TODAY + timedelta(days=1)
    slots = [(TODAY, 0), (tomorrow, 0), (tomorrow, 1)]
    keys = {TODAY: "RECOVERY", tomorrow: "BUILD"}
    count = planning_allocation.remaining_slots(slots, TODAY, 0, segment_keys=keys)
    assert count == 1
    assert planning_allocation.dose_share(180., 50., 100., count) is None
    # Within one microcycle two remaining doses can still be balanced.
    assert planning_allocation.remaining_slots(slots, tomorrow, 1, segment_keys=keys) == 1
    assert planning_allocation.dose_share(180., 50., 100.,
        planning_allocation.remaining_slots(slots, tomorrow, 0, segment_keys=keys)) == 90.


@pytest.mark.parametrize("strength_type", ["STR_MAX", "STR_END"])
def test_strength_main_exposure_includes_passive_pauses_once_and_excludes_preparation(strength_type):
    blocks = [{"zone": "STR", "kind": "WARMUP", "duration_min": 10},
              {"zone": "STR", "kind": "WORK", "duration_min": 1, "strength_type": strength_type},
              {"zone": "STR", "kind": "RECOVERY", "duration_min": 3, "passive_rest": True},
              {"zone": "STR", "kind": "TRANSITION", "duration_min": .5},
              {"zone": "STR", "kind": "WORK", "duration_min": 1, "strength_type": strength_type},
              {"zone": "STR", "kind": "COOLDOWN", "duration_min": 5}]
    before = deepcopy(blocks)
    direct, effective, _ = engine._canonical_load(blocks, Repository().settings, [], TODAY)
    assert direct["STR"] == effective["STR"] == 5.5
    assert all(value == 0 for z,value in direct.items() if z != "STR")
    assert engine._strength_duration(blocks) == {
        "work_minutes": 2., "rest_minutes": 3.5, "main_minutes": 5.5,
        "basis": "WORK_AND_INTERSET_REST_EXCLUDING_WARMUP_COOLDOWN"}
    assert blocks == before


def test_maintenance_circuit_shortfall_is_not_missing_rest_time():
    p = body(); p.update(strength_enabled=True, strength_circuits=3)
    method = next(m for m in engine.resolved_methods(p) if m["structure"] == "STRENGTH_CIRCUIT")
    cap = engine.capacity_for(method, Repository().settings, None, (None, [], []), TODAY)
    fraction = engine._nominal_fraction(method, "MAINTENANCE", p, "GENERAL_PREPARATION", cap, p["planning_controls"])
    assert fraction == .6 and cap["capacity_minutes"]*fraction == pytest.approx(5.4)
    work = engine._quantized_work(method, cap["capacity_minutes"]*fraction)
    blocks = engine._blocks(method, work, cap, Repository().settings)
    duration = engine._strength_duration(blocks)
    assert duration["work_minutes"] == 3. and duration["rest_minutes"] == 4.
    assert duration["main_minutes"] == 7.
    assert sum(b["duration_min"] for b in blocks) == pytest.approx(22.)


def test_race_forecast_below_90_changes_warning_but_not_prescriptions(monkeypatch):
    repo = Repository()
    race_day = TODAY + timedelta(days=5)
    repo.events.append({"event_type": "CONTROL_RACE", "event_id": "control", "name": "Контролен старт",
                       "start_date": race_day.isoformat(), "end_date": race_day.isoformat()})
    repo.events.append({"event_type": "CONTROL_RACE", "event_id": "second", "name": "Следващ старт",
                       "start_date": (race_day+timedelta(days=1)).isoformat(),
                       "end_date": (race_day+timedelta(days=1)).isoformat()})
    original = engine.recovery_v2.simulate
    level = [90.]
    def simulate(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("target") == race_day:
            for row in result["current"]:
                row["readiness_percent"] = level[0]
        return result
    monkeypatch.setattr(engine.recovery_v2, "simulate", simulate)
    p = body(sessions_per_week=7)
    ready = run(monkeypatch, p, repo)
    level[0] = 70.
    low = run(monkeypatch, p, repo)
    assert ready["summary"]["sessions"] > 0
    assert [d for d in ready["days"] if d["date"] < race_day.isoformat()] == [
        d for d in low["days"] if d["date"] < race_day.isoformat()]
    assert [d["sessions"] for d in ready["days"]] == [d["sessions"] for d in low["days"]]
    assert ready["activation_eligible"] == low["activation_eligible"]
    assert not any(w["code"] == "RACE_RECOVERY_WARNING" for w in ready["warnings"])
    warning = next(w for w in low["warnings"] if w["code"] == "RACE_RECOVERY_WARNING")
    assert len([w for w in low["warnings"] if w["code"] == "RACE_RECOVERY_WARNING"]) == 1
    assert warning["blocking"] is False and warning["race_date"] == race_day.isoformat()
    assert "STR" not in warning["components"] and set(warning["components"].values()) == {70.}
    assert not any(r["code"] == "RACE_RECOVERY_CONFLICT" for day in low["days"] for r in day["rejected_alternatives"])


def test_search_fills_support_and_optional_tails_even_when_accents_are_covered():
    baseline = {"days": [{"date": TODAY.isoformat(), "sessions": [{"zone": "Z3", "main_work_minutes": 30}]}],
                "allocation": {"components": {
                    "Z3": {"target":30, "actual":0, "planned":30, "remaining":0},
                    "Z2": {"target":60, "actual":0, "planned":20, "remaining":40}}}}
    calls = []
    def replay(policy):
        calls.append(policy)
        result = deepcopy(baseline)
        if policy.get("optional_easy_fraction") == 0 and policy.get("balance_complete_doses") is False:
            result["allocation"]["components"]["Z2"].update(planned=60, remaining=0)
        return result
    result = improve(baseline, {"planning_controls": {"accents": ["Z3"]}}, replay)
    assert result["allocation"]["components"]["Z2"]["planned"] == 60
    assert result["allocation"]["search"]["conclusion"] == "TARGETS_COVERED"
    assert len(calls) == 1
    assert result["allocation"]["search"]["alternatives"] == 1
