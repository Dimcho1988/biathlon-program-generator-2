"""Behavioural checks for independently dosed pairs and small mixed workloads."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from apps.api import training_plan_engine as engine
from apps.api.management_schemas import ManagementProfile, PlanningControls
from biathlon import adaptive_methods, training_guidance
from biathlon.training_methods import resolved_methods
from tests.api.test_management_schedule import body, run
from tests.api.test_management_v2 import interval
from tests.api.test_training_plan_engine import Repository, TODAY
from tests.api.test_readiness_adaptive_plan_v2 import assert_readiness_dose


@pytest.mark.parametrize("age,experience", [(16, 3), (30, .5), (None, None)])
def test_developmental_profiles_keep_the_anchor_but_shorten_work_and_lengthen_rest(age, experience):
    p = body()
    p.update(age_years=age, training_experience_years=experience, interval_profiles=[interval()])
    ManagementProfile.model_validate(p)
    original = deepcopy(p)
    m = next(m for m in resolved_methods(p) if m["structure"] == "METABOLIC_INTERVALS" and not m.get("mixed_component"))
    assert m["developmental_variant"]
    assert m["interval_profile"]["work_seconds"] <= 30
    assert m["interval_profile"]["recovery_seconds"] >= 3*m["interval_profile"]["work_seconds"]
    assert m["interval_profile"]["continuous_capacity_min"] == p["interval_profiles"][0]["continuous_capacity_min"]
    assert p == original


def test_short_z5_still_requires_a_real_effort_anchor():
    p = body(); p["age_years"] = 16
    m = next(m for m in resolved_methods(p) if m["zone"] == "Z5" and not m.get("mixed_component"))
    assert m["interval_template"]["work_seconds"] == 15
    assert engine.capacity_for(m, Repository().settings, None, (None, [], []), TODAY) is None


@pytest.mark.parametrize("zone", ["Z2", "Z3", "Z4", "Z5"])
def test_mixed_block_sequence_load_and_duration_are_complete(zone):
    p = body()
    p["interval_profiles"] = [interval(zone="Z4"), interval(zone="Z5", continuous_capacity_min=10, work_seconds=30, recovery_seconds=60)]
    m = next(m for m in resolved_methods(p) if m.get("mixed_component") and m["zone"] == zone)
    settings = Repository().settings
    cap = engine.capacity_for(m, settings, None, (None, [], []), TODAY)
    cap.update(secondary_capacity={"capacity_minutes": 100, "target_hr_bpm": 130, "target_speed_kmh": None}, easy_to_primary_ratio=4.)
    blocks = engine._blocks(m, max(m["min_work_min"], 3), cap, settings)
    assert [b["kind"] for b in blocks[:2]] == ["WARMUP", "PREPARATION"]
    assert blocks[0]["duration_min"] == 15
    assert blocks[-2]["zone"] == "Z1" and blocks[-2]["kind"] == "WORK"
    assert blocks[-1]["kind"] == "COOLDOWN"
    direct, _, _ = engine._canonical_load(blocks, settings, [], TODAY)
    assert direct[zone] > 0 and direct["Z1"] > 0
    assert sum(b["duration_s"] for b in blocks) == pytest.approx(60*sum(b["duration_min"] for b in blocks))


def test_double_pair_denominators_are_independent_even_in_the_same_zone():
    p = body()
    m = adaptive_methods.threshold_pairs([("Run", m) for m in resolved_methods(p)], ["Z3"])[0][1]
    first = {"capacity_minutes": 60., "target_hr_bpm": 157., "target_speed_kmh": None,
             "dose_capacity_basis": "INDEPENDENT_CONTINUOUS_TMAX"}
    first["paired_capacity"] = {**first, "zone": "Z3", "capacity_minutes": 40.}
    blocks = engine._blocks(m, 60, first, Repository().settings)
    assert sum(b["duration_min"] for b in blocks if b["kind"] == "WORK" and b["session_index"] == 1) == 30
    assert sum(b["duration_min"] for b in blocks if b["kind"] == "WORK" and b["session_index"] == 2) == 20
    assert engine._dose_usage(blocks, first, "Z3") == pytest.approx(.5)


def test_lactate_method_ceiling_never_overwrites_personal_reference():
    p = body(double_threshold_lactate_ceiling=3.8)
    blocks = [{"kind": "WORK", "zone": "Z3", "instructions": "Control", "lactate_reference": {"source": "GENERAL_OLT_2024"}},
              {"kind": "WORK", "zone": "Z4", "instructions": "Control", "lactate_reference": {"source": "INDIVIDUAL_MANUAL", "high_mmol": 2.8}}]
    training_guidance.annotate_double_threshold(blocks, p)
    assert blocks[0]["lactate_reference"]["high_mmol"] == 3.8
    assert blocks[0]["lactate_reference"]["measured"] is False
    assert blocks[1]["lactate_reference"]["high_mmol"] == 2.8


@pytest.mark.parametrize("ready,allowed", [(85., True), (69., True), (50., True), (0., False)])
def test_mixed_candidate_below_90_uses_residual_budget_and_own_readiness_rule(monkeypatch, ready, allowed):
    p = body(sessions_per_week=7, intensity_days=[(TODAY.weekday()+6)%7], accent_mode="MANUAL", accents=["Z3"], accent_index=1.5)
    methods = [m for m in resolved_methods(p) if m.get("mixed_component") and m["zone"] == "Z3"]
    monkeypatch.setattr(engine, "resolved_methods", lambda _: deepcopy(methods))
    original = engine.recovery_v2.simulate
    def recovery(*args, **kwargs):
        result = original(*args, **kwargs)
        for row in result["current"]:
            row["readiness_percent"] = ready if row["zone"] == "Z3" else 100.
        return result
    monkeypatch.setattr(engine.recovery_v2, "simulate", recovery)
    repo = Repository()
    # Isolate the readiness/day rule from the separate exposure ceiling. The
    # revised Z1 capacity makes the complete mixed structure longer than 60 min.
    for activity in repo.envelope["snapshot_payload"]["load_history"]["activities"]:
        activity["duration_min"] = 120
    plan = run(monkeypatch, p, repo)
    sessions = [s for d in plan["days"] for s in d["sessions"]]
    assert bool(sessions) is allowed
    if allowed:
        s = sessions[0]
        assert s["mixed_component"] and not s["is_key_session"]
        assert s["dose_evidence"]["readiness_policy"]["observed_percent"] == ready
        assert s["dose_evidence"]["readiness_policy"]["dose_factor"] == ready/100
        assert_readiness_dose(s)
        assert s["dose_evidence"]["requested_primary_work_minutes"]/s["dose_evidence"]["capacity_minutes"] == pytest.approx(.15*ready/100, abs=.0001)
        work = sum(b["duration_min"] for b in s["blocks"] if b["kind"] == "WORK" and b["zone"] == "Z3")
        assert work <= s["dose_evidence"]["capacity_minutes"]*.15*s["dose_evidence"]["readiness_policy"]["dose_factor"]+.001
        d = next(d for d in plan["days"] if d["sessions"])
        assert d["date"] == TODAY.isoformat()  # Easy day, outside the reserved key slot.
        assert all(v <= d["load_budget"]["components"][z]["deficit_effective"]+.001 for z, v in s["canonical_effective_load"].items())
    else:
        assert any(r["code"] == "READINESS_DOSE_UNAVAILABLE" for d in plan["days"] for r in d["rejected_alternatives"])


def test_disabled_mixed_policy_has_no_candidates():
    assert not any(m.get("mixed_component") for m in resolved_methods(body(mixed_sessions_enabled=False)))


def test_double_threshold_still_obeys_day_time_and_component_budgets(monkeypatch):
    p = body(sessions_per_week=12, double_threshold_days=[TODAY.weekday()], accent_mode="MANUAL", accents=["Z3"], accent_index=1.5)
    p.update(availability_mode="MANUAL", available_minutes=[40]*7)
    plan = run(monkeypatch, p, Repository())
    assert all(sum(s["total_minutes"] for s in d["sessions"]) <= 40.001 for d in plan["days"])
    assert not any(s["double_threshold"] for d in plan["days"] for s in d["sessions"])
    p.update(availability_mode="AUTO_HISTORY", component_targets_weekly={"Z3": 0})
    plan = run(monkeypatch, p, Repository())
    assert not any(s["double_threshold"] or s.get("mixed_component") and s["zone"] == "Z3" for d in plan["days"] for s in d["sessions"])


def test_long_threshold_work_is_monotone_at_repetition_boundaries():
    p = body()
    m = adaptive_methods.threshold_pairs([("Run", m) for m in resolved_methods(p)], ["Z3"])[0][1]
    m = {**m, "double_threshold": False}
    cap = {"capacity_minutes": 80, "target_hr_bpm": 157, "target_speed_kmh": None}
    values = []
    for work in (19.5, 20., 20.5, 21.):
        blocks = engine._blocks(m, work, cap, Repository().settings)
        values.append(sum(b["duration_min"] for b in blocks if b["kind"] == "WORK"))
        assert all(6 <= b["duration_min"] <= 10 for b in blocks if b["kind"] == "WORK")
    assert values == sorted(values)


@pytest.mark.parametrize("changes", [{"mixed_min_readiness": 59}, {"double_threshold_fraction": .9}, {"double_threshold_gap_hours": 0}])
def test_policy_values_validate(changes):
    with pytest.raises(ValidationError):
        PlanningControls(**changes)
