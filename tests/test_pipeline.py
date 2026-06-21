from datetime import date

import pytest

from biathlon.demo_data import generate_demo_bundle
from biathlon.service import analyze_athlete, team_summary


def test_full_pipeline_generates_real_plan():
    bundle = generate_demo_bundle(history_days=120)
    analysis = analyze_athlete(bundle, "A", as_of=date.today(), generate_plan=True)
    plan = analysis["plan"]
    preferences = analysis["planning_preferences"]

    assert plan["date"].nunique() == 7
    assert int((plan["focus"] != "REST").sum()) == int(preferences["sessions_per_week"])
    assert plan["total_real_min"].ge(0).all()
    assert set(analysis["load_stats"].index) == {"Z1", "Z2", "Z3", "Z4", "Z5", "STR"}
    assert analysis["integrated"]["integrated_readiness"].between(0, 100).all()
    assert analysis["decision_snapshot"]["snapshot_type"] == "DecisionSnapshot"
    assert analysis["decision_snapshot"]["algorithm_version"] == "streamlit-demo-0.5.0"


def test_profiles_produce_different_adaptation():
    bundle = generate_demo_bundle(history_days=120)
    a = analyze_athlete(bundle, "A", generate_plan=False)
    b = analyze_athlete(bundle, "B", generate_plan=False)
    c = analyze_athlete(bundle, "C", generate_plan=False)
    values = {round(a["global_readiness"], 1), round(b["global_readiness"], 1), round(c["global_readiness"], 1)}
    assert len(values) >= 2


def test_team_summary_has_three_demo_profiles():
    bundle = generate_demo_bundle(history_days=90)
    summary = team_summary(bundle)
    assert len(summary) == 3


def test_pipeline_still_works_without_activity_history():
    bundle = generate_demo_bundle(history_days=90)
    bundle["activities"] = bundle["activities"].loc[bundle["activities"]["athlete_id"] != "A"].copy()
    analysis = analyze_athlete(bundle, "A", generate_plan=True)

    assert analysis["activity_summaries"].empty
    assert analysis["annual_context"]["history_reliability"] == 0.0
    assert analysis["plan"]["date"].nunique() == 7
    assert analysis["load_stats"]["index_7_40"].notna().all()


def test_strength_sessions_use_declared_type_and_coefficient():
    from biathlon.constants import STRENGTH_COEFFICIENTS, STRENGTH_TYPES

    bundle = generate_demo_bundle(history_days=120)
    analysis = analyze_athlete(bundle, "A", as_of=date.today(), generate_plan=True)
    strength = analysis["plan"].loc[analysis["plan"]["focus"] == "STR"]

    assert not strength.empty
    assert set(strength["strength_type"]).issubset(set(STRENGTH_TYPES))
    for _, row in strength.iterrows():
        expected_k = STRENGTH_COEFFICIENTS[str(row["strength_type"])]
        assert float(row["strength_coefficient"]) == expected_k
        assert float(row["strength_equivalent_min"]) == pytest.approx(
            float(row["strength_real_min"]) * expected_k, abs=0.2
        )
