from datetime import date

from biathlon.demo_data import generate_demo_bundle
from biathlon.service import analyze_athlete, team_summary


def test_full_pipeline_generates_real_plan():
    bundle = generate_demo_bundle(history_days=120)
    analysis = analyze_athlete(bundle, "A", as_of=date.today(), generate_plan=True)
    assert len(analysis["plan"]) == 7
    assert analysis["plan"]["total_real_min"].ge(0).all()
    assert set(analysis["load_stats"].index) == {"Z1", "Z2", "Z3", "Z4", "Z5", "STR"}
    assert analysis["integrated"]["integrated_readiness"].between(0, 100).all()
    assert analysis["decision_snapshot"]["snapshot_type"] == "DecisionSnapshot"


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
