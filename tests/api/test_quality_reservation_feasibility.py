"""Protection is permission only for complete doses that can really occur."""
from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta

from apps.api import training_plan_engine as engine
from tests.api.test_load_progression import configured, observed
from tests.api.test_long_term_scheduling import assert_segment_contract
from tests.api.test_management_v2 import interval
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness
from tests.api.test_training_plan_engine import TODAY, NOW, reference_speed


def prescribed_intent(monkeypatch):
    original = engine._goals
    def goals(*args, **kwargs):
        values, accents, cycle = original(*args, **kwargs)
        for zone, target in {"Z1":500., "Z2":80., "Z3":80., "Z4":20.}.items():
            values[zone]["target_weekly_q"] = target
            values[zone]["desired_weekly_q"] = target
        return values, accents, cycle
    monkeypatch.setattr(engine, "_goals", goals)


def constrained_profile(minutes, slots):
    body = configured(age_years=30, training_experience_years=10, building_fraction=.65,
        available_minutes=minutes, interval_profiles=[interval("Z4", continuous_capacity_min=20.)])
    body["planning_controls"].update(sessions_by_day=slots, sessions_per_week=sum(slots),
        threshold_days=[1], threshold_method="INTERVALS", mixed_sessions_enabled=True, history_gap_days=10)
    return body


def selected_methods(monkeypatch, body, identifiers):
    methods = [method for method in engine.resolved_methods(body) if method["id"] in identifiers]
    assert len(methods) == len(identifiers)
    monkeypatch.setattr(engine, "resolved_methods", lambda _: deepcopy(methods))
    return {method["id"]:method for method in methods}


def core_minutes(repo, body, method, factor=1.):
    method = deepcopy(method)
    speed = reference_speed(repo, "athlete", "Run")
    context = engine._capacity_context(speed, repo.settings)
    capacity = engine.capacity_for(method, repo.settings, speed, context, TODAY)
    assert capacity is not None
    capacity["readiness_dose_factor"] = factor
    if method.get("mixed_component"):
        secondary = engine.capacity_for({**method,"zone":"Z1","position":.35,"structure":"CONTINUOUS"},
            repo.settings, speed, context, TODAY)
        assert secondary is not None
        capacity.update(secondary_capacity=secondary, easy_work_cap_minutes=0.,
            easy_to_primary_ratio=max(2., secondary["capacity_minutes"]*.25/max(1., capacity["capacity_minutes"]*.15)))
    minimum = engine._minimum_work(method, capacity, repo.settings)
    assert minimum is not None
    blocks = engine._blocks(method, minimum, capacity, repo.settings)
    assert blocks
    return sum(block["duration_min"] for block in blocks)


def protection_limits(session):
    return [limit for limit in session["dose_evidence"]["limits"] if limit["code"] == "LONG_TERM_QUALITY_PREPARATION"]


def test_same_day_protection_fits_full_selected_session_and_preserves_both_qualities(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    prescribed_intent(monkeypatch)
    body = constrained_profile([0,80,0,0,0,0,0], [0,2,0,0,0,0,0])
    methods = selected_methods(monkeypatch, body, {"END-THR-TIME-01-FLEX", "END-CROSS-TRAIN-01-Z2-MIX-STEADY"})
    repo, _, _ = observed()
    key_minimum = core_minutes(repo, body, methods["END-THR-TIME-01-FLEX"])
    mixed_minimum = core_minutes(repo, body, methods["END-CROSS-TRAIN-01-Z2-MIX-STEADY"])
    assert key_minimum+mixed_minimum <= 80.
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY+timedelta(days=1), now=NOW)
    tuesday = plan["days"][0]
    protection_count = 0
    elapsed = 0.
    for session in tuesday["sessions"]:
        elapsed += session["total_minutes"]
        for limit in protection_limits(session):
            future_minutes = defaultdict(float)
            for protected in limit["protected_methods"]:
                minimum = core_minutes(repo, body, methods[protected["method_id"]])
                reported = protected.get("minutes", minimum)
                assert abs(reported-minimum) <= .001
                future_minutes[protected["date"]] += reported
            for reserved_day, minutes in future_minutes.items():
                allowance = body["available_minutes"][date.fromisoformat(reserved_day).weekday()]
                assert minutes+(elapsed if reserved_day == tuesday["date"] else 0.) <= allowance+.005
            for protected in limit["protected_methods"]:
                assert "minutes" in protected
                assert protected["readiness_dose_factor"] == 1.
            protection_count += 1
    assert protection_count > 0
    assert len(tuesday["sessions"]) == 2
    assert {session["zone"] for session in tuesday["sessions"]} == {"Z2","Z3"}
    assert elapsed <= 80.+.005
    assert_segment_contract(plan)


def test_future_key_minimum_time_cannot_be_reserved_again_for_a_supplement(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    prescribed_intent(monkeypatch)
    body = constrained_profile([80,40,0,0,0,0,0], [1,2,0,0,0,0,0])
    methods = selected_methods(monkeypatch, body, {"END-THR-TIME-01-FLEX",
        "END-CROSS-TRAIN-01-Z2-MIX-STEADY", "END-VO2-TREF-01-Z4-MIX-REPETITIONS-SHORT"})
    repo, _, _ = observed()
    key_minimum = core_minutes(repo, body, methods["END-THR-TIME-01-FLEX"])
    assert key_minimum <= 40.
    for identifier, method in methods.items():
        if method.get("mixed_component"):
            assert key_minimum+core_minutes(repo, body, method) > 40.
    plan = engine.generate_plan(repo, "athlete", body, start_date=TODAY, now=NOW)
    tuesday = (TODAY+timedelta(days=1)).isoformat()
    assert plan["days"][0]["sessions"]
    assert any(session["is_key_session"] for day in plan["days"] if day["date"] == tuesday for session in day["sessions"])
    for session in plan["days"][0]["sessions"]:
        for limit in protection_limits(session):
            assert all(protected["date"] != tuesday for protected in limit["protected_methods"]), "The key minimum consumes the only usable time on Tuesday."
    assert_segment_contract(plan)


def test_future_mixed_core_is_not_protected_when_key_load_reduces_readiness_below_its_nominal(monkeypatch):
    original = engine.recovery_v2.simulate
    tuesday, wednesday = TODAY+timedelta(days=1), TODAY+timedelta(days=2)
    def simulated(rows, *args, **kwargs):
        result = original(rows, *args, **kwargs)
        key_load = {row["zone"]:row["effective_load"] for row in rows if row["date"] == tuesday.isoformat()}
        after_key = key_load.get("Z3", 0.) > key_load.get("Z4", 0.)
        for row in result["current"]:
            row["readiness_percent"] = 20. if kwargs.get("target") == wednesday and after_key and row["zone"] == "Z4" else 100.
        return result
    monkeypatch.setattr(engine.recovery_v2, "simulate", simulated)
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    prescribed_intent(monkeypatch)
    body = constrained_profile([0,80,80,0,0,0,0], [0,1,1,0,0,0,0])
    methods = selected_methods(monkeypatch, body, {"END-THR-TIME-01-FLEX", "END-VO2-TREF-01-Z4-MIX-REPETITIONS-SHORT"})
    repo, _, _ = observed()
    mixed = methods["END-VO2-TREF-01-Z4-MIX-REPETITIONS-SHORT"]
    assert mixed["min_work_min"] == 1.
    assert mixed["min_work_min"] > 20.*.15*.2  # Absolute repeats cannot fit the future 20% nominal.
    plan = engine.generate_plan(repo, "athlete", body, start_date=tuesday, now=NOW)
    key_day = plan["days"][0]
    assert key_day["sessions"] and key_day["sessions"][0]["is_key_session"]
    for session in key_day["sessions"]:
        for limit in protection_limits(session):
            assert not any(protected["component"] == "Z4" and protected["date"] == wednesday.isoformat()
                           for protected in limit["protected_methods"])
    next_day = next(day for day in plan["days"] if day["date"] == wednesday.isoformat())
    assert next_day["readiness_before"]["Z4"] == 20.
    assert not next_day["sessions"]
    assert any(rejection["code"] == "INSUFFICIENT_DOSE_BUDGET" for rejection in next_day["rejected_alternatives"])
    assert_segment_contract(plan)


def test_future_mixed_core_rebuilds_its_complete_minimum_when_recovery_improves(monkeypatch):
    original = engine.recovery_v2.simulate
    tuesday, wednesday = TODAY+timedelta(days=1), TODAY+timedelta(days=2)
    def simulated(rows, *args, **kwargs):
        result = original(rows, *args, **kwargs)
        key_load = {row["zone"]:row["effective_load"] for row in rows if row["date"] == tuesday.isoformat()}
        after_key = key_load.get("Z3", 0.) > key_load.get("Z4", 0.)
        for row in result["current"]:
            row["readiness_percent"] = 100. if kwargs.get("target") == wednesday and after_key else 80.
        return result
    monkeypatch.setattr(engine.recovery_v2, "simulate", simulated)
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    prescribed_intent(monkeypatch)
    body = constrained_profile([0,80,80,0,0,0,0], [0,1,1,0,0,0,0])
    body["interval_profiles"] = [interval("Z4", continuous_capacity_min=50.)]
    methods = selected_methods(monkeypatch, body, {"END-THR-TIME-01-FLEX", "END-VO2-TREF-01-Z4-MIX-REPETITIONS-SHORT"})
    repo, _, _ = observed()
    mixed = methods["END-VO2-TREF-01-Z4-MIX-REPETITIONS-SHORT"]
    current_minimum = core_minutes(repo, body, mixed, factor=.8)
    future_minimum = core_minutes(repo, body, mixed, factor=1.)
    assert current_minimum < future_minimum <= 80.
    plan = engine.generate_plan(repo, "athlete", body, start_date=tuesday, now=NOW)
    key = next(session for session in plan["days"][0]["sessions"] if session["is_key_session"])
    assert key["dose_evidence"]["readiness_dose_factor"] == .8
    protected = [item for limit in protection_limits(key) for item in limit["protected_methods"]
                 if item["component"] == "Z4" and item["date"] == wednesday.isoformat()]
    assert protected, "A legal future core must remain reservable when Recovery improves."
    for item in protected:
        assert item.get("readiness_dose_factor", .8) == 1.
        assert abs(item.get("minutes", current_minimum)-future_minimum) <= .001
    next_day = next(day for day in plan["days"] if day["date"] == wednesday.isoformat())
    assert next_day["readiness_before"]["Z4"] == 100.
    actual = next(session for session in next_day["sessions"] if session["zone"] == "Z4")
    assert actual["dose_evidence"]["readiness_dose_factor"] == 1.
    primary = sum(block["duration_min"] for block in actual["blocks"]
                  if block["zone"] == "Z4" and block["kind"] == "WORK")
    assert primary >= 50.*.05-.001
    assert_segment_contract(plan)
