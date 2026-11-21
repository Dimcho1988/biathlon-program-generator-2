from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from apps.api import learning_evidence, learning_methods, learning_service, load_adaptation, response_service
from apps.api.response_monitoring import OptionalTest
from biathlon.learning_provenance import eligible_load_rows, heuristic_load_dates
from biathlon.load_progression import observed_window
from tests.api.test_learning_evidence import START, TODAY, fixture
from tests.api.test_learning_methods import comparison, assess, observation
from tests.api.test_load_progression import response_fixture, projected_response_source, NOW
from tests.api.test_response_monitoring import Repository, ACTOR


@pytest.mark.parametrize("provenance", [
    {"initial_heuristic": True}, {"calibration_source": "INITIAL_HEURISTIC"},
])
def test_only_explicit_heuristic_dates_are_excluded_from_learning_without_changing_ledger(provenance):
    data = fixture()
    day = str(START)
    activity = next(a for a in data["source"]["activities"] if a["date"] == day)
    activity["load_source"] = {**provenance, "calibration_eligible": False}
    original = deepcopy(data)
    assert heuristic_load_dates(data["source"]) == {day}
    rows = eligible_load_rows(data["source"], data["rows"])
    assert len(rows) == len(data["rows"])-6
    assert all(r["date"] != day for r in rows)
    result = learning_evidence.build_evidence(**data)
    assert result["episodes"] == []
    assert any(r["reason"] == "INITIAL_HEURISTIC_NOT_LEARNING_EVIDENCE" for r in result["exclusions"])
    assert result["current"]["latest_day"] is not None
    assert data == original


def test_generic_calibration_flag_does_not_discard_existing_observed_learning():
    data = fixture()
    for activity in data["source"]["activities"]:
        activity["load_source"] = {"calibration_eligible": False, "initial_heuristic": False}
    assert eligible_load_rows(data["source"], data["rows"]) == data["rows"]
    assert len(learning_evidence.build_evidence(**data)["episodes"]) == 1


def test_estimated_dose_remains_complete_for_planning_without_becoming_measured_reference():
    data = fixture()
    for activity in data["source"]["activities"]:
        activity.update(duration_min=50., quality_status="valid",
                        load_source={"initial_heuristic": True})
    original = deepcopy(data)
    window = observed_window(data["source"], data["rows"], START, TODAY)
    z3 = window["components"]["Z3"]
    assert window["complete"] is True
    assert z3["weekly_q"] > 0 and z3["weekly_minutes"] > 0
    assert z3["measured_weekly_q"] == 0
    assert z3["estimated_weekly_q"] == z3["weekly_q"]
    assert z3["reference_supported"] is False
    assert data == original


def test_fresh_adaptation_has_no_measured_dose_when_a_window_uses_heuristic_load():
    entries, rows = response_fixture()
    source = projected_response_source(rows)
    source["activities"][0]["load_source"] = {"initial_heuristic": True}
    assert load_adaptation.assess(entries, NOW.date(), rows=rows)["components"]
    assert not load_adaptation.assess(entries, NOW.date(),
        rows=eligible_load_rows(source, rows))["components"]


def test_actual_outcome_is_saved_without_freezing_heuristic_load_as_measured_truth(monkeypatch):
    from apps.api import component_load_projection
    entries, rows = response_fixture()
    source = projected_response_source(rows)
    source["activities"][0]["load_source"] = {"initial_heuristic": True}
    original = deepcopy(source)
    repo = Repository(entries[:-1])
    repo.active_activity_calendar = lambda *args: {
        "generation_id": "observed", "revision": 3,
        "snapshot_payload": {"load_history": source}}
    monkeypatch.setattr(component_load_projection, "project_snapshot", lambda *args: {"load_history": source})
    body = OptionalTest(**entries[-1]["payload"])
    response_service.save_report(repo, "ath-test", "TEST", body, ACTOR, now=NOW)
    saved = repo.saved["p_payload"]
    assert saved["value"] == body.value
    assert saved["observed_load_windows"] == []
    assert saved["load_observation_status"] == "UNAVAILABLE"
    assert source == original


@pytest.mark.parametrize("boundary,excluded", [
    ("before", False), ("previous_first", True), ("load_first", True),
    ("recovery_last", True), ("after", False),
])
def test_frozen_windows_retract_only_when_heuristic_dates_overlap_either_window(boundary, excluded):
    entries, rows = response_fixture()
    source = projected_response_source(rows)
    windows = load_adaptation.load_observations(entries, rows, NOW.date())
    window = windows[0]
    start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["recovery_end"])
    first = start-timedelta(days=window["days_per_window"])
    day = {"before": first-timedelta(days=1), "previous_first": first,
           "load_first": start, "recovery_last": end, "after": end+timedelta(days=1)}[boundary]
    source["activities"].append({"date": day.isoformat(), "sport": "Run",
        "load_source": {"initial_heuristic": True}})
    entries[-1]["payload"].update(observed_load_windows=windows,
        load_source={"component_load_context_key": learning_service.component_load_context_key(source)})
    original = deepcopy(entries)
    filtered = learning_service.compatible_load_observations(entries, source)
    assert filtered[-1]["payload"]["observed_load_windows"] == ([] if excluded else windows)
    assert filtered[-1]["payload"]["value"] == entries[-1]["payload"]["value"]
    assert entries == original


def test_raw_and_flat_equivalent_speed_doses_do_not_pool_method_preferences():
    pairs = comparison()
    for i, (entry, activity) in enumerate(pairs):
        entry["payload"].update(planned_speed_kmh=12., executed_speed_kmh=12. if i < 16 else 9.)
        activity["load_source"] = {"initial_heuristic": i < 16,
            "speed_role": "RAW_MOVING_SPEED" if i < 16 else "FLAT_EQUIVALENT"}
    result = assess(pairs)
    assert result["summary"]["observations"] == 32
    assert all(p["score_delta"] == 0 for p in result["preferences"])
    assert {row["speed_role"] for row in result["memory"]["observations"]} == {"RAW_MOVING_SPEED", "FLAT_EQUIVALENT"}


def test_heuristic_prescription_does_not_erase_original_subjective_execution_feedback():
    pair = observation(1)
    pair[1]["load_source"] = {"initial_heuristic": True, "speed_role": "RAW_MOVING_SPEED"}
    result = assess([pair])
    assert result["summary"]["observations"] == 1
    assert result["memory"]["observations"][0]["success"] is True


def test_frozen_execution_speed_frame_survives_a_later_activity_recalculation():
    pair = observation(1, planned_speed_kmh=12., executed_speed_kmh=12.)
    pair[0]["payload"]["executed_method"]["speed_role"] = "RAW_MOVING_SPEED"
    pair[1]["load_source"] = {"initial_heuristic": False, "speed_role": "FLAT_EQUIVALENT"}
    result = assess([pair])
    assert result["memory"]["observations"][0]["speed_role"] == "RAW_MOVING_SPEED"


def test_server_descriptor_freezes_speed_frame_without_confusing_equivalent_labels():
    from tests.api.test_learning_methods import METHODS, A
    method = {**METHODS[A], "speed_role": "FLAT_EQUIVALENT_REFERENCE_NOT_TERRAIN_PACE"}
    assert learning_methods.method_descriptor(method, "Run")["speed_role"] == "FLAT_EQUIVALENT"


@pytest.mark.parametrize("catalog_only", [False, True])
def test_confirming_actual_execution_freezes_its_recorded_speed_provenance(monkeypatch, catalog_only):
    from tests.api.test_learning_methods import METHODS, A
    monkeypatch.setattr(learning_methods, "_methods", lambda *args, **kwargs: [METHODS[A]])
    provenance = {"speed_role": "RAW_MOVING_SPEED", "initial_heuristic": True,
                  "calibration_source": "INITIAL_HEURISTIC"}
    metadata = ({"canonical_summary": {"intensity_provenance": provenance}} if catalog_only
                else {"load_source": provenance})
    result = learning_methods.confirmed_descriptor(object(), "athlete",
        SimpleNamespace(method_confirmed=True, executed_method_id=A),
        {"sport": "Run", "local_date": START.isoformat(), **metadata})
    assert {key: result[key] for key in provenance} == provenance
