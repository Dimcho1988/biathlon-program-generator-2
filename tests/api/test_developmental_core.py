"""Preparation keeps the smallest complete developmental method dose."""
from copy import deepcopy

from apps.api import training_plan_engine as engine
from biathlon import planning_development
from tests.api.test_curve_tmax_nominal_dose_v3 import model_method
from tests.api.test_training_plan_engine import Repository
from tests.api.test_trial_budget_copy_reuse import budget_fixture, prescribe


def test_discrete_core_does_not_add_a_repeat_above_the_passing_search_boundary():
    method = model_method()  # Authored Z4: 2–6 repetitions of 3 minutes.
    evidence = {"capacity_minutes": 34., "target_hr_bpm": None, "target_speed_kmh": None,
                "readiness_policy": {"allowed": True, "observed_dose_factor": .8}}
    settings = Repository().settings
    fraction = engine._nominal_fraction(method, "BUILDING", {}, "GENERAL_PREPARATION", evidence, {})
    evidence["base_fraction"] = fraction
    maximum = engine._quantized_work(method,
        min(method["max_work_min"], 34.*fraction*evidence["readiness_dose_factor"]))
    assert maximum == 15.
    blocks = engine._blocks(method, maximum, evidence, settings)
    before = deepcopy((method, evidence, blocks))

    work, core = engine._developmental_core(method, evidence, settings, purpose="BUILDING",
        minimum=engine._minimum_work(method, evidence, settings), maximum=maximum, blocks=blocks)

    assert work == 12.  # Four repeats exceed maintenance (10.8 min); five are unnecessary.
    assert [b["duration_min"] for b in core if b["kind"] == "WORK"] == [3.]*4
    session = {"purpose": "BUILDING", "zone": "Z4", "dose_evidence": evidence}
    assert planning_development.is_developmental({**session, "blocks": core})
    assert not planning_development.is_developmental({**session,
        "blocks": engine._blocks(method, 9., evidence, settings)})
    assert (method, evidence, blocks) == before


def test_combination_rechecks_development_after_quantizing_its_primary_work(monkeypatch):
    method, evidence, settings = budget_fixture("combined", monkeypatch)
    prepared = prescribe(method, evidence, settings, "BUILDING")
    maximum = 15.
    blocks = engine._blocks(method, maximum, evidence, settings)
    before = deepcopy((method, evidence, blocks))

    work, core = engine._developmental_core(method, evidence, settings, purpose="BUILDING",
        minimum=prepared["minimum"], maximum=maximum, blocks=blocks)

    assert work == 12.
    session = {"purpose": "BUILDING", "zone": method["zone"], "dose_evidence": evidence}
    assert planning_development.is_developmental({**session, "blocks": core})
    assert not planning_development.is_developmental({**session,
        "blocks": engine._blocks(method, 9., evidence, settings)})
    for index, minimum in enumerate(evidence["combined_minimum_work_minutes"], 1):
        part = [b for b in core if b["kind"] == "WORK" and b.get("combination_part") == index]
        assert part and sum(b["duration_min"] for b in part) >= minimum
    assert (method, evidence, blocks) == before
