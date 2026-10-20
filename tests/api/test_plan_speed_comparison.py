from copy import deepcopy

import pytest

from apps.api import plan_speed_comparison as comparison
from biathlon import execution_ranges, speed_equivalence


def parameters():
    edges = [10., 13., 15., 18., 20., 24.]
    return speed_equivalence.prepare({"status": "AVAILABLE", "zones": [
        {"zone": f"Z{i+1}", "low_kmh": edges[i], "high_kmh": edges[i+1]}
        for i in range(5)], "v10": {"status": "AVAILABLE", "speed_kmh": 24., "source": "MEASURED_10_SECOND_MEAN"}})


def work(seconds=1800., speed=19., **changes):
    return {"kind": "WORK", "zone": "Z4", "duration_s": seconds,
            "duration_min": seconds/60, "target_speed_kmh": speed, **changes}


def zone(result, name):
    return next(row for row in result["zones"] if row["zone"] == name)


def test_exact_selected_plan_replays_both_settings_without_modifying_dose():
    session = {"sport": "Run", "blocks": [work()]}
    before = deepcopy(session)
    result = comparison.session_projection(session, parameters())
    z4 = zone(result, "Z4")
    assert z4["real_minutes"] == 30
    assert z4["equivalent_minutes"] == pytest.approx(26.25)
    assert z4["baseline_equivalent_minutes"] == pytest.approx(28.5)
    assert z4["normalized_q"] is None
    assert session == before
    assert "total_equivalent_minutes" not in result


def test_preparation_missing_speed_and_passive_rest_are_separate():
    session = {"sport": "Run", "blocks": [work(),
        {"kind": "WARMUP", "zone": "Z1", "duration_min": 10},
        {"kind": "RECOVERY", "zone": "Z1", "duration_min": 3, "passive_rest": True}]}
    result = comparison.session_projection(session, parameters())
    assert result["parts"]["WORK"]["coverage_percent"] == 100
    assert result["parts"]["WARMUP"]["status"] == "UNAVAILABLE"
    assert result["passive_rest_minutes"] == 3
    assert result["recorded_minutes"] == 40
    assert result["coverage_percent"] == 75
    assert result["uncovered_minutes"] == 10


def test_repeated_interval_evidence_is_lossless_without_repeated_zone_models():
    session = {"blocks": [work(seconds=60, speed=19) for _ in range(30)]}
    result = comparison.session_projection(session, parameters())
    assert len(result["blocks"]) == 1
    assert result["blocks"][0]["block_indices"] == list(range(30))
    interval = result["blocks"][0]["zones"][0]
    assert interval["real_seconds"] == 60
    assert interval["equivalent_minutes"] == pytest.approx(.875)
    assert interval["equivalent_minutes_range"] == pytest.approx([.875, .875])
    subtotal = result["parts"]["WORK"]
    assert "settings" not in subtotal and "parameters_version" not in subtotal
    assert len(subtotal["zones"]) == 1
    assert subtotal["zones"][0]["equivalent_minutes"] == pytest.approx(26.25)
    assert subtotal["zones"][0]["equivalent_minutes_range"] == pytest.approx([26.25, 26.25])
    assert len(result["zones"]) == 5  # All reference evidence remains in the session header.


def test_stored_session_references_identical_shared_header_and_retains_all_quantities():
    session = {"sport": "Run", "blocks": [work()]}
    full = comparison.session_projection(session, parameters())
    result = comparison.build([{"date": "2026-10-20", "sessions": [session]}],
        {"Run": {"comparison_parameters": parameters()}})
    stored = session["speed_equivalence_comparison"]
    header = result["sports"][0]["planned"]
    assert stored["storage_format"] == comparison.SESSION_STORAGE_FORMAT
    assert stored["parameters_version"] == header["parameters_version"]
    assert stored["sport"] == header["sport"]
    assert "settings" not in stored
    warnings = [header["warnings"][i] for i in stored["warning_indices"]]
    assert warnings == full["warnings"]
    for saved, original, shared in zip(stored["zone_values"], full["zones"], header["zones"]):
        assert original["zone"] == shared["zone"]
        assert [original[key] for key in comparison.SESSION_ZONE_VALUE_FIELDS] == saved
        assert "low_kmh" in shared
    for kind, part in stored["parts"].items():
        assert [warnings[i] for i in part["warning_indices"]] == full["parts"][kind]["warnings"]
    for compact, original in zip(stored["blocks"], full["blocks"]):
        assert compact["block_indices"] == original["block_indices"]
        for saved, row in zip(compact.get("zone_values", []), original.get("zones", [])):
            assert saved == [row[key] for key in comparison.BLOCK_ZONE_VALUE_FIELDS]


def test_source_fixed_intervals_never_gain_arbitrary_reduced_ranges():
    session = {"blocks": [work(seconds=240, speed=18,
        method_library={"id": "original"}, target_distance_m=1200)], "dose_evidence": {}}
    execution_ranges.annotate(session)
    block = session["blocks"][0]
    assert block["duration_range_seconds"] == [240, 240]
    assert block["target_speed_range_kmh"] == [18, 18]
    assert block["distance_range_m"] == [1200, 1200]
    assert session["execution_range_policy"]["dose_basis"] == "SELECTED_ADMISSIBLE_METHOD_VARIANT"


def test_source_distance_range_keeps_speed_time_pairing():
    # 1 km at 18–19 km/h has constant c=1 equivalent time in Z4.
    block = work(seconds=3600/18.5, speed=18.5,
        target_speed_range_kmh=[18, 19], duration_range_seconds=[3600/19, 200],
        method_library={"resolution": {"work_unit": "meters", "work_range": [1000, 1000]}})
    result = comparison.session_projection({"blocks": [block]}, parameters())
    assert zone(result, "Z4")["baseline_equivalent_minutes_range"] == pytest.approx([3, 3])
    lo, hi = zone(result, "Z4")["equivalent_minutes_range"]
    assert lo == pytest.approx(2.5)
    assert hi == pytest.approx((3600/19)/60*.875)


def test_paired_time_speed_range_does_not_combine_both_maxima():
    block = work(seconds=180, speed=18.5, target_speed_range_kmh=[18, 19],
        duration_range_seconds=[120, 240], method_library={"resolution": {
            "work_unit": "seconds", "work_range": [120, 240]}})
    result = comparison.session_projection({"blocks": [block]}, parameters())
    # Legal endpoints are 120s@19 and 240s@18; 240s@19 is not an option.
    assert zone(result, "Z4")["equivalent_minutes_range"] == pytest.approx([1.75, 3.])
    assert zone(result, "Z4")["baseline_equivalent_minutes_range"] == pytest.approx([1.9, 3.6])


def test_undeclared_varying_axes_do_not_become_a_fabricated_range():
    result = comparison.session_projection({"blocks": [work(
        target_speed_range_kmh=[18, 19], duration_range_seconds=[1200, 1800])]}, parameters())
    assert result["blocks"][0]["range_reason"] == "RANGE_DEPENDENCY_NOT_DECLARED"
    assert zone(result, "Z4")["equivalent_minutes_range"] is None


def test_legacy_target_and_missing_speed_execution_do_not_look_completed():
    params = parameters()
    days = [{"date": "2026-10-20", "sessions": [{"sport": "Run", "blocks": [work()]}]}]
    result = comparison.build(days, {"Run": {"comparison_parameters": params}},
        source={"activities": [{"activity_ref": "missing-speed", "sport": "Run", "date": "2026-10-20"}]},
        start_date="2026-10-20", end_date="2026-10-26")
    sport = result["sports"][0]
    target = next(row for row in sport["targets"] if row["zone"] == "Z4")
    assert sport["actual"]["status"] == "INCOMPLETE"
    assert target["actual_equivalent_minutes"] is None
    assert target["remaining_equivalent_minutes"] is None
    assert target["target_equivalent_minutes"] is None
    assert target["reason"] == "LEGACY_HR_TARGET_NOT_CONVERTED"


@pytest.mark.parametrize("source", [None, {}, {"activities": None}, {"activities": "unavailable"}, {"activities": {}}, {"activities": [None]}])
def test_missing_execution_source_is_not_a_verified_empty_history(source):
    params = parameters()
    days = [{"date": "2026-10-20", "sessions": [{"sport": "Run", "blocks": [work()]}]}]
    result = comparison.build(days, {"Run": {"comparison_parameters": params}}, source=source)
    assert result["sports"][0]["actual"]["reason"] == "EXECUTION_SOURCE_UNAVAILABLE"
    assert all(row["actual_equivalent_minutes"] is None for row in result["sports"][0]["targets"])


def test_same_metric_execution_and_target_reconcile_both_settings():
    params = parameters()
    actual = speed_equivalence.integrate(params, [{"seconds": 1800, "speed_kmh": 19}])
    days = [{"date": "2026-10-21", "sessions": [{"sport": "Run", "blocks": [work()]}]}]
    result = comparison.build(days, {"Run": {"comparison_parameters": params}},
        source={"activities": [{"activity_ref": "run", "sport": "Run", "date": "2026-10-20",
                                "speed_equivalence_comparison": actual}]},
        start_date="2026-10-20", end_date="2026-10-26",
        speed_targets_by_sport={"Run": {"Z4": {"parameters_version": params["parameters_version"],
            "unit": "EQUIVALENT_MINUTES_AT_ZONE_REFERENCE", "equivalent_minutes": 60}}})
    z4 = next(row for row in result["sports"][0]["targets"] if row["zone"] == "Z4")
    assert z4["actual_equivalent_minutes"] == pytest.approx(26.25)
    assert z4["planned_equivalent_minutes"] == pytest.approx(26.25)
    assert z4["remaining_equivalent_minutes"] == pytest.approx(7.5)
    assert z4["baseline_remaining_equivalent_minutes"] == pytest.approx(3)
    assert z4["target_attainment_percent"] == pytest.approx(87.5)
    assert z4["baseline_target_attainment_percent"] == pytest.approx(95)


def test_changed_reference_version_cannot_reuse_execution():
    params = parameters()
    actual = speed_equivalence.integrate(params, [{"seconds": 10, "speed_kmh": 24}])
    actual["parameters_version"] = "old-reference"
    result = comparison.build([], {"Run": {"comparison_parameters": params}},
        source={"activities": [{"activity_ref": "run", "sport": "Run", "date": "2026-10-20",
                                "speed_equivalence_comparison": actual}]})
    assert result["sports"][0]["actual"]["different_version_count"] == 1
    assert result["sports"][0]["actual"]["comparison"] is None


@pytest.mark.parametrize("missing_in", ["plan", "actual"])
def test_partial_speed_coverage_never_claims_target_attainment(missing_in):
    params = parameters()
    actual_segments = [{"seconds": 1800, "speed_kmh": 19}]
    blocks = [work()]
    if missing_in == "actual":
        actual_segments.append({"seconds": 600, "speed_kmh": None})
    else:
        blocks.append({"kind": "WARMUP", "duration_min": 10, "zone": "Z1"})
    actual = speed_equivalence.integrate(params, actual_segments)
    result = comparison.build([{"date": "2026-10-21", "sessions": [{"sport": "Run", "blocks": blocks}]}],
        {"Run": {"comparison_parameters": params}},
        source={"activities": [{"activity_ref": "run", "sport": "Run", "date": "2026-10-20",
                                "speed_equivalence_comparison": actual}]},
        speed_targets_by_sport={"Run": {"Z4": {"parameters_version": params["parameters_version"],
            "unit": "EQUIVALENT_MINUTES_AT_ZONE_REFERENCE", "equivalent_minutes": 60}}})
    z4 = next(row for row in result["sports"][0]["targets"] if row["zone"] == "Z4")
    assert z4["remaining_equivalent_minutes"] is None
    assert z4["target_attainment_percent"] is None
    assert z4["baseline_remaining_equivalent_minutes"] is None
    assert z4["baseline_target_attainment_percent"] is None
    assert "COVERAGE" in z4["reason"]


def test_zone_crossing_is_counted_once_per_selected_prescription():
    result = comparison.session_projection({"blocks": [work(seconds=10, speed=20)]}, parameters())
    assert zone(result, "Z4")["real_seconds"] == 0
    assert zone(result, "Z5")["real_seconds"] == 10
    assert zone(result, "Z5")["equivalent_minutes"] == pytest.approx(1/6)


@pytest.mark.parametrize("source", [None, {"activities": []}])
def test_other_sports_never_receive_running_weights(source):
    result = comparison.build([{"date": "2026-10-20", "sessions": [
        {"sport": "NordicSki", "blocks": [work()]}]}], {}, source=source)
    planned = result["sports"][0]["planned"]
    assert planned["status"] == "UNAVAILABLE"
    assert "RUN_ONLY" in planned["warnings"]
    assert result["sports"][0]["actual"]["reason"] == "RUN_ONLY"
    assert all(row["actual_equivalent_minutes"] is None for row in result["sports"][0]["targets"])
