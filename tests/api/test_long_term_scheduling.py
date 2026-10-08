from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import training_plan_engine as engine
from biathlon.constants import COMPONENTS
from tests.api.test_load_progression import configured, observed
from tests.api.test_management_v2 import interval
from tests.api.test_readiness_adaptive_plan_v2 import fixed_readiness, assert_readiness_dose
from tests.api.test_training_plan_engine import TODAY, NOW


def prescribed_q(monkeypatch, targets):
    original = engine._goals
    def goals(body, day, *args, **kwargs):
        values, accents, cycle = original(body, day, *args, **kwargs)
        for zone, target in targets(day).items():
            values[zone]["target_weekly_q"] = target
            values[zone]["desired_weekly_q"] = target
        return values, accents, cycle
    monkeypatch.setattr(engine, "_goals", goals)


def actual_today(repo, zones):
    source = repo.envelope["snapshot_payload"]["load_history"]
    # A genuine canonical actual vector: upper HR for Z1–Z4 and lower HR
    # for Z5 each yield one equivalent minute per raw minute. Include the
    # technical cascade from the causal history, as the real import does.
    blocks = [engine._block("WORK", "Actual work", zone, 2.,
                            repo.settings.zone_bounds_bpm[4 if zone == "Z5" else int(zone[1:])], "")
              for zone in zones]
    direct, effective, _ = engine._canonical_load(blocks, repo.settings, engine._daily_rows(source, TODAY), TODAY)
    activity = {"activity_ref":"actual-today", "date":TODAY.isoformat(), "sport":"Run", "duration_min":2.*len(zones),
                "zones":[{"zone":zone,"raw_time_min":2. if zone in zones else 0.,"equivalent_time_min":direct[zone]} for zone in COMPONENTS if zone != "STR"]}
    source["activities"].append(activity)
    repo.envelope["activities"].append({**deepcopy(activity),"local_date":TODAY.isoformat()})
    for row in source["daily"]:
        if row["date"] == TODAY.isoformat():
            row["effective_load"] = effective[row["zone"]]


def assert_segment_contract(plan, *, constant_q=None):
    for segment in plan["allocation"]["segments"]:
        matching = [week for week in plan["long_term"]["weeks"] if week["start_date"] <= segment["window_start"] and week["end_date"] >= segment["window_end"]]
        assert len(matching) == 1
        for zone, row in segment["components"].items():
            outlook = matching[0]["components"][zone]
            target = outlook["target_period_q"] if row["basis"] == "DIRECT_Q" else outlook["target_period_effective"]
            if matching[0]["start_date"] == segment["window_start"] and matching[0]["end_date"] == segment["window_end"]:
                assert row["target"] == pytest.approx(target, abs=.001)
            elif zone in (constant_q or {}):
                assert row["target"] == pytest.approx(constant_q[zone]*segment["days"]/7,abs=.001)
            assert row["actual"]+row["planned"] <= row["target"]+.005
            assert row["actual_effective"]+row["planned_effective"] <= row["target_effective"]+.005
    for day in plan["days"]:
        for session in day["sessions"]:
            assert_readiness_dose(session)


def test_positive_long_term_z2_to_z5_targets_receive_direct_work_with_actual_and_planned_bounded(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=30, training_experience_years=10, available_minutes=[360]*7, building_fraction=.65,
                      interval_profiles=[interval("Z4",continuous_capacity_min=30.,work_seconds=30,recovery_seconds=60,min_repetitions=4,max_repetitions=20),
                                         interval("Z5",continuous_capacity_min=40.,work_seconds=30,recovery_seconds=60,min_repetitions=4,max_repetitions=12)])
    body["planning_controls"].update(sessions_per_week=13,sessions_by_day=[2,2,2,2,2,2,1],threshold_days=[1,4],mixed_sessions_enabled=True)
    targets = {"Z1":400.,"Z2":40.,"Z3":25.,"Z4":10.,"Z5":6.}
    prescribed_q(monkeypatch, lambda _: targets)
    repo, _, _ = observed()
    actual_today(repo, ["Z1","Z2","Z3","Z4","Z5"])
    plan = engine.generate_plan(repo,"athlete",body,start_date=TODAY,now=NOW)
    for zone in ("Z2","Z3","Z4","Z5"):
        row = plan["allocation"]["components"][zone]
        assert row["actual_q"] == 2.
        assert row["planned_q"] > 0, f"Supported {zone} intent must not disappear behind the two primary-key slots."
    assert plan["summary"]["key_sessions"] <= body["max_key_sessions_per_week"]
    assert_segment_contract(plan)


def test_supported_z5_target_includes_the_complete_minimums_canonical_direct_q():
    body = configured(age_years=30,training_experience_years=10,interval_profiles=[interval("Z5",continuous_capacity_min=40.,work_seconds=30,recovery_seconds=60,min_repetitions=4,max_repetitions=12)])
    method = next(method for method in engine.resolved_methods(body) if method["id"] == "END-VO2-TREF-01-Z5-MIX-REPETITIONS-SHORT")
    repo, _, rows = observed()
    evidence = engine.capacity_for(method, repo.settings, None, (None, [], []), TODAY)
    evidence.update(readiness_dose_factor=1., secondary_capacity={"capacity_minutes":120.,"target_hr_bpm":120.,"target_speed_kmh":None},easy_to_primary_ratio=2.)
    minimum = engine._minimum_work(method,evidence,repo.settings)
    blocks = engine._blocks(method,minimum,evidence,repo.settings)
    direct, _, _ = engine._canonical_load(blocks,repo.settings,rows,TODAY)
    assert minimum == 2.
    assert [block["duration_min"] for block in blocks if block["kind"] == "WORK" and block["zone"] == "Z5"] == [.25]*8
    # Existing Z5 equivalence is 1+.05*(190-175), so 2 work min are 3.5 Q.
    # A target of 5 with 2 actual Q cannot fit it; the executable fixture
    # above deliberately gives 6−2=4 Q rather than bypassing this gate.
    assert direct["Z5"] == 3.5
    assert 5.-2. < direct["Z5"] <= 6.-2.


def test_short_current_microcycle_cannot_borrow_the_next_microcycle_q_budget(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = configured(age_years=30,training_experience_years=10,available_minutes=[180]*7)
    body["planning_controls"].update(mesocycle_anchor=(TODAY-timedelta(days=5)).isoformat(),sessions_per_week=2,sessions_by_day=[0,1,0,0,1,0,0],threshold_days=[1,4],threshold_method="INTERVALS")
    method = next(method for method in engine.resolved_methods(body) if method["id"] == "END-THR-TIME-01")
    monkeypatch.setattr(engine,"resolved_methods",lambda _: [deepcopy(method)])
    prescribed_q(monkeypatch, lambda _: {"Z3":80.})
    repo, _, _ = observed()
    actual_today(repo, ["Z3"])
    plan = engine.generate_plan(repo,"athlete",body,start_date=TODAY,now=NOW)
    first_planned_q = sum(session["direct_equivalent_minutes"]["Z3"] for day in plan["days"][:2] for session in day["sessions"])
    assert 2.+first_planned_q <= 80.*2/7+.005
    first = plan["allocation"]["segments"][0]
    assert first["days"] == 2
    assert first["components"]["Z3"]["target"] == pytest.approx(80.*2/7,abs=.001)
    assert first["components"]["Z3"]["actual"] == 2.
    assert first["components"]["Z3"]["planned"] > 0
    assert_segment_contract(plan,constant_q={"Z3":80.})


def test_locked_old_dose_requires_review_when_long_term_direct_q_allowance_shrinks(monkeypatch):
    fixed_readiness(monkeypatch,100.)
    body = configured(age_years=30,training_experience_years=10,available_minutes=[180]*7)
    body["planning_controls"].update(sessions_per_week=2,sessions_by_day=[1,0,0,0,1,0,0],threshold_days=[0,4],threshold_method="INTERVALS")
    method = next(method for method in engine.resolved_methods(body) if method["id"] == "END-THR-TIME-01")
    monkeypatch.setattr(engine,"resolved_methods",lambda _: [deepcopy(method)])
    prescribed_q(monkeypatch,lambda _: {"Z3":80.})
    repo, _, _ = observed()
    first = engine.generate_plan(repo,"athlete",body,start_date=TODAY,now=NOW)
    locked = deepcopy(first["days"][0])
    assert locked["sessions"] and locked["sessions"][0]["direct_equivalent_minutes"]["Z3"] > 2.
    prescribed_q(monkeypatch,lambda _: {"Z3":2.})
    plan = engine.generate_plan(repo,"athlete",body,start_date=TODAY,now=NOW,locked_day=locked)
    assert plan["days"][0]["status"] == "REVIEW_REQUIRED"
    assert not plan["days"][0]["sessions"]
    assert plan["activation_eligible"] is False
