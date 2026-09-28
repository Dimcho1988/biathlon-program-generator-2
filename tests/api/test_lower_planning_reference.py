"""A low observed Q guides execution without trapping the planning target."""
from copy import deepcopy
from datetime import timedelta

import pytest

from biathlon import load_progression as policy
from apps.api import training_plan_engine as engine
from tests.api.test_load_progression import configured, observed
from tests.api.test_training_plan_engine import TODAY, NOW, reference_speed


def low_history():
    profile = configured()
    profile["planning_controls"]["accents"] = ["Z5"]
    repo, source, rows = observed()
    before = policy.context(profile, source, rows, TODAY)
    measured = {z: 110/60 if z == "Z5" else low/2
                for z, (low, _) in policy.WEEKLY_Q_BOUNDS.items()}
    for activity in source["activities"]:
        for zone in activity["zones"]:
            z = zone["zone"]
            zone["equivalent_time_min"] *= measured[z]/before["components"][z]["weekly_q"]
    phases = {"phases": [{"kind": "GENERAL_PREPARATION", "start_date": TODAY.isoformat(),
                          "end_date": profile["program_end"]}], "taper_windows": []}
    context = policy.context(profile, source, rows, TODAY, periodization=phases)
    return profile, repo, source, rows, phases, context, measured


def goals(profile, rows, context, day=TODAY, period="GENERAL_PREPARATION", limited=False):
    return engine._goals(profile, day, period, False, {}, None, 0, 4,
                         rows, TODAY, limited, 1., context)[0]


def test_all_aerobic_targets_use_lower_planning_reference_without_inflating_e_or_history():
    profile, _, source, rows, _, context, measured = low_history()
    original_source = deepcopy(source)
    historical_only = deepcopy(context)
    for z, value in measured.items():
        historical_only["components"][z]["reference_q"] = value
    before = goals(profile, rows, historical_only)
    after = goals(profile, rows, context)
    for z, (low, _) in policy.WEEKLY_Q_BOUNDS.items():
        component = context["components"][z]
        assert component["weekly_q"] == pytest.approx(measured[z])
        assert component["reference_q"] == low
        assert component["limitation"] == "BELOW_REFERENCE_BOUND"
        assert after[z]["target_weekly_q"] == pytest.approx(before[z]["target_weekly_q"]*low/measured[z])
        assert after[z]["target"] == before[z]["target"]  # E/7–40 never inherits the Q floor.
        assert after[z]["target_index"] == before[z]["target_index"]
    assert source == original_source
    assert context["reference_is_clamped"]


def test_outlook_uses_five_minute_z5_basis_and_keeps_recovery_and_partial_weeks():
    profile, _, _, rows, phases, context, _ = low_history()
    reference = {"cutoff": TODAY.isoformat()}
    preferences = {"mesocycle_anchor_date": TODAY.isoformat()}
    outlook = engine._long_term_outlook(profile, phases, reference, None, preferences, rows, TODAY, False, progression=context)
    full = [w for w in outlook["weeks"] if w["days"] == 7]
    loading = [w for w in full if w["cycle"]["kind"] == "BUILD"]
    recovery = [w for w in full if w["cycle"]["kind"] == "RECOVERY"]
    assert loading and recovery
    assert loading[0]["components"]["Z5"]["target_period_q"] >= 5
    assert recovery[0]["components"]["Z5"]["target_period_q"] < 5
    short = engine._long_term_outlook(profile, phases, reference, None, preferences, rows, TODAY+timedelta(days=6), False, progression=context)["weeks"][0]
    assert short["days"] == 1
    assert short["components"]["Z5"]["target_period_q"] == pytest.approx(short["components"]["Z5"]["target_weekly_q"]/7, abs=.001)
    assert context["components"]["Z5"]["target_q"] > 5
    assert context["components"]["Z5"]["weekly_q"] == pytest.approx(110/60)


@pytest.mark.parametrize("version", ["load-progression-v3-stable-q", "load-progression-v4-clamped-q",
                                    "load-progression-v5-cycle-q", "load-progression-v6-individual-reference"])
def test_saved_anchors_migrate_once_without_resetting_dates_or_capping_high_personal_volume(version):
    profile, _, _, _, _, context, _ = low_history()
    anchor = deepcopy(context["anchor"])
    anchor["version"] = version
    anchor["components"]["Z5"].update(reference_q=110/60, reference_selection="OBSERVED")
    anchor["components"]["Z3"].update(weekly_q=210., reference_q=120., reference_selection="UPPER_BOUND")
    original = deepcopy(anchor)
    later = TODAY+timedelta(days=50)
    migrated = policy.context(profile, {}, [], later, retained=anchor)
    assert migrated["anchor_reused"]
    assert migrated["anchor"]["created_on"] == anchor["created_on"]
    assert migrated["anchor"]["windows"] == anchor["windows"]
    assert migrated["components"]["Z5"]["reference_q"] == 5
    assert migrated["components"]["Z5"]["weekly_q"] == pytest.approx(110/60)
    assert migrated["components"]["Z3"]["reference_q"] == 210
    assert policy.context(profile, {}, [], later, retained=migrated["anchor"]) == migrated
    assert anchor == original


@pytest.mark.parametrize("exposure", [None, 0.])
def test_lower_reference_does_not_authorize_missing_or_zero_recent_exposure(exposure):
    profile, _, _, rows, _, context, _ = low_history()
    context["components"]["Z5"]["recent_observed_q"] = exposure
    assert goals(profile, rows, context)["Z5"]["target_weekly_q"] == exposure


def test_reentry_and_manual_targets_remain_independent_of_lower_reference():
    profile, _, _, rows, _, context, _ = low_history()
    historical_only = deepcopy(context)
    historical_only["components"]["Z5"]["reference_q"] = context["components"]["Z5"]["recent_observed_q"]
    assert goals(profile, rows, context, period="RE_ENTRY")["Z5"]["target_weekly_q"] == goals(profile, rows, historical_only, period="RE_ENTRY")["Z5"]["target_weekly_q"]
    profile["component_targets_weekly"] = {"Z5": 1.}
    before, after = goals(profile, rows, historical_only)["Z5"], goals(profile, rows, context)["Z5"]
    assert after["target"] == before["target"]
    assert after.get("target_weekly_q") == before.get("target_weekly_q")
    assert after["progression"]["manual_override"]


def test_composed_sessions_keep_actual_budgets_readiness_and_session_limits(monkeypatch):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    profile, repo, _, _, _, _, _ = low_history()
    profile["planning_controls"].update(sessions_per_week=3, sessions_by_day=[0,1,0,1,0,1,0], mixed_sessions_enabled=False)
    profile["available_minutes"] = [0,80,0,80,0,80,0]
    plan = engine.generate_plan(repo, "athlete", profile, start_date=TODAY+timedelta(days=1), now=NOW)
    assert plan["parameters"]["load_progression"]["components"]["Z5"]["reference_q"] == 5
    sessions = [s for d in plan["days"] for s in d["sessions"]]
    assert sessions and len(sessions) <= 3
    for day in plan["days"]:
        for z in engine.COMPONENTS:
            assert sum(s["canonical_effective_load"][z] for s in day["sessions"]) <= day["load_budget"]["components"][z]["deficit_effective"]+.01
        for session in day["sessions"]:
            assert session["total_minutes"] <= 80
            assert day["readiness_before"][session["zone"]] >= 90
            assert session["dose_evidence"]["applied_structure_fraction"] <= .8005
