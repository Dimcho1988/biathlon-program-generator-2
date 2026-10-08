"""Readiness changes dose; the calendar and canonical budgets remain real gates."""
from copy import deepcopy
from datetime import timedelta

import pytest

from apps.api import training_plan_engine as engine
from biathlon import adaptive_methods
from biathlon.constants import COMPONENTS
from tests.api.test_training_plan_engine import (
    NOW, TODAY, Repository, profile, reference_speed,
)


def fixed_readiness(monkeypatch, value, *, overrides=None):
    """Keep the genuine recovery envelope; isolate its day-start dose input."""
    original = engine.recovery_v2.simulate

    def simulated(*args, **kwargs):
        result = original(*args, **kwargs)
        for row in result["current"]:
            row["readiness_percent"] = (overrides or {}).get(row["zone"], value)
        return result

    monkeypatch.setattr(engine.recovery_v2, "simulate", simulated)
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)


def threshold_only(monkeypatch, body):
    """Use an actual executable method, retaining capacity and Q/E accounting."""
    method = next(m for m in engine.resolved_methods(body) if m["id"] == "END-THR-LONG-01")
    monkeypatch.setattr(engine, "resolved_methods", lambda _: [deepcopy(method)])


def threshold_profile(**changes):
    return profile(reentry_days=0, available_minutes=[180] * 7,
                   age_years=25, training_experience_years=5, **changes)


def generate(body, repo=None, *, start=None):
    repo = repo or Repository()
    repo.accents.update(accent_mode="MANUAL", manual_components=["Z3"])
    return engine.generate_plan(repo, "athlete", body,
                                start_date=start or TODAY + timedelta(days=1), now=NOW)


def assert_rolling_budgets(plan):
    for day in plan["days"]:
        for z in COMPONENTS:
            load = sum(s["canonical_effective_load"][z] for s in day["sessions"])
            assert load <= day["load_budget"]["components"][z]["deficit_effective"] + .005


def assert_readiness_dose(session):
    """Reusable behavioral assertion for the reported linear dose evidence."""
    evidence = session["dose_evidence"]
    required = evidence["readiness_policy"]["required_components"]
    assert required and all(value is not None for value in required.values())
    assert evidence["readiness_dose_factor"] == pytest.approx(min(required.values()) / 100)
    # Split sessions derive the displayed fraction from a milliminute-rounded
    # requested budget; the tolerance follows that reporting precision.
    assert evidence["fraction"] == pytest.approx(
        evidence["base_fraction"] * evidence["readiness_dose_factor"],
        abs=.0005 / evidence["capacity_minutes"],
    )
    assert evidence["applied_structure_fraction"] <= evidence["max_dose_fraction"] + .001
    if evidence.get("min_dose_fraction") is not None:
        assert evidence.get("applied_minimum_capacity_fraction", evidence["applied_structure_fraction"]) >= evidence["min_dose_fraction"] - .001


@pytest.mark.parametrize("readiness", [0., 30., 50., 70., 89.9, 90., 100.])
def test_readiness_multiplier_is_linear_without_a_90_percent_discontinuity(readiness):
    method = {"zone": "Z3", "structure": "CONTINUOUS"}
    policy = adaptive_methods.readiness_policy(method, threshold_profile(), {"Z3": readiness})
    assert policy["dose_factor"] == pytest.approx(readiness / 100)
    assert policy["minimum_percent"] == 0


def test_combination_uses_the_least_ready_required_component_once():
    method = {"zone": "Z3", "structure": "THRESHOLD_LONG", "double_threshold": True,
              "paired_method": {"zone": "Z4", "structure": "THRESHOLD_SHORT"}}
    policy = adaptive_methods.readiness_policy(method, threshold_profile(),
                                               {"Z1": 80., "Z3": 70., "Z4": 50.})
    assert policy["dose_factor"] == .5
    # The readiness of the three required components is not multiplied together.
    assert policy["dose_factor"] != pytest.approx(.8 * .7 * .5)


@pytest.mark.parametrize("readiness", [{"Z3": None}, {"Z3": 100., "Z1": None}])
def test_unknown_required_readiness_never_becomes_full_capacity(readiness):
    policy = adaptive_methods.readiness_policy({"zone": "Z3"}, threshold_profile(), readiness)
    assert policy["dose_factor"] == 0


@pytest.mark.parametrize("base_fraction, readiness, expected", [(.6, 50., .3), (.7, 50., .35), (.7, 35., .245)])
def test_readiness_prescribes_selected_fraction_times_ready_capacity(monkeypatch, base_fraction, readiness, expected):
    fixed_readiness(monkeypatch, readiness)
    body = threshold_profile(building_fraction=base_fraction)
    threshold_only(monkeypatch, body)
    plan = generate(body)
    work = [s for d in plan["days"] for s in d["sessions"] if s["zone"] == "Z3"]
    assert work, "A reduced complete key session should survive when its whole structure fits."
    dose = work[0]["dose_evidence"]
    assert dose["base_fraction"] == base_fraction
    assert dose["fraction"] == pytest.approx(expected)
    assert dose["readiness_dose_factor"] == readiness / 100
    assert dose["requested_primary_work_minutes"] / dose["capacity_minutes"] == pytest.approx(expected, abs=.0001)
    assert dose["applied_structure_fraction"] <= expected + .001
    assert_rolling_budgets(plan)


def test_lower_readiness_preserves_supported_effort_and_complete_blocks(monkeypatch):
    body = threshold_profile(building_fraction=.6)
    threshold_only(monkeypatch, body)
    original = engine.recovery_v2.simulate
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)

    def plan_at(readiness):
        def simulated(*args, **kwargs):
            result = original(*args, **kwargs)
            for row in result["current"]:
                row["readiness_percent"] = readiness
            return result
        monkeypatch.setattr(engine.recovery_v2, "simulate", simulated)
        plan = generate(body)
        return next(s for d in plan["days"] for s in d["sessions"])

    full, reduced = plan_at(100.), plan_at(50.)
    assert reduced["main_work_minutes"] < full["main_work_minutes"]
    assert reduced["dose_evidence"]["capacity_source"] == full["dose_evidence"]["capacity_source"]
    assert reduced["dose_evidence"]["capacity_minutes"] == full["dose_evidence"]["capacity_minutes"]
    full_work = [b for b in full["blocks"] if b["kind"] == "WORK"]
    reduced_work = [b for b in reduced["blocks"] if b["kind"] == "WORK"]
    assert {b["target_hr_bpm"] for b in reduced_work} == {b["target_hr_bpm"] for b in full_work}
    assert {b["target_speed_kmh"] for b in reduced_work} == {b["target_speed_kmh"] for b in full_work}
    for session in (full, reduced):
        assert any(b["kind"] == "WARMUP" for b in session["blocks"])
        assert any(b["kind"] == "COOLDOWN" for b in session["blocks"])
        assert session["main_work_minutes"] == pytest.approx(
            sum(b["duration_min"] for b in session["blocks"] if b["kind"] == "WORK"), abs=.002)


def test_missed_preferred_key_day_moves_to_available_day_without_overriding_rest(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = threshold_profile(building_fraction=.6)
    threshold_only(monkeypatch, body)
    repo = Repository()
    repo.preferences.update(intensity_days=[1, 4], rest_days=[3])
    tuesday = TODAY + timedelta(days=1)
    repo.events.append({"event_id": "unavailable-tuesday", "event_type": "UNAVAILABLE",
                        "start_date": tuesday.isoformat(), "end_date": tuesday.isoformat()})
    plan = generate(body, repo)
    quality = [d for d in plan["days"] if any(s["zone"] == "Z3" for s in d["sessions"])]
    assert quality
    assert quality[0]["date"] == (TODAY + timedelta(days=2)).isoformat()
    assert any(c["to"] for c in plan["parameters"]["key_schedule_changes"])
    for day in plan["days"]:
        if day["date"] == tuesday.isoformat() or engine.date.fromisoformat(day["date"]).weekday() == 3:
            assert not day["sessions"]
    assert_rolling_budgets(plan)


def test_adaptive_dose_does_not_invent_a_missing_capacity(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = threshold_profile(allow_expert_fallback=False)
    threshold_only(monkeypatch, body)
    plan = generate(body)
    assert not any(d["sessions"] for d in plan["days"])
    assert any(r["code"] == "CAPACITY_UNAVAILABLE" for d in plan["days"] for r in d["rejected_alternatives"])


def test_z1_is_reserved_for_later_key_structures_without_bypassing_rolling_limit(monkeypatch):
    fixed_readiness(monkeypatch, 100.)
    body = threshold_profile(building_fraction=.6,
                             component_targets_weekly={"Z1": 140., "Z3": 120.})
    methods = [m for m in engine.resolved_methods(body)
               if m["id"] in {"END-LONG-Z1-01-MAINTAIN", "END-THR-LONG-01"}]
    monkeypatch.setattr(engine, "resolved_methods", lambda _: deepcopy(methods))
    repo = Repository()
    repo.preferences.update(sessions_per_week=7, intensity_days=[1, 4])
    plan = generate(body, repo, start=TODAY)
    keys = [d for d in plan["days"] if any(s["zone"] == "Z3" for s in d["sessions"])]
    assert len(keys) == 2, "Early easy work must leave a complete dose for the second key opportunity."
    assert [d["date"] for d in keys] == [(TODAY + timedelta(days=n)).isoformat() for n in (1, 4)]
    assert plan["days"][0]["session"]["zone"] == "Z1"
    assert all(any(b["kind"] == "WARMUP" for b in s["blocks"])
               for d in keys for s in d["sessions"])
    assert sum(s["canonical_effective_load"]["Z1"] for d in plan["days"] for s in d["sessions"]) <= 140.005
    assert_rolling_budgets(plan)


def test_reduced_cycle_can_keep_a_controlled_nonaccent_component_within_its_q_and_e(monkeypatch):
    from tests.api.test_load_progression import configured, observed

    fixed_readiness(monkeypatch, 100.)
    body = configured()
    body["planning_controls"].update(
        mesocycle_anchor=(TODAY - timedelta(days=21)).isoformat(),
        accents=["Z1"], mixed_sessions_enabled=False,
    )
    threshold_only(monkeypatch, body)
    repo, _, _ = observed()
    plan = generate(body, repo, start=TODAY)
    assert all(d["cycle"]["kind"] == "RECOVERY" for d in plan["days"])
    work = [s for d in plan["days"] for s in d["sessions"] if s["zone"] == "Z3"]
    assert work, "A recovery cycle reduces component targets without banning every nonaccent quality."
    assert all(s["purpose"] == "MAINTENANCE" for s in work)
    assert all(s["dose_evidence"]["fraction"] <= body["maintenance_fraction"] for s in work)
    component = plan["allocation"]["components"]["Z3"]
    assert component["planned_q"] <= component["target_q"] + .005
    assert component["planned_effective"] <= component["target_effective"] + .005
    assert_rolling_budgets(plan)


def test_double_threshold_split_reports_each_session_base_fraction_and_one_readiness_scale(monkeypatch):
    from tests.api.test_management_schedule import body

    fixed_readiness(monkeypatch, 80.)
    configured = body(sessions_per_week=12, double_threshold_days=[TODAY.weekday()],
                      threshold_method="INTERVALS", accent_mode="MANUAL", accents=["Z3"],
                      accent_index=1.5)
    plan = generate(configured, start=TODAY)
    pair = plan["days"][0]["sessions"]
    assert len(pair) == 2 and all(s["double_threshold"] for s in pair)
    for session in pair:
        evidence = session["dose_evidence"]
        assert evidence["base_fraction"] == configured["planning_controls"]["double_threshold_fraction"]
        assert evidence["readiness_dose_factor"] == .8
        assert evidence["fraction"] == pytest.approx(evidence["base_fraction"] * .8)
        assert evidence["requested_work_minutes"] / evidence["capacity_minutes"] == pytest.approx(.4, abs=.0001)
        assert evidence["applied_structure_fraction"] <= .4 + .001
        assert any(b["kind"] == "WARMUP" for b in session["blocks"])
        assert any(b["kind"] == "COOLDOWN" for b in session["blocks"])
    assert_rolling_budgets(plan)


def test_z4_maintenance_can_fit_whole_profile_at_less_than_full_readiness(monkeypatch):
    from tests.api.test_management_v2 import interval

    fixed_readiness(monkeypatch, 80.)
    body = threshold_profile(interval_profiles=[interval()])
    method = next(m for m in engine.resolved_methods(body) if m["id"] == "END-VO2-TREF-01-Z4")
    monkeypatch.setattr(engine, "resolved_methods", lambda _: [deepcopy(method)])
    plan = generate(body)
    work = [s for d in plan["days"] for s in d["sessions"] if s["zone"] == "Z4"]
    assert work, "Reduced readiness should retain a whole legal maintenance variant when it fits."
    session = work[0]
    assert session["purpose"] == "MAINTENANCE"
    evidence = session["dose_evidence"]
    assert evidence["maintenance_policy"]
    assert evidence["base_fraction"] == 1.
    assert evidence["fraction"] == .8
    assert evidence["readiness_dose_factor"] == .8
    assert evidence["requested_primary_work_minutes"] == pytest.approx(9.6)
    assert session["main_work_minutes"] == 9.
    assert [b["duration_min"] for b in session["blocks"] if b["kind"] == "WORK"] == [3., 3., 3.]
    assert [b["duration_min"] for b in session["blocks"] if b["kind"] == "RECOVERY"] == [3., 3.]
    assert evidence["applied_structure_fraction"] == .6
    assert evidence["applied_structure_fraction"] <= evidence["max_dose_fraction"] + .001
    assert_rolling_budgets(plan)

