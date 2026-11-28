from copy import deepcopy
from datetime import date

import pytest
from pydantic import ValidationError

from apps.api import response_service, training_plan_engine as engine
from apps.api.management_schemas import ManagementProfile
from apps.api.response_monitoring import SessionReport, session_rows
from apps.api.training_observation_schemas import LactateProfile, LactateSample, NeuromuscularProfile
from biathlon import training_guidance as guidance
from biathlon.training_methods import resolved_methods
from tests.api.test_training_plan_engine import Repository, BOUNDS, TODAY, NOW, profile, reference_speed
from tests.api.test_management_schedule import body, high_capacity_history


def personal(**changes):
    return LactateProfile(sport="Run", assessed_on=TODAY, source="MANUAL",
                          zone_ranges={"Z3": {"low_mmol": 5., "high_mmol": 7.}}, **changes).model_dump(mode="json")


def test_individual_lactate_can_exceed_population_reference_without_clamping_or_sport_transfer():
    p = {"lactate_profiles": [personal()]}
    ref = guidance.lactate_reference(p, "Run", "Z3", BOUNDS, TODAY)
    assert (ref["low_mmol"], ref["high_mmol"]) == (5, 7)
    assert ref["source"] == "INDIVIDUAL_MANUAL" and ref["measured"] is False
    other = guidance.lactate_reference(p, "NordicSki", "Z3", BOUNDS, TODAY)
    assert other["source"] == "GENERAL_OLT_2024"
    assert guidance.lactate_reference(p, "Run", "Z5", BOUNDS, TODAY)["high_mmol"] is None
    assert guidance.lactate_reference({**p, "lactate_guidance_enabled": False}, "Run", "Z3", BOUNDS, TODAY) is None


def test_test_interpolation_is_covered_dated_and_overridden_by_coach_interpretation():
    test = LactateProfile(sport="Run", source="TEST", assessed_on=TODAY, protocol="4 min stages, immediate sample",
                          stages=[{"hr_bpm": hr, "lactate_mmol": la} for hr, la in [(130, 1), (155, 3), (180, 6)]]).model_dump(mode="json")
    p = {"lactate_profiles": [test]}
    ref = guidance.lactate_reference(p, "Run", "Z3", BOUNDS, TODAY)
    assert ref["source"] == "TEST_INTERPOLATION"
    assert (ref["low_mmol"], ref["high_mmol"]) == pytest.approx((2.2, 3.6))
    assert guidance.lactate_reference(p, "Run", "Z2", BOUNDS, TODAY)["source"] == "GENERAL_OLT_2024"
    assert guidance.lactate_reference(p, "Run", "Z5", BOUNDS, TODAY)["source"] == "INDIVIDUAL_REQUIRED"
    test["zone_ranges"] = {"Z3": {"low_mmol": 4., "high_mmol": 6.}}
    assert guidance.lactate_reference(p, "Run", "Z3", BOUNDS, TODAY)["low_mmol"] == 4
    assert guidance.lactate_reference(p, "Run", "Z3", BOUNDS, date(2026, 9, 20))["source"] == "GENERAL_OLT_2024"


def test_lactate_comparison_needs_matching_context_and_never_changes_load():
    sample = LactateSample(value_mmol=4.2, planned_high_mmol=4, delay_seconds=60).model_dump()
    assert guidance.lactate_comparisons([sample])[0]["comparison"] == "NOT_COMPARED"
    result = guidance.lactate_comparisons([{**sample, "comparison_confirmed": True}])[0]
    assert result["comparison"] == "ABOVE" and result["difference_mmol"] == .2
    assert result["automatic_load_weight"] == 0
    with pytest.raises(ValidationError):
        LactateSample(value_mmol=3, after="REPETITION")
    with pytest.raises(ValidationError):
        LactateSample(value_mmol=3, planned_high_mmol=4, comparison_confirmed=True)
    with pytest.raises(ValidationError):
        LactateSample(value_mmol=float("nan"))


def test_observations_persist_under_exact_activity_and_survive_history_read():
    from tests.api.test_response_monitoring import Repository as ResponseRepository, activity, TODAY as DAY, NOW as INSTANT, ACTOR
    repo = ResponseRepository()
    report = SessionReport(activity_ref=activity(DAY)["activity_ref"], duration_minutes=60,
        lactate_samples=[{"value_mmol": 3.2, "after": "REPETITION", "repetition": 4, "delay_seconds": 30,
                         "planned_low_mmol": 2., "planned_high_mmol": 4., "comparison_confirmed": True}],
        neuromuscular={"repetitions": 4, "work_seconds": 40, "peak_speed_kmh": 29}, expected_revision=2)
    response_service.save_report(repo, "ath-test", "SESSION", report, ACTOR, now=INSTANT)
    saved = repo.saved
    assert saved["p_expected_revision"] == 2 and saved["p_key"] == report.activity_ref
    rows = session_rows([activity(DAY)], {("SESSION", report.activity_ref): {"revision": 3, "payload": saved["p_payload"]}})
    assert rows[0]["lactate_samples"][0]["comparison"] == "WITHIN"
    assert rows[0]["neuromuscular"]["work_seconds"] == 40
    assert rows[0]["srpe_load"] is None  # Lactate-only observations do not fabricate RPE.


def test_nms_structure_counts_time_and_recovery_without_fabricating_z5():
    nms = NeuromuscularProfile(enabled=True).model_dump()
    blocks = guidance.neuromuscular_blocks(nms, 115)
    assert len(blocks) == 8
    assert sum(b["duration_s"] for b in blocks) == 4 * (10 + 120)
    assert all(b["target_hr_bpm"] is None for b in blocks if b["zone"] == "NMS")
    direct, _, _ = engine._canonical_load(blocks, Repository().settings, [], TODAY)
    assert direct["Z1"] > 0 and direct["Z5"] == 0
    assert guidance.nms_exposure(blocks)["metabolic_load_status"] == "NOT_MODELLED"
    assert guidance.nms_exposure(blocks)["work_seconds"] == 40
    assert not any(m.get("neuromuscular_profile") for m in resolved_methods({}))


def test_nms_generator_respects_day_time_budgets_and_default_off(monkeypatch):
    monkeypatch.setattr(engine.model_service, "speed_view", reference_speed)
    p = body(sessions_per_week=7)
    p.update(max_key_sessions_per_week=0, neuromuscular=NeuromuscularProfile(enabled=True, days=[TODAY.weekday()]).model_dump(),
             component_targets_weekly={"Z2": 0, "Z3": 0, "Z4": 0, "Z5": 0, "STR": 0})
    original = deepcopy(p)
    plan = engine.generate_plan(high_capacity_history(), "athlete", p, start_date=TODAY, now=NOW)
    sessions = [(d, s) for d in plan["days"] for s in d.get("sessions", [])]
    with_nms = [(d, s) for d, s in sessions if s.get("neuromuscular_exposure")]
    assert with_nms
    for d, s in with_nms:
        assert date.fromisoformat(d["date"]).weekday() == TODAY.weekday()
        assert s["total_minutes"] == pytest.approx(sum(b["duration_min"] for b in s["blocks"]), abs=.01)
        assert s["direct_equivalent_minutes"]["Z5"] == 0
        assert s["blocks"][0]["duration_min"] >= 15
        assert s["blocks"][1]["kind"] == "PREPARATION"
        assert any(b.get("lactate_reference") for b in s["blocks"])
    assert p == original
    p["neuromuscular"]["enabled"] = False
    disabled = engine.generate_plan(high_capacity_history(), "athlete", p, start_date=TODAY, now=NOW)
    assert not any(s.get("neuromuscular_exposure") for d in disabled["days"] for s in d.get("sessions", []))


def test_existing_profiles_remain_valid_and_individual_profile_requires_evidence():
    assert ManagementProfile.model_validate(body()).neuromuscular.enabled is False
    with pytest.raises(ValidationError):
        LactateProfile(sport="Run", source="TEST", assessed_on=TODAY, protocol="test")
    with pytest.raises(ValidationError):
        NeuromuscularProfile(enabled=True, work_seconds=20, recovery_seconds=30)
