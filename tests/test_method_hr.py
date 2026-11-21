"""Original HR prescriptions work without speed; no automatic anchor swaps."""
from copy import deepcopy

import pytest

from biathlon import method_hr


def record(**prescription_changes):
    prescription = {
        "anchor": {"type": "HRMAX"}, "hrmax_fraction": [.90, .95],
        "structure": "INTERVALS", "work": {"unit": "seconds", "range": [240, 240]},
        "repetitions": [4, 4], "sets": {"count": 1},
        "standard_variant": {"repetitions": 4, "sets": 1,
                             "work": {"unit": "seconds", "range": [240, 240]}},
        "recovery": {"seconds": [180, 180], "type": "ACTIVE", "hrmax_fraction": [.70, .70]},
    }
    prescription.update(prescription_changes)
    return {"id": "HR4", "sports": ["Run"], "prescription": prescription}


def view(hrmax=200):
    # No V(t), speed or tests. This is sufficient for an HR-native publication.
    return {"sport": "Run", "status": "UNAVAILABLE",
            "sport_hr_policy": {"sport_hrmax_bpm": hrmax,
                                "sport_zone_bounds_bpm": [100, 150, 170, 180, 190, 200]}}


def test_original_hr_protocol_does_not_require_speed_curve_or_synthetic_tmax():
    resolved = method_hr.resolve(record(), {"sport": "Run"}, view())
    assert resolved["status"] == "READY"
    assert resolved["standard_work_seconds"] == 960
    assert resolved["work_options_seconds"] == [960]
    assert resolved["target_hr_bpm"] == 185
    assert resolved["hr_reference_range_bpm"] == [180, 190]
    assert resolved["target_speed_kmh"] is None
    assert resolved["continuous_capacity_seconds"] is None
    assert resolved["capacity_reference"] == "SOURCE_STANDARD_WORK_NOT_PHYSIOLOGICAL_TMAX"
    assert resolved["recovery_resolution"]["target_hr_bpm"] == 140
    assert resolved["recovery_seconds"] == 180


def test_selected_standard_is_not_source_range_maximum_and_reductions_are_whole():
    r = record(repetitions=[4, 6], allow_fewer_repetitions=True)
    r["prescription"]["allowed_variants"] = [
        {"repetitions": n, "sets": 1, "work": {"unit": "seconds", "range": [240, 240]}}
        for n in (4, 5, 6)]
    resolved = method_hr.resolve(r, {"sport": "Run"}, view())
    assert resolved["status"] == "READY"
    assert resolved["standard_work_seconds"] == 4 * 240
    assert resolved["maximum_work_seconds"] == 6 * 240
    assert resolved["work_options_seconds"] == [240, 480, 720, 960, 1200, 1440]
    assert 960 * .6 not in resolved["work_options_seconds"]
    assert 960 * .9 not in resolved["work_options_seconds"]


def test_one_interval_beginner_method_has_no_invented_shorter_variant():
    r = record(repetitions=[1, 1], recovery={"seconds": [0, 0]},
               standard_variant={"repetitions": 1, "sets": 1,
                                 "work": {"unit": "seconds", "range": [240, 240]}})
    resolved = method_hr.resolve(r, {"sport": "Run"}, view())
    assert resolved["status"] == "READY"
    assert resolved["work_options_seconds"] == [240]
    assert resolved["minimum_work_seconds"] == 240


@pytest.mark.parametrize("anchor", ["MAS", "V6", "V5", "CV", "POWER", "ALL_OUT"])
def test_source_speed_and_effort_anchors_never_become_hr(anchor):
    r = record(anchor={"type": anchor})
    resolved = method_hr.resolve(r, {"sport": "Run"}, view())
    assert resolved["status"] == "UNAVAILABLE"
    assert resolved["reason"] == "SOURCE_NATIVE_HRMAX_ANCHOR_REQUIRED"


@pytest.mark.parametrize("hrmax", [None, 0, -10, float("nan"), float("inf"), True])
def test_missing_invalid_hrmax_does_not_create_protocol_targets(hrmax):
    result = method_hr.resolve(record(), {"sport": "Run"}, view(hrmax))
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "HRMAX_SETTING_REQUIRED"


def test_source_hr_range_is_retained_across_individual_zones():
    r = record(hrmax_fraction=[.85, .95])
    resolved = method_hr.resolve(r, {"sport": "Run"}, view())
    assert resolved["hr_reference_range_bpm"] == [170, 190]
    assert resolved["target_hr_bpm"] == 180
    assert resolved["zone"] == "Z4"


def test_disjoint_variants_do_not_combine_maximum_counts_and_duration():
    r = record(repetitions=[4, 6])
    r["prescription"]["allowed_variants"] = [
        {"repetitions": 4, "sets": 1, "work": {"unit": "seconds", "range": [240, 240]}},
        {"repetitions": 6, "sets": 1, "work": {"unit": "seconds", "range": [120, 120]}},
    ]
    resolved = method_hr.resolve(r, {"sport": "Run"}, view())
    assert resolved["status"] == "READY"
    assert resolved["repetitions"] == [4, 4]
    assert resolved["maximum_work_seconds"] == 960


def test_distance_only_hr_dose_has_no_fabricated_speed_or_duration():
    r = record(work={"unit": "meters", "range": [1000, 1000]}, standard_variant={})
    result = method_hr.resolve(r, {"sport": "Run"}, view())
    assert result["reason"] == "HR_PROTOCOL_REQUIRES_SOURCE_TIME_DOSE"


def test_source_and_profile_are_not_modified():
    r, profile, speed = record(), {"sport": "Run"}, view()
    before = deepcopy((r, profile, speed))
    assert method_hr.resolve(r, profile, speed)["status"] == "READY"
    assert (r, profile, speed) == before


def test_continuous_source_range_retains_bounds_and_uses_lower_standard():
    r = record(structure="CONTINUOUS", repetitions=[1, 1],
               work={"unit": "seconds", "range": [1200, 2400]},
               standard_variant={"repetitions": 1, "sets": 1,
                                 "work": {"unit": "seconds", "range": [1200, 2400]}},
               recovery={"seconds": [0, 0]})
    resolved = method_hr.resolve(r, {"sport": "Run"}, view())
    assert resolved["status"] == "READY"
    assert resolved["standard_work_seconds"] == 1200
    assert resolved["minimum_work_seconds"] == 1200
    assert resolved["maximum_work_seconds"] == 2400
    assert resolved["work_options_seconds"] is None


def test_explicit_coach_repetition_range_keeps_its_minimum_and_source_identity():
    r = record(allow_fewer_repetitions=True)
    r["prescription"]["source_repetitions_range"] = [4, 4]
    r["prescription"]["allowed_variants"] = [deepcopy(r["prescription"]["standard_variant"])]
    profile = {"sport": "Run", "method_library": {"overrides": {"HR4": {"repetitions": [2, 3]}}}}
    resolved = method_hr.resolve(r, profile, view())
    assert resolved["status"] == "READY"
    assert resolved["source_repetitions"] == [4, 4]
    assert resolved["repetitions"] == [2, 3]
    assert resolved["standard_work_seconds"] == 480
    assert resolved["work_options_seconds"] == [480, 720]
    assert resolved["source_count_policy"] == "EXPLICIT_COACH_REPEAT_RANGE"
