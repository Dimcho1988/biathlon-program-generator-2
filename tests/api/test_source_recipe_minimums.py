"""Role-neutral recipes retain authored complete work, sets and rests."""
from copy import deepcopy

import pytest

from biathlon import method_library as library
from tests.api.test_sourced_method_library import athlete, view, recipe


def resolve(record, profile=None):
    result = library.resolve(record, profile or athlete(), view(), phase="SPECIAL_PREPARATION")
    assert result["status"] == "READY", result
    return result


def test_published_twelve_repeat_recipe_does_not_resolve_as_one_or_two():
    source = next(r for r in library.entries() if r["id"] == "R02")
    original = deepcopy(source)
    result = resolve(source)
    assert result["repetitions"] == [12, 12]
    assert result["source_minimum_repetitions"] == 12
    assert result["minimum_work_seconds"] == pytest.approx(12 * result["cycle_work_seconds"])
    assert result["source_work_options_seconds"] == [result["minimum_work_seconds"]]
    assert result["role_reduction_allowed"] is True
    assert result["work_duration_seconds"] == pytest.approx(200 / (result["target_speed_kmh"] / 3.6))
    assert result["recovery_seconds"] == 180
    assert source == original


def test_declared_ten_to_thirty_repeat_recipe_preserves_its_lower_bound():
    source = recipe()
    source["prescription"].update(
        anchor={"type": "V5"}, speed_multiplier=[1, 1],
        work={"unit": "seconds", "range": [45, 45]},
        source_repetitions_range=[10, 30], repetitions=[10, 30],
        allow_fewer_repetitions=True, minimum_repetitions=1,
        recovery={"seconds": [15, 15], "type": "ACTIVE_Z1"},
    )
    result = resolve(source)
    assert result["repetitions"] == [10, 30]
    assert result["minimum_work_seconds"] == 450
    assert result["source_work_options_seconds"] == [45 * n for n in range(10, 31)]
    assert result["recovery_seconds"] == 15


def test_compound_floor_keeps_every_part_and_every_set():
    source = recipe()
    source["prescription"].update(
        anchor={"type": "V5"}, speed_multiplier=[1, 1], structure="COMPOUND",
        repetitions=[4, 4], source_repetitions_range=[4, 4],
        allow_fewer_repetitions=True, minimum_repetitions=1,
        sets={"count": 3, "recovery_seconds": 180, "recovery_type": "PASSIVE"},
        pattern=[{"work": {"unit": "seconds", "range": [30, 30]}},
                 {"work": {"unit": "seconds", "range": [15, 15]}}],
    )
    result = resolve(source)
    assert result["sets"] == 3
    assert result["cycle_work_seconds"] == 45
    assert result["source_minimum_repetitions"] == 4
    assert result["source_minimum_work_seconds"] == 4 * 3 * 45
    assert result["work_options_seconds"] == [4 * 3 * 45]
    assert len(result["segments"]) == 2
    assert result["set_recovery_seconds"] == 180


def test_explicit_coach_repeat_minimum_is_not_subject_to_automatic_reduction():
    source = recipe()
    source["prescription"].update(
        source_repetitions_range=[8, 8], repetitions=[8, 8],
        allow_fewer_repetitions=True, minimum_repetitions=1,
    )
    result = resolve(source, athlete(method_library={"overrides": {"TEST": {"repetitions": [4, 6]}}}))
    assert result["source_repetitions"] == [8, 8]
    assert result["repetitions"] == [4, 6]
    assert result["source_minimum_repetitions"] == 4
    assert result["role_reduction_allowed"] is False
    assert result["source_work_options_seconds"] == pytest.approx(
        [result["cycle_work_seconds"] * n for n in (4, 5, 6)])


def test_a_single_authored_complete_sequence_is_not_banned_by_a_generic_rep_floor():
    source = recipe()
    source["prescription"].update(
        anchor={"type": "V5"}, speed_multiplier=[1, 1], structure="COMPOUND",
        repetitions=[1, 1],
        pattern=[{"work": {"unit": "seconds", "range": [30, 30]}},
                 {"work": {"unit": "seconds", "range": [60, 60]}}],
    )
    result = resolve(source)
    assert result["source_minimum_repetitions"] == 1
    assert result["source_minimum_work_seconds"] == 90
    assert result["work_options_seconds"] == [90]
    assert result["role_reduction_allowed"] is False


def test_validated_personal_ten_repeat_selection_retains_its_exact_minimum(monkeypatch):
    from biathlon import personal_methods
    from apps.api import training_plan_engine as engine
    from tests.api.test_source_standard_dosing import source_record
    from tests.api.test_sourced_method_library import adapter

    source = source_record()
    source["prescription"]["allowed_variants"] = [deepcopy(source["prescription"]["standard_variant"])]
    definition = {"id": "PERSONAL-ten", "name": "Selected ten repetitions", "base_method_id": "TEST",
                  "work_unit": "seconds", "work_range": [60, 60], "repetitions": [10, 10]}
    profile = athlete(method_library={"personal_methods": [definition],
        "overrides": {"PERSONAL-ten": {"repetitions": [1, 99], "work_range": [15, 15]}}})
    selected = personal_methods.source_methods(profile, [source])[0]
    assert selected["personal_variant"]["validation"]["status"] == "ACCEPTED"
    result = resolve(selected, profile)
    assert result["source_minimum_repetitions"] == 10
    assert result["source_minimum_work_seconds"] == 600
    assert result["role_reduction_allowed"] is False
    assert result["work_options_seconds"] == [600]
    method, evidence, settings = adapter(monkeypatch, selected, profile)
    fraction = engine._nominal_fraction(method, "MAINTENANCE", profile, "SPECIAL_PREPARATION", evidence, {})
    assert evidence["capacity_minutes"] * fraction == pytest.approx(6)
    assert method["min_work_min"] == 10
    assert engine._blocks(method, 6, evidence, settings) == []
