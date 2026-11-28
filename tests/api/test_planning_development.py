"""Frequency credit follows genuine method doses and whole model cycles."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from apps.api.training_sessions import RecordedSession
from biathlon import planning_development as development, strength_methods

START = "2026-11-23"
END = "2026-11-29"
SEGMENTS = [{"window_start": START, "window_end": END}]


def session(zone="Z1", work=80., nominal=100., role="BUILDING"):
    return {"zone": zone, "purpose": role, "blocks": [
        {"zone": zone, "kind": "WORK", "duration_min": work},
        {"zone": "Z1", "kind": "WARMUP", "duration_min": 15.}],
        "dose_evidence": {"resolved_role": role, "capacity_minutes": nominal/.65,
                          "base_fraction": .65, "readiness_dose_factor": 1.}}


def cycles(kind="BUILD"):
    return {(date.fromisoformat(START)+timedelta(days=i)).isoformat(): {"kind": kind} for i in range(7)}


@pytest.mark.parametrize("requested,minimum,target", [(0, 2, 2), (1, 2, 2), (2, 2, 2), (3, 2, 3), (4, 2, 4)])
def test_loading_minimum_is_separate_from_high_zone_maximum(requested, minimum, target):
    for kind in ("BUILD", "STRESS"):
        value = development.policy({"max_key_sessions_per_week": requested}, {"kind": kind})
        assert (value["minimum"], value["target"]) == (minimum, target)
        assert value["requested"] == requested
        assert value["key_session_ceiling_unchanged"]


@pytest.mark.parametrize("kind", ["RECOVERY", "MAINTAIN", "MAINTENANCE", "RE_ENTRY", "TRANSITION"])
def test_nonloading_cycles_have_no_developmental_quota(kind):
    assert development.policy({"max_key_sessions_per_week": 3}, {"kind": kind})["target"] == 0


@pytest.mark.parametrize("override", [{"limited": True}, {"taper": True}])
def test_exceptions_do_not_force_building_into_taper_or_limited_plan(override):
    assert not development.policy({}, {"kind": "BUILD"}, **override)["required"]


@pytest.mark.parametrize("zone", ["Z1", "Z2", "Z3", "Z4", "Z5"])
def test_all_endurance_components_count_only_when_real_work_exceeds_maintenance(zone):
    assert development.is_developmental(session(zone, 80))
    assert not development.is_developmental(session(zone, 60))
    assert not development.is_developmental(session(zone, 40))
    assert not development.is_developmental(session(zone, 80, role="MAINTENANCE"))


def test_library_standard_owns_maintenance_comparison_not_generic_tmax():
    proposed = session("Z5", 13., nominal=100.)
    proposed["dose_evidence"]["standard_method_work_minutes"] = 20.
    assert development.is_developmental(proposed)
    proposed["blocks"][0]["duration_min"] = 12.
    assert not development.is_developmental(proposed)


def test_warmup_or_easy_supplement_cannot_make_tiny_quality_dose_building():
    proposed = session("Z4", 5., nominal=20.)
    proposed["blocks"] += [{"zone": "Z1", "kind": "WORK", "duration_min": 100.}]
    assert not development.is_developmental(proposed)


def test_combined_parents_count_as_one_occasion_with_their_own_roles_and_doses():
    proposed = session("Z4", 0.)
    proposed["blocks"] = [{"zone": z, "kind": "WORK", "duration_min": minutes, "combination_part": i}
                          for i, z, minutes in [(1, "Z4", 15.), (2, "Z2", 80.)]]
    proposed["dose_evidence"]["combined_budgets"] = [
        {"zone": "Z4", "role": "BUILDING", "nominal_work_minutes": 20.},
        {"zone": "Z2", "role": "BUILDING", "nominal_work_minutes": 100.}]
    report = development.count([{"date": START, "sessions": [proposed], "session": proposed}], start=START, end=END)
    assert report["planned"] == 1
    proposed["dose_evidence"]["combined_budgets"][0]["role"] = "MAINTENANCE"
    proposed["dose_evidence"]["combined_budgets"][1]["role"] = "MAINTENANCE"
    assert not development.is_developmental(proposed)


def test_strength_uses_its_own_reduced_whole_set_dose():
    method = next(m for m in strength_methods.methods({"sport": "Run", "strength_enabled": True}) if m["strength_profile"]["key"] == "MAX")
    strength = method["strength_profile"]
    nominal = strength_methods.options(strength)[-1][1]
    proposed = session("STR", nominal, nominal)
    proposed["dose_evidence"]["strength_profile"] = strength
    assert development.is_developmental(proposed)
    proposed["blocks"][0]["duration_min"] = nominal*strength_methods.reduced_fraction(strength)
    assert not development.is_developmental(proposed)


def test_canonical_reference_still_requires_real_work_not_a_counts_flag():
    proposed = session("Z4", 5., nominal=20.)
    proposed["dose_evidence"]["developmental"] = {"role": "BUILDING", "maintenance_work_minutes": 12., "counts": True}
    assert not development.is_developmental(proposed)
    proposed["blocks"][0]["duration_min"] = 13.
    assert development.is_developmental(proposed)


def test_legacy_single_day_locked_sessions_and_confirmed_actual_count_once():
    actual = RecordedSession(START, ({"activity_ref": "done"},), confirmed_methods={
        "done": {"resolved_role": "BUILDING", "developmental": {"counts": True}}})
    days = [{"date": "2026-11-25", "locked": True, "session": session()}]
    report = development.report({"max_key_sessions_per_week": 3}, SEGMENTS, days, [actual], cycles=cycles())
    row = report["segments"][0]
    assert (row["actual"], row["planned"], row["total"]) == (1, 1, 2)
    assert row["minimum_shortfall"] == 0
    assert row["target_shortfall"] == 1
    assert report["needs_search"]


def test_unlabelled_completed_intensity_is_unknown_not_automatic_development():
    actual = RecordedSession(START, ({"activity_ref": "done", "zones": [{"zone": "Z4", "raw_time_min": 20.}]},))
    report = development.report({}, SEGMENTS, [], [actual], cycles=cycles())
    row = report["segments"][0]
    assert row["unknown_actual"] == 1
    assert row["actual"] == 0
    assert row["minimum_shortfall"] == 2
    assert not row["hard_limit_proven"]


def test_confirmed_execution_of_a_reduced_label_keeps_the_verified_dose_classification():
    actual = RecordedSession(START, ({"activity_ref": "done"},), confirmed_methods={
        "done": {"resolved_role": "BUILDING", "developmental": {"counts": False}}})
    assert development.recorded_evidence(actual) == {"counts": False, "known": True, "basis": "CONFIRMED_METHOD_EXECUTION"}


def test_legacy_confirmed_building_label_without_dose_evidence_is_unknown():
    actual = RecordedSession(START, ({"activity_ref": "done"},), confirmed_methods={
        "done": {"resolved_role": "BUILDING"}})
    result = development.count([], [actual], start=START, end=END)
    assert result["actual"] == result["total"] == 0
    assert result["unknown_actual"] == 1


def test_whole_cycle_includes_lookahead_and_does_not_borrow_next_cycle_credit():
    days = [{"date": d, "sessions": [session()]} for d in ("2026-11-25", "2026-11-28", "2026-11-30")]
    report = development.report({"max_key_sessions_per_week": 3}, SEGMENTS, days, cycles=cycles())
    assert report["segments"][0]["total"] == 2
    assert report["minimum_shortfall"] == 0
    assert report["target_shortfall"] == 1


def test_truncated_programme_does_not_cram_whole_week_quota_into_two_days():
    report = development.report({}, [{"window_start": "2026-11-28", "window_end": END}], [], cycles=cycles())
    row = report["segments"][0]
    assert row["status"] == "NOT_ASSESSED_PARTIAL_CYCLE"
    assert not report["needs_search"]
    assert not row["assessed"]


def test_recovery_only_maintenance_stays_accepted_and_inputs_are_unchanged():
    days = [{"date": START, "sessions": [session(role="MAINTENANCE")]}]
    frozen = deepcopy(days)
    report = development.report({}, SEGMENTS, days, cycles=cycles("RECOVERY"))
    assert report["segments"][0]["status"] == "MAINTENANCE_ONLY"
    assert not report["needs_search"]
    assert days == frozen


def test_limited_loading_cycle_has_no_frequency_shortfall_or_search_request():
    result = development.report({"max_key_sessions_per_week": 3}, SEGMENTS, [],
                                cycles=cycles("BUILD"), limited=True)
    row = result["segments"][0]
    assert not row["required"]
    assert row["minimum"] == row["target"] == 0
    assert row["minimum_shortfall"] == row["target_shortfall"] == 0
    assert not result["needs_search"]
