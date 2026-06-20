from datetime import date

import pandas as pd
import pytest

from biathlon.constants import COMPONENTS
from biathlon.demo_data import generate_demo_bundle
from biathlon.preferences import (
    annual_volume_context,
    build_week_structure,
    default_planning_preferences,
    normalize_preferences,
    weekly_totals_to_activities,
)
from biathlon.service import analyze_athlete


def test_week_structure_honours_sessions_rest_and_double_days():
    prefs = default_planning_preferences("A", date(2026, 6, 20))
    prefs.update(
        {
            "sessions_per_week": 9,
            "rest_days": [0],
            "double_session_days": [2, 5],
            "double_threshold_enabled": False,
        }
    )
    structure = build_week_structure(date(2026, 6, 20), prefs)

    assert structure["date"].nunique() == 7
    assert int(structure["planned_training"].sum()) == 9
    monday = structure.loc[structure["day_index"] == 0]
    assert not monday["planned_training"].any()
    assert int(structure.groupby("date")["planned_training"].sum().max()) <= 2


def test_normalization_rejects_all_rest_days_safely():
    prefs = default_planning_preferences("A")
    prefs["rest_days"] = list(range(7))
    normalized = normalize_preferences(prefs)

    assert normalized["rest_days"] == [0]
    assert normalized["sessions_per_week"] >= 1


def test_double_threshold_can_activate_when_rules_are_met():
    bundle = generate_demo_bundle(history_days=120)
    prefs = bundle["planning_preferences"]["A"]
    prefs.update(
        {
            "sessions_per_week": 9,
            "rest_days": [0],
            "double_threshold_enabled": True,
            "double_threshold_day": 2,
            "double_threshold_min_readiness": 60.0,
            "double_threshold_phase_min": 0.0,
            "double_threshold_phase_max": 1.0,
            "max_key_sessions_per_week": 4,
        }
    )
    analysis = analyze_athlete(bundle, "A", as_of=date(2026, 6, 20), generate_plan=True)
    plan = analysis["plan"]

    dt_rows = plan.loc[plan["double_threshold"]]
    assert analysis["decision_snapshot"]["plan"]["double_threshold_active"] is True
    assert len(dt_rows) == 2
    assert dt_rows["date"].nunique() == 1
    assert set(dt_rows["focus"]).issubset({"Z3", "Z4"})


def test_weekly_totals_are_preserved_by_onboarding_distribution():
    prefs = default_planning_preferences("A", date(2026, 6, 20))
    totals = {"Z1": 120.0, "Z2": 360.0, "Z3": 30.0, "Z4": 15.0, "Z5": 6.0, "STR": 45.0}
    weekly = pd.DataFrame(
        [
            {
                "week_start": "2026-06-08",
                "sessions": 9,
                **totals,
                "rpe": 4.5,
            }
        ]
    )
    activities = weekly_totals_to_activities(weekly, "A", prefs)

    assert len(activities) == 9
    assert activities["activity_id"].is_unique
    for component in COMPONENTS:
        assert activities[f"real_{component}"].sum() == pytest.approx(totals[component], abs=0.15)


def test_annual_goal_factor_is_bounded_and_history_weighted():
    prefs = default_planning_preferences("A", date(2026, 6, 20))
    prefs.update({"annual_target_hours": 600.0, "min_volume_factor": 0.90, "max_volume_factor": 1.12})
    rows = []
    for day in pd.date_range("2026-01-01", "2026-06-19", freq="D"):
        row = {"date": day}
        for component in COMPONENTS:
            row[f"real_{component}"] = 0.0
        row["real_Z2"] = 45.0
        rows.append(row)
    context = annual_volume_context(pd.DataFrame(rows), prefs, as_of=date(2026, 6, 20))

    assert 0.90 <= context["volume_factor"] <= 1.12
    assert context["history_reliability"] == pytest.approx(1.0)
    assert context["target_hours"] == 600.0
