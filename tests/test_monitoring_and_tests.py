from datetime import date

from biathlon.demo_data import generate_demo_bundle
from biathlon.monitoring import analyze_wellness
from biathlon.testing import analyze_tests


def test_pain_creates_hard_flag():
    bundle = generate_demo_bundle(history_days=60)
    mask = (bundle["wellness"]["athlete_id"] == "A") & (bundle["wellness"]["date"].dt.date == date.today())
    bundle["wellness"].loc[mask, "pain"] = 8.5
    _, by_component, reasons = analyze_wellness(bundle["wellness"], "A", bundle["parameters"], date.today())
    assert by_component["hard_flag"].any()
    assert reasons


def test_test_adjustments_are_capped():
    bundle = generate_demo_bundle(history_days=60)
    details, adjustments = analyze_tests(bundle["tests"], "A", bundle["parameters"])
    assert not details.empty
    assert adjustments.max() <= bundle["parameters"]["max_positive_test_adjustment"] + 1e-9
    assert adjustments.min() >= bundle["parameters"]["max_negative_test_adjustment"] - 1e-9
