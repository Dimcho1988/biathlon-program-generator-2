from copy import deepcopy

import pytest

from apps.api.load_metadata import selection_metadata, with_load_metadata
from apps.api.schemas import ModelMetadata
from biathlon import speed_equivalence, unified_load
from tests.test_unified_zone_load import BOUNDS, profile, streams


def mixed_stream():
    shadow = streams()
    for row in shadow["timeseries"][:20]:
        row["hrmod_final_bpm"] = 170.
    for row in [*shadow["timeseries"][20:30], *shadow["timeseries"][50:60]]:
        row["hr_raw_bpm"] = None
    for row in shadow["speed_test_series"][40:]:
        row.update(load_status="UNAVAILABLE", load_reason="NEGATIVE_DESCENT_RESIDUAL")
    return shadow


@pytest.mark.parametrize("leading,expected_hr,expected_speed", [("HEART_RATE", 40., 10.), ("SPEED", 10., 40.)])
def test_source_coverage_is_a_partition_and_rejected_speed_can_have_hr_load(leading, expected_hr, expected_speed):
    shadow = mixed_stream()
    original = deepcopy(shadow)
    ledger = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", leading_indicator=leading,
        parameters=speed_equivalence.prepare(profile()), duration_s=60.)
    assert ledger["recorded_seconds"] == 60.
    assert ledger["active_seconds"]+ledger["rest_seconds"]+ledger["unknown_seconds"] == pytest.approx(60.)
    assert ledger["source_seconds"] == {"HEART_RATE": expected_hr, "SPEED": expected_speed}
    assert sum(ledger["hr_source_seconds"].values()) == expected_hr
    assert ledger["speed_status_seconds"] == {"ACTIVE": 40., "REST": 0., "UNAVAILABLE": 20.}
    assert ledger["speed_unavailable_reasons_seconds"] == {"NEGATIVE_DESCENT_RESIDUAL": 20.}
    assert ledger["unavailable_speed_hr_seconds"] == 10.
    assert ledger["unknown_seconds"] == ledger["fallback_seconds"] == 10.
    if leading == "HEART_RATE":
        assert ledger["hr_source_seconds"] == {"HRMOD": 20., "RAW_HR": 20.}
    assert shadow == original


def test_mixed_hr_interpolation_is_not_falsely_labelled_pure_hrmod():
    shadow = {"timeseries": [
        {"elapsed_s": 0., "hr_raw_bpm": 160., "hrmod_final_bpm": 170.,
         "hr_interval_classification": "smart_recording", "hr_interval_end_s": 5.},
        {"elapsed_s": 5., "hr_raw_bpm": 165.}], "speed_test_series": []}
    ledger = unified_load.integrate(shadow, bounds=BOUNDS, sport="Run", duration_s=5.,
        parameters=speed_equivalence.prepare(profile()))
    assert ledger["hr_source_seconds"] == {"MIXED_HR_INTERPOLATION": 5.}
    assert ledger["source_seconds"]["HEART_RATE"] == 5.


def test_known_low_speed_descent_is_rest_and_is_not_missing_time():
    shadow = streams(None, 1.)
    for row in shadow["speed_test_series"]:
        row.update(load_status="ACTIVE", load_speed_kmh=1., load_source="DESCENT_BALANCE")
    ledger = unified_load.integrate(shadow, bounds=BOUNDS, sport="NordicSki", duration_s=60.,
        leading_indicator="SPEED", parameters=speed_equivalence.prepare(profile("NordicSki"), sport="NordicSki"))
    assert ledger["speed_status_seconds"] == {"ACTIVE": 0., "REST": 60., "UNAVAILABLE": 0.}
    assert ledger["estimated_descent_seconds"] == ledger["rest_seconds"] == 60.
    assert ledger["unknown_seconds"] == ledger["fallback_seconds"] == 0.
    assert not ledger["hr_source_seconds"]
    assert sum(z["equivalent_time_min"] for z in ledger["zones"]) == 0.


def test_metadata_retains_partial_coverage_and_does_not_relabel_the_raw_adapter():
    ledger = unified_load.integrate(mixed_stream(), bounds=BOUNDS, sport="Run", duration_s=60.,
        parameters=speed_equivalence.prepare(profile()))
    activities = [{"sport": "Run", "load_source": ledger}, {"sport": "NordicSki"},
                  {"sport": "WeightTraining", "strength_time_min": 60.}]
    model = {"algorithm_version": "test", "effective_hr_version": "raw-adapter", "effective_hr_source": "raw_hr", "parameter_version": 1}
    original = deepcopy((model, activities))
    result = with_load_metadata(model, activities)
    assert result["effective_hr_source"] == "unified_interval_selection"
    assert result["load_selection"]["activity_count"] == 1
    assert result["load_selection"]["total_aerobic_activities"] == 2
    assert result["load_selection"]["hr_source_seconds"] == {"HRMOD": 20., "RAW_HR": 20.}
    assert ModelMetadata(**result).model_dump()["load_selection"] == result["load_selection"]
    assert (model, activities) == original
    assert with_load_metadata(model, [{"sport": "Run"}]) is model
    assert "load_selection" not in ModelMetadata(**model).model_dump()


def test_old_ledgers_do_not_invent_a_zero_hrmod_or_speed_breakdown():
    ledger = unified_load.integrate(streams(), bounds=BOUNDS, sport="Run", duration_s=60.,
        parameters=speed_equivalence.prepare(profile()))
    for key in ("hr_source_seconds", "speed_status_seconds", "speed_unavailable_reasons_seconds",
                "unavailable_speed_hr_seconds", "estimated_descent_seconds"):
        ledger.pop(key)
    result = selection_metadata([{"sport": "Run", "load_source": ledger}])
    assert result["source_seconds"] == {"HEART_RATE": 60., "SPEED": 0.}
    assert result["hr_source_seconds"] is None
    assert result["speed_status_seconds"] is None
    assert result["unavailable_speed_hr_seconds"] is None


def test_read_projection_updates_stale_metadata_even_when_q_and_e_are_already_current(monkeypatch):
    from types import SimpleNamespace
    from apps.api import component_load_projection as projection
    ledger = unified_load.integrate(streams(), bounds=BOUNDS, sport="Run", duration_s=60.,
        parameters=speed_equivalence.prepare(profile()))
    history = {"activities": [{"activity_ref": "test", "date": "2026-10-25", "sport": "Run",
        "load_source": ledger, "zones": ledger["zones"]}], "zones": []}
    model = {"algorithm_version": "test", "effective_hr_version": "raw-adapter", "effective_hr_source": "raw_hr", "parameter_version": 1}
    snapshot = {"load_history": history, "training_status": {"model": model, "zones": []}}
    before = deepcopy(snapshot)
    monkeypatch.setattr(projection, "project_history", lambda source, contexts: source)
    result = projection.project_snapshot(SimpleNamespace(), "test", snapshot, contexts_by_sport={})
    assert result["training_status"]["model"]["effective_hr_source"] == "unified_interval_selection"
    assert result["load_history"] == history
    assert snapshot == before


def test_limited_planning_explains_unknown_load_without_changing_its_gate():
    from datetime import date
    from apps.api.planning_history_estimate import prepare
    ledger = unified_load.integrate(mixed_stream(), bounds=BOUNDS, sport="Run", duration_s=60.,
        parameters=speed_equivalence.prepare(profile()))
    source = {"period_start": "2026-10-24", "period_end": "2026-10-24", "load_selection": {"version": unified_load.VERSION},
        "activities": [{"activity_ref": "test", "date": "2026-10-24", "sport": "Run", "quality_status": "limited", "load_source": ledger}],
        "daily": [], "quality": {"excluded_activities": 0}}
    before = deepcopy(source)
    _, evidence = prepare(source, [], {}, date(2026, 10, 25))
    assert evidence["supported"] is False
    assert evidence["estimated"] is False
    assert evidence["unresolved_activity_count"] == 1
    assert evidence["load_coverage"]["recorded_seconds"] == 60.
    assert evidence["load_coverage"]["unknown_seconds"] == 10.
    assert source == before
