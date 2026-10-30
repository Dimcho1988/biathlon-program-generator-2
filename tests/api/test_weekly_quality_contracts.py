"""Whole-week reservations protect executable methods and their preparation."""
from copy import deepcopy
from datetime import timedelta

from apps.api import training_plan_engine as engine
from biathlon import method_library, combined_methods
from tests.api.test_management_schedule import body, high_capacity_history
from tests.api.test_methodology_completion import parents
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness, assert_rolling_budgets
from tests.api.test_training_plan_engine import TODAY, NOW


def test_future_combination_keeps_its_preparation_before_an_easy_day(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    profile = body(sessions_per_week=2, sessions_by_day=[1,1,0,0,0,0,0], threshold_days=[1],
                   accent_mode="MANUAL", accents=["Z3","Z2"], mixed_sessions_enabled=True)
    profile.update(age_years=30, training_experience_years=5, available_minutes=[360]*7,
                   component_targets_weekly={"Z1":400.,"Z2":200.,"Z3":100.,"Z4":0.,"Z5":0.,"STR":0.})
    methods = [dict(m, source_id=m["id"], source_version="1") for m in parents()]
    combo = combined_methods.candidates([("Run", m) for m in methods],
        {"shortage":True, "sessions_by_component":{"Z2":1,"Z3":1}})[0][1]
    profile["_allocation_policy"] = {"reservation_methods":{(TODAY+timedelta(days=1)).isoformat():[
        {"method_id":combo["id"],"sport":"Run","zone":"Z3","purpose":"BUILDING","is_key_session":True,"slot":1}]}}
    easy = next(m for m in engine.resolved_methods(profile) if m["id"] == "RUN-REC-EASY-01")
    monkeypatch.setattr(engine, "resolved_methods", lambda _: deepcopy([easy, *methods]))
    monkeypatch.setattr(method_library, "executable_methods", lambda *a, **kw: [])
    plan = engine.generate_plan(high_capacity_history(), "athlete", profile, start_date=TODAY, now=NOW)
    monday, tuesday = plan["days"][:2]
    assert monday["sessions"]
    assert tuesday["date"] == (TODAY+timedelta(days=1)).isoformat()
    assert tuesday["sessions"][0]["method_id"].startswith("COMBO-")
    reserved = monday["quality_reservations"]
    assert any(r["method_id"] == tuesday["sessions"][0]["method_id"] for r in reserved), (reserved, tuesday["sessions"][0]["method_id"])
    assert_rolling_budgets(plan)
