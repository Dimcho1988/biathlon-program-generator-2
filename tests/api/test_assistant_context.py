"""Authorization and provenance gates before any athlete data reaches an LLM."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from apps.api import assistant_context as context
from apps.api.oauth_store import PersistentStoreFailure

NOW = datetime(2026, 11, 10, 23, 30, tzinfo=timezone.utc)
ALIAS = "athlete-test"
FULL = {"is_owner": True, "can_view_plan": True, "can_view_recovery": True, "can_edit_plan": True}
PLAN = {**FULL, "is_owner": False, "can_view_recovery": False, "can_edit_plan": False}


def session(number):
    return {"method_id": f"method-{number}", "title": f"Сесия {number}", "sport": "Run", "zone": "Z4", "total_minutes": 20 + number,
        "blocks": [{"kind": "WORK", "label": "Отсечка", "duration_min": number * 3, "target_speed_kmh": 20,
                    "instructions": "Запази равномерна скорост.", "private_response": "hidden"}],
        "dose_evidence": {"explanation": "Замразен разчет от вчера", "limits": [{"code": "RECOVERY", "limit_minutes": 12}],
                          "capacity_minutes": 15, "model_version": "canonical-1", "response_memory": "hidden",
                          "individual_learning": {"memory": {"labs": "hidden"}}},
        "method_library": {"id": f"method-{number}", "guidance": {"execution": "Равномерно изпълнение."},
            "source": {"authors": "Author", "reference": "Study"}, "original": {"work": "Original"},
            "adaptation": {"notes": "onFlows adaptation"}, "provenance": {"status": "SOURCE_REVIEW_REQUIRED"},
            "prescription": None}}


def plan():
    return {"revision": 7, "recorded_at": "2026-11-10T12:00:00+00:00", "payload": {"status": "REVIEW_REQUIRED",
        "plan": {"engine_version": "plan-v1", "input_snapshot": {"generation_id": "old-generation", "analysis_revision": 3,
            "response_revisions": [{"kind": "LAB", "entry_key": "hidden"}]},
            "days": [{"date": "2026-11-11", "status": "TRAINING", "explanation": "private detail", "sessions": [session(1), session(2)]}]},
        "proposal": {"days": [{"date": "2026-11-11", "sessions": [{"title": "Неприето предложение"}]}]}}}


def snapshot():
    return {"generation_id": "new-generation", "revision": 4, "analysis_as_of": "2026-11-10",
        "snapshot_payload": {"training_status": {"athlete_id": ALIAS, "as_of": "2026-11-10", "model": {"algorithm_version": "load-v1"},
            "zones": [{"zone": "Z1", "status_7_40": 1.2, "recovery_readiness_percent": 93, "recovery_days_to_full": 0.5}]},
            "recovery_history": {"as_of": "2026-11-11", "data_quality": {"complete": False, "missing_days": 2},
                "current": [{"zone": "Z1", "readiness_percent": 99, "days_to_practical_recovery": 0}],
                "forecast": [{"zone": "Z1", "readiness_percent": 100}], "daily": [{"readiness_before_percent": 85}]},
            "load_history": {"athlete_id": ALIAS, "period_start": "2026-11-01", "period_end": "2026-11-10", "daily": [
                {"date": "2026-11-09", "zone": "Z1", "effective_load": 40, "equivalent_time_min": 55},
                {"date": "2026-11-10", "zone": "Z1", "effective_load": 20, "equivalent_time_min": 25}],
                "strength": {"daily": []}, "activities": [{"date": "2026-11-09", "activity_ref": "act_123", "duration_min": 60, "note": "hidden"}]}}}


@pytest.fixture
def repository(monkeypatch):
    repo = SimpleNamespace(athlete_settings=Mock(return_value=SimpleNamespace(timezone="Europe/Sofia")),
        active_analysis=Mock(side_effect=lambda _: deepcopy(snapshot())), _request=Mock(), _json=lambda value: value)
    repo.plan_read = Mock(side_effect=lambda _: deepcopy(plan()))
    monkeypatch.setattr(context, "PlanStore", lambda _: SimpleNamespace(current=repo.plan_read))
    return repo


def test_two_exact_sessions_are_read_from_approved_revision_in_athlete_timezone(repository):
    result = context.read(repository, ALIAS, FULL, {"kind": "day"}, now=NOW)
    assert result["today"] == result["date"] == "2026-11-11"
    assert result["timezone"] == "Europe/Sofia" and result["plan_revision"] == 7
    assert [s["total_minutes"] for s in result["sessions"]] == [21, 22]
    assert [s["session_id"] for s in result["sessions"]] == ["2026-11-11:1", "2026-11-11:2"]
    assert result["proposal_available"] and result["prescription_active"]
    assert "Неприето предложение" not in json.dumps(result, ensure_ascii=False)
    repository.active_analysis.assert_not_called()
    repository._request.assert_not_called()


def test_plan_only_projection_removes_dose_and_private_embedded_sources(repository):
    result = context.read(repository, ALIAS, PLAN, {"kind": "day"}, now=NOW)
    text = json.dumps(result)
    assert "private" not in text and "response_revisions" not in text and '"LAB"' not in text
    assert "capacity_minutes" not in text and "limit_minutes" not in text and "individual_learning" not in text
    assert result["sessions"][0]["blocks"][0]["target_speed_kmh"] == 20
    assert result["sessions"][0]["dose_evidence"] == {"availability": "UNAVAILABLE", "reason": "RECOVERY_ACCESS_REQUIRED"}


def test_saved_dose_evidence_keeps_historical_reason_without_using_new_snapshot(repository):
    result = context.read(repository, ALIAS, FULL, {"kind": "day"}, now=NOW)
    assert result["input_snapshot"]["generation_id"] == "old-generation"
    evidence = result["sessions"][0]["dose_evidence"]
    assert evidence["explanation"] == "Замразен разчет от вчера" and evidence["limits"][0]["code"] == "RECOVERY"
    assert "response_memory" not in evidence and "individual_learning" not in evidence
    repository.active_analysis.assert_not_called()


@pytest.mark.parametrize("query", [{"kind": "status"}, {"kind": "history"}, {"kind": "inputs", "input_kind": "LAB", "key": "lab"}])
def test_plan_only_access_is_rejected_before_loading_private_records(repository, query):
    with pytest.raises(HTTPException) as error:
        context.read(repository, ALIAS, PLAN, query, now=NOW)
    assert error.value.status_code == 403
    repository.athlete_settings.assert_not_called()
    repository.active_analysis.assert_not_called()
    repository._request.assert_not_called()


def test_no_approved_plan_never_invents_a_workout(repository):
    repository.plan_read.side_effect = lambda _: None
    result = context.read(repository, ALIAS, FULL, {"kind": "day"}, now=NOW)
    assert result["availability"] == "UNAVAILABLE" and result["reason"] == "NO_APPROVED_PLAN" and result["sessions"] == []


def test_missing_date_does_not_mean_rest(repository):
    result = context.read(repository, ALIAS, FULL, {"kind": "day", "date": "2026-11-12"}, now=NOW)
    assert result["reason"] == "DATE_NOT_IN_APPROVED_PLAN" and result["sessions"] == []
    assert result.get("day_status") != "REST"


def test_status_sanitizes_all_duplicate_incomplete_readiness_numbers(repository):
    result = context.read(repository, ALIAS, FULL, {"kind": "status"}, now=NOW)
    text = json.dumps(result)
    assert result["recovery"]["availability"] == "UNAVAILABLE"
    for forbidden in ("readiness_percent", "recovery_readiness_percent", "recovery_days_to_full", "forecast", "readiness_before_percent"):
        assert forbidden not in text
    assert result["zones"][0]["status_7_40"] == 1.2
    assert result["freshness"] == "STALE"


def estimated_quality():
    return {"complete": False, "readiness_available": True, "readiness_basis": "ESTIMATED_LOAD", "estimated": True,
        "missing_days": 0, "estimation": {"supported": True, "unresolved_activity_count": 0, "missing_calendar_days": []}}


def test_admissible_estimate_is_labelled_modelled_and_never_measured():
    result = context.present_recovery({"data_quality": estimated_quality(), "current": [{"zone": "Z1", "readiness_percent": 92}]})
    assert result["availability"] == "ESTIMATED" and result["estimated"] and result["is_measurement"] is False
    assert result["current"][0]["readiness_percent"] == 92


@pytest.mark.parametrize("change", [{"missing_days": 1}, {"readiness_available": False}, {"estimated": False},
    {"estimation": {"supported": False, "unresolved_activity_count": 0, "missing_calendar_days": []}},
    {"estimation": {"supported": True, "unresolved_activity_count": 1, "missing_calendar_days": []}},
    {"estimation": {"supported": True, "unresolved_activity_count": 0, "missing_calendar_days": ["2026-11-01"]}}])
def test_unsupported_estimation_never_exposes_numerical_recovery(change):
    result = context.present_recovery({"data_quality": {**estimated_quality(), **change}, "current": [{"zone": "Z1", "readiness_percent": 92}]})
    assert result["availability"] == "UNAVAILABLE" and "readiness_percent" not in json.dumps(result)


def test_absent_quality_does_not_promote_legacy_raw_number():
    assert context.present_recovery({"current": [{"readiness_percent": 92}]})["reason"] == "QUALITY_NOT_AVAILABLE"


def test_cold_prepared_status_does_not_fall_back_to_model_or_stale_analysis(repository):
    repository.dashboard_preparation = Mock(return_value={"ready": False})
    result = context.read(repository, ALIAS, FULL, {"kind": "status"}, now=NOW)
    assert result["availability"] == "PENDING"
    repository.active_analysis.assert_not_called()
    assert repository.dashboard_preparation.call_args.args == ("READ",)


def test_wrong_athlete_prepared_cache_is_rejected(repository):
    wrong = snapshot()
    wrong["snapshot_payload"]["training_status"]["athlete_id"] = "another-athlete"
    repository.dashboard_preparation = Mock(return_value={"ready": True, "envelope": wrong})
    with pytest.raises(PersistentStoreFailure):
        context.read(repository, ALIAS, FULL, {"kind": "status"}, now=NOW)


def test_history_marks_missing_days_without_filling_rest_or_zero(repository):
    result = context.read(repository, ALIAS, FULL, {"kind": "history", "start": "2026-11-08", "end": "2026-11-10"}, now=NOW)
    assert result["unobserved_days"] == ["2026-11-08"] and result["missing_day_means_rest"] is False
    assert len(result["daily"]) == 2 and result["units"]["effective_load"] == "E_EFFECTIVE_LOAD"
    assert "note" not in result["activities"][0]


def test_history_keeps_canonical_quality_and_marks_heuristic_daily_load(repository):
    data = snapshot()
    source = data["snapshot_payload"]["load_history"]
    source["activities"] = [
        {"date": "2026-11-09", "activity_ref": "act_estimated", "quality_status": "limited", "hr_coverage_percent": 65,
         "load_source": {"initial_heuristic": False, "calibration_source": "INITIAL_HEURISTIC", "speed_role": "RAW_SPEED",
                         "calibration_eligible": False, "note": "private omitted detail"}},
        {"date": "2026-11-10", "activity_ref": "act_recorded", "quality_status": "valid", "hr_coverage_percent": 100,
         "load_source": {"initial_heuristic": False, "calibration_source": "MAXIMAL_TEST"}},
        {"date": "2026-11-07", "activity_ref": "act_outside", "load_source": {"initial_heuristic": True}},
    ]
    repository.active_analysis.side_effect = lambda _: deepcopy(data)
    result = context.read(repository, ALIAS, FULL, {"kind": "history", "start": "2026-11-08", "end": "2026-11-10"}, now=NOW)
    assert result["estimated_days"] == ["2026-11-09"]
    assert result["daily"][0]["estimated"] is True
    assert result["daily"][0]["estimation_basis"] == "INITIAL_HEURISTIC"
    assert result["daily"][0]["effective_load"] == 40
    assert "estimated" not in result["daily"][1]
    assert result["activities"][0]["quality_status"] == "limited"
    assert result["activities"][0]["hr_coverage_percent"] == 65
    assert result["activities"][0]["load_source"] == {
        "initial_heuristic": False, "calibration_source": "INITIAL_HEURISTIC", "speed_role": "RAW_SPEED", "calibration_eligible": False}
    assert "private omitted detail" not in json.dumps(result)
    assert source["daily"][0].get("estimated") is None


def test_selected_method_uses_frozen_card_and_keeps_uncertain_source_status(repository, monkeypatch):
    monkeypatch.setattr(context.method_library, "entries", Mock(side_effect=AssertionError("No fresh catalog substitution")))
    result = context.read(repository, ALIAS, FULL, {"kind": "method", "session_id": "2026-11-11:2"}, now=NOW)
    assert len(result["sessions"]) == 1 and result["method_source"] == "FROZEN_SESSION_CARD"
    assert result["sessions"][0]["method_library"]["source_status"] == "SOURCE_REVIEW_REQUIRED"


def test_reference_only_catalog_card_is_not_an_individual_prescription(repository, monkeypatch):
    monkeypatch.setattr(context.method_library, "entries", lambda: [{"id": "reference", "prescription": None, "guidance": {"execution": "Reference only"}}])
    result = context.read(repository, ALIAS, PLAN, {"kind": "method", "method_id": "reference"}, now=NOW)
    assert result["method_source"] == "CURRENT_CATALOG_REFERENCE" and result["is_individual_prescription"] is False
    assert result["method"]["is_executable_recipe"] is False


def test_inputs_are_latest_bounded_and_exclude_calculated_fields(repository):
    repository._request.return_value = [{"kind": "DAILY", "entry_key": "2026-11-11", "revision": 3,
        "payload": {"day": "2026-11-11", "sleep_quality": 2, "fatigue": 4, "note": "Existing note", "source": "ONFLOWS", "baseline": {"computed": 10}}}]
    result = context.read(repository, ALIAS, FULL, {"kind": "inputs", "input_kind": "DAILY"}, now=NOW)
    assert result["revision"] == 3 and result["payload"]["note"] == "Existing note"
    assert "source" not in result["payload"] and "baseline" not in result["payload"]
    args = repository._request.call_args.args
    assert args[0] == "GET" and "entry_key=eq.2026-11-11" in args[1] and "order=revision.desc&limit=1" in args[1]


def test_manual_speed_input_retains_original_distance_choice(repository):
    key = "manual_" + "a" * 32
    repository._request.return_value = [{"kind": "SPEED_TEST", "entry_key": key, "revision": 2, "payload": {
        "source": "MANUAL", "measurement_input": "DISTANCE", "duration_s": 180, "distance_m": 1000,
        "speed_kmh": 20, "maximal": True, "comparable": True, "flat_terrain": True}}]
    result = context.read(repository, ALIAS, FULL, {"kind": "inputs", "input_kind": "MANUAL_SPEED_TEST", "key": key}, now=NOW)
    assert result["payload"]["distance_m"] == 1000 and result["payload"]["speed_kmh"] is None
    assert "onflows_model_entries" in repository._request.call_args.args[1]


@pytest.mark.parametrize("query", [{"kind": "SQL"}, {"kind": "day", "athlete_id": "someone-else"},
    {"kind": "day", "date": "2026-02-30"}, {"kind": "history", "start": "2026-08-01", "end": "2026-11-11"}])
def test_unregistered_queries_or_scope_overrides_are_rejected(repository, query):
    with pytest.raises(HTTPException) as error:
        context.read(repository, ALIAS, FULL, query, now=NOW)
    assert error.value.status_code == 422


@pytest.mark.parametrize("kind", ["DAILY", "WEIGHT", "SESSION", "TEST", "LAB", "BLOCK", "MANUAL_SPEED_TEST", "CALENDAR_EVENT", "MANAGEMENT_PROFILE"])
def test_selected_input_contract_never_exposes_expert_model_settings(kind):
    contract = context.input_contract(kind)
    assert "patch_rule" in contract
    server_owned = {"expected_revision", "event_id", "test_id", "sample_id", "input_method"}
    assert not server_owned.intersection(contract.get("properties", {}))
    assert not server_owned.intersection(contract.get("required_for_new_record", []))
    assert "duration_coefficient" not in json.dumps(contract)


def test_daily_labels_match_the_existing_form_contract():
    from pathlib import Path
    form = Path("apps/web/lib/response-monitoring.ts").read_text()
    import re
    for field, labels in context.DAILY_SCALES.items():
        matched = re.search(rf"{field}:(\[[^\]]+\])", form)
        assert matched and json.loads(matched.group(1)) == labels


@pytest.mark.parametrize("kind", ["TEST", "LAB", "BLOCK", "MANUAL_SPEED_TEST"])
def test_new_record_context_has_zero_revision_without_reading_unrelated_history(repository, kind):
    result = context.read(repository, ALIAS, FULL, {"kind": "inputs", "input_kind": kind}, now=NOW)
    assert result["creating"] and result["revision"] == 0 and result["entry_key"] == ""
    assert result["input_kind"] == kind and result["input_contract"]["required_for_new_record"]
    repository._request.assert_not_called()


def test_calendar_reads_exact_event_and_whole_calendar_revision(repository):
    event = {"event_id": "race-one", "event_type": "MAIN_RACE", "name": "Основен старт", "start_date": "2026-12-12", "end_date": "2026-12-12"}
    repository._request.return_value = [{"planning_calendar": {"schema_version": "planning-calendar-v1", "events": [event]}, "calendar_revision": 9}]
    result = context.read(repository, ALIAS, PLAN, {"kind": "inputs", "input_kind": "CALENDAR_EVENT", "key": "race-one"}, now=NOW)
    assert result["revision"] == result["calendar_revision"] == 9
    assert result["payload"] == event and result["entry_key"] == "race-one"
    assert repository._request.call_args.args[0] == "GET"


def test_calendar_list_supplies_existing_event_keys_and_revision_for_update(repository):
    event = {"event_id": "race-one", "event_type": "MAIN_RACE", "name": "Основен старт", "start_date": "2027-01-09", "end_date": "2027-01-10"}
    repository._request.return_value = [{"planning_calendar": {"schema_version": "planning-calendar-v1", "events": [event]}, "calendar_revision": 9}]
    result = context.read(repository, ALIAS, FULL, {"kind": "inputs", "input_kind": "CALENDAR_EVENT"}, now=NOW)
    assert result["entries"] == [{"kind": "CALENDAR_EVENT", "entry_key": "race-one", "revision": 9, "payload": event, "identical_copy_count": 1}]


def test_session_discovery_uses_completed_activity_date_duration_and_current_input_revision(repository):
    ref = "act_" + "a" * 32
    repository.active_activity_calendar = Mock(return_value={"activities": [
        {"activity_ref": ref, "local_date": "2026-11-11", "name": "Бягане", "sport": "Run", "elapsed_time_s": 3600, "private": "hidden"},
        {"activity_ref": "act_" + "b" * 32, "local_date": "2026-11-10", "elapsed_time_s": 1800}]})
    repository._request.return_value = [{"kind": "SESSION", "entry_key": ref, "revision": 2,
        "payload": {"activity_ref": ref, "rpe": 5, "duration_minutes": 55, "timing": "DELAYED"}}]
    result = context.read(repository, ALIAS, FULL, {"kind": "inputs", "input_kind": "SESSION"}, now=NOW)
    assert result["date"] == "2026-11-11" and result["activity_count"] == 1
    entry = result["entries"][0]
    assert entry["revision"] == 2 and entry["entry_key"] == ref
    assert entry["suggested_duration_minutes"] == 60 and entry["payload"]["duration_minutes"] == 55
    assert "private" not in json.dumps(result) and "plan_revision" not in result
    repository.plan_read.assert_not_called()
    repository.active_analysis.assert_not_called()


def test_session_discovery_does_not_invent_activity_when_none_and_rejects_future(repository):
    repository.active_activity_calendar = Mock(return_value={"activities": []})
    result = context.read(repository, ALIAS, FULL, {"kind": "inputs", "input_kind": "SESSION"}, now=NOW)
    assert result["entries"] == [] and result["activity_count"] == 0
    with pytest.raises(HTTPException) as error:
        context.read(repository, ALIAS, FULL, {"kind": "inputs", "input_kind": "SESSION", "date": "2026-11-12"}, now=NOW)
    assert error.value.status_code == 422
    repository._request.assert_not_called()


def test_session_discovery_checks_recovery_permission_before_reading_activity(repository):
    repository.active_activity_calendar = Mock()
    with pytest.raises(HTTPException) as error:
        context.read(repository, ALIAS, PLAN, {"kind": "inputs", "input_kind": "SESSION"}, now=NOW)
    assert error.value.status_code == 403
    repository.active_activity_calendar.assert_not_called()


def test_effective_profile_read_matches_editable_application_configuration(repository, monkeypatch):
    monkeypatch.setattr(context, "ManagementStore", lambda _: SimpleNamespace(profile=lambda _: {"revision": 5, "profile": {
        "sport": "Run", "planning_controls": {"double_threshold_days": [2], "capacity_policy": "MODEL_WITH_PRIOR"},
        "individual_learning": {"mode": "CONTROL"}, "adaptation_mode": "AUTO", "building_fraction": 0.65}}))
    result = context.read(repository, ALIAS, PLAN, {"kind": "inputs", "input_kind": "MANAGEMENT_PROFILE"}, now=NOW)
    assert result["revision"] == 5 and result["payload"]["planning_controls"] == {"double_threshold_days": [2], "capacity_policy": "MODEL_WITH_PRIOR"}
    assert result["payload"]["individual_learning"] == {"mode": "CONTROL"}
    assert result["payload"]["adaptation_mode"] == "AUTO" and result["payload"]["building_fraction"] == 0.65


@pytest.mark.parametrize("quality", [{"complete": True, "estimated": True, "readiness_basis": "RECORDED_LOAD"},
    {"complete": True, "estimated": False, "readiness_basis": "ESTIMATED_LOAD"},
    {"readiness_available": True, "readiness_basis": "RECORDED_LOAD"}])
def test_contradictory_or_unknown_explicit_quality_does_not_admit_raw_numbers(quality):
    result = context.present_recovery({"data_quality": quality, "current": [{"zone": "Z1", "readiness_percent": 92}]})
    assert result["availability"] == "UNAVAILABLE" and "readiness_percent" not in json.dumps(result)


@pytest.mark.parametrize("kind", ["settings", "activities", "activity", "speed", "trainability", "learning", "response"])
def test_new_private_read_surfaces_reject_plan_only_before_any_read(repository, kind):
    repository.prepared_view = Mock()
    with pytest.raises(HTTPException) as error:
        context.read(repository, ALIAS, PLAN, {"kind": kind}, now=NOW)
    assert error.value.status_code == 403
    repository.athlete_settings.assert_not_called()
    repository.prepared_view.assert_not_called()


def prepared_repository(repository, payload, kind):
    repository.prepared_view = Mock(return_value={"ready": True, "athlete_alias": ALIAS,
        "kind": kind, "payload": payload})
    return repository


@pytest.mark.parametrize("query,cache_kind,parameters", [
    ({"kind": "speed"}, "speed", {}),
    ({"kind": "speed", "sport": "Run", "duration_s": 300}, "speed", {"sport": "Run", "duration_s": 300}),
    ({"kind": "speed", "view": "history", "sport": "NordicSki"}, "speed-history", {"sport": "NordicSki"}),
    ({"kind": "speed", "view": "load", "start": "2026-11-01", "end": "2026-11-10"}, "speed-load", {"period_start": "2026-11-01", "period_end": "2026-11-10"}),
    ({"kind": "response"}, "response", {}),
    ({"kind": "trainability"}, "trainability", {}),
    ({"kind": "management"}, "management-view", {"view": "overview", "projection": "overview-v1"}),
    ({"kind": "management", "view": "state"}, "management-state", {}),
    ({"kind": "management", "view": "outlook"}, "management-outlook", {}),
    ({"kind": "methods", "phase": "COMPETITION"}, "management-methods", {"phase": "COMPETITION"}),
])
def test_new_views_use_exact_canonical_prepared_cache_keys(repository, monkeypatch, query, cache_kind, parameters):
    from apps.api import prepared_views
    monkeypatch.setattr(prepared_views, "calculate", Mock(side_effect=AssertionError("No model calculation on assistant read")))
    prepared_repository(repository, {"status": "AVAILABLE", "revision": 4}, cache_kind)
    result = context.read(repository, ALIAS, FULL, query, now=NOW)
    assert result["availability"] == "AVAILABLE" and result["data"]["revision"] == 4
    assert repository.prepared_view.call_args.args == ("READ",)
    assert repository.prepared_view.call_args.kwargs["parameters"] == parameters
    repository.active_analysis.assert_not_called()
    prepared_views.calculate.assert_not_called()


@pytest.mark.parametrize("kind", ["speed", "trainability", "response", "management", "methods"])
def test_cold_new_views_remain_pending_without_calculation(repository, kind):
    repository.prepared_view = Mock(return_value={"ready": False})
    result = context.read(repository, ALIAS, FULL, {"kind": kind}, now=NOW)
    assert result["availability"] == "PENDING"
    repository.active_analysis.assert_not_called()


def test_prepared_read_wrong_scope_does_not_reach_assistant(repository):
    repository.prepared_view = Mock(return_value={"ready": True, "athlete_alias": "other", "kind": "speed",
        "payload": {"private": "other-athlete"}})
    result = context.read(repository, ALIAS, FULL, {"kind": "speed"}, now=NOW)
    assert result["availability"] == "PENDING" and "other-athlete" not in json.dumps(result)


def test_speed_curve_and_dosing_are_paginated_without_losing_exact_samples(repository):
    points = [{"duration_s": n + 1, "speed_kmh": 30 - n / 10} for n in range(65)]
    payload = {"status": "READY", "points": points, "dosing_model": {"points": deepcopy(points)},
        "hr_bounds": [100, 120, 140, 160, 180, 200], "tests": [{"entry_key": "test-a"}]}
    prepared_repository(repository, payload, "speed")
    first = context.read(repository, ALIAS, FULL, {"kind": "speed", "view": "curve"}, now=NOW)
    assert len(first["data"]["points"]) == 30
    page = next(row for row in first["pagination"] if row["section"] == "points")
    assert page == {"section": "points", "total": 65, "offset": 0, "returned": 30, "next_offset": 30}
    seen = first["data"]["points"]
    for offset in (30, 60):
        result = context.read(repository, ALIAS, FULL, {"kind": "speed", "section": "points", "offset": offset}, now=NOW)
        seen += result["data"]
    assert seen == points
    # Copy semantics: paging or summaries cannot mutate the shared saved view.
    assert payload["points"] == points and len(payload["dosing_model"]["points"]) == 65


def test_page_offset_never_slices_short_zone_vectors(repository):
    prepared_repository(repository, {"points": list(range(65)), "zone_bounds": [100, 120, 140, 160, 180, 200]}, "speed")
    result = context.read(repository, ALIAS, FULL, {"kind": "speed", "view": "curve", "offset": 30}, now=NOW)
    assert result["data"]["points"] == list(range(30, 60))
    assert result["data"]["zone_bounds"] == [100, 120, 140, 160, 180, 200]


def test_speed_summary_offers_full_curve_and_test_reads(repository):
    prepared_repository(repository, {"status": "READY", "points": [1, 2], "tests": [{"entry_key": "t"}],
        "dosing_model": {"points": [1, 2], "source": "BLENDED"}, "speed_zones": {"zones": [{"zone": "Z1"}]}}, "speed")
    overview = context.read(repository, ALIAS, FULL, {"kind": "speed"}, now=NOW)
    assert "points" not in overview["data"] and "tests" not in overview["data"]
    assert overview["data"]["dosing_model"] == {"source": "BLENDED"}
    tests = context.read(repository, ALIAS, FULL, {"kind": "speed", "view": "tests"}, now=NOW)
    assert tests["data"]["tests"] == [{"entry_key": "t"}]
    points = context.read(repository, ALIAS, FULL, {"kind": "speed", "section": "dosing_model.points"}, now=NOW)
    assert points["data"] == [1, 2]


@pytest.mark.parametrize("query", [
    {"kind": "speed", "duration_s": float("nan")}, {"kind": "speed", "duration_s": True},
    {"kind": "speed", "duration_s": 300, "speed_kmh": 20},
    {"kind": "speed", "view": "arbitrary-route"}, {"kind": "learning", "days": 1000},
    {"kind": "trainability", "start": "2026-11-12", "end": "2026-11-10"},
    {"kind": "response", "start": "2025-01-01", "end": "2026-11-10"},
])
def test_invalid_new_query_never_requests_prepared_view(repository, query):
    repository.prepared_view = Mock()
    with pytest.raises(HTTPException) as error:
        context.read(repository, ALIAS, FULL, query, now=NOW)
    assert error.value.status_code == 422
    repository.prepared_view.assert_not_called()


def test_learning_reads_historical_year_from_shared_prepared_history(repository):
    repository.learning_preparation = Mock(side_effect=AssertionError("Use archived prepared reader"))
    repository.learning_history_view = Mock(return_value={"ready": True, "day": "2025-12-31",
        "payload": {"athlete_alias": ALIAS, "report": {"schema_version": "recovery-learning-view-v1", "as_of": "2025-12-31",
            "series": [{"day": "2025-01-01", "stress_index": 0.2}, {"day": "2025-12-31", "stress_index": 0.1}]}}})
    result = context.read(repository, ALIAS, FULL, {"kind": "learning", "days": 366, "end": "2025-12-31"}, now=NOW)
    assert result["data"]["series"][0]["day"] == "2025-01-01"
    assert repository.learning_history_view.call_args.kwargs["days"] == 366
    assert repository.learning_history_view.call_args.kwargs["end"].isoformat() == "2025-12-31"
    repository.active_analysis.assert_not_called()


def test_methods_catalog_summary_selects_complete_current_card_without_frozen_plan(repository):
    card = {"id": "tempo", "title": "Темпово бягане", "guidance": {"execution": "Пълен текст"},
        "source": {"authors": "Example author"}, "resolution": {"target_speed_kmh": 16}}
    prepared_repository(repository, {"methods": [card], "revision": 4}, "management-methods")
    summary = context.read(repository, ALIAS, PLAN, {"kind": "methods"}, now=NOW)
    assert summary["data"]["methods"][0]["id"] == "tempo"
    assert summary["data"]["methods"][0]["title"] == "Темпово бягане"
    detail = context.read(repository, ALIAS, PLAN, {"kind": "methods", "method_id": "tempo"}, now=NOW)
    assert all(detail["data"]["method"][key] == value for key, value in card.items())
    repository.plan_read.assert_not_called()


def test_management_week_distinguishes_proposal_from_approved_plan(repository):
    result = context.read(repository, ALIAS, PLAN, {"kind": "management", "view": "week"}, now=NOW)
    data = result["data"]
    assert data["revision"] == 7 and data["plan_status"] == "REVIEW_REQUIRED"
    assert data["plan"]["is_approved"] is True and data["proposal"]["is_approved"] is False
    assert data["proposal"]["days"][0]["sessions"][0]["title"] == "Неприето предложение"
    assert "dose_evidence" not in json.dumps(data) and "private detail" not in json.dumps(data)


def test_navigation_does_not_load_training_settings(repository):
    result = context.read(repository, ALIAS, PLAN, {"kind": "navigation", "key": "athletes"}, now=NOW)
    assert result["performed"] is False and result["links"][0]["href"] == "/account#athletes-title"
    repository.athlete_settings.assert_not_called()


def test_speed_comparison_requires_recovery_before_prepared_read(repository):
    repository.prepared_view = Mock()
    with pytest.raises(HTTPException) as error:
        context.read(repository, ALIAS, PLAN, {"kind": "management", "view": "speed-comparison", "plan_kind": "ACTIVE", "revision": 7}, now=NOW)
    assert error.value.status_code == 403
    repository.prepared_view.assert_not_called()


def test_speed_comparison_is_pinned_to_exact_kind_and_revision(repository):
    prepared_repository(repository, {"revision": 7, "comparison": []}, "management-speed-comparison")
    context.read(repository, ALIAS, FULL, {"kind": "management", "view": "speed-comparison", "plan_kind": "DRAFT", "revision": 7, "date": "2026-11-09"}, now=NOW)
    assert repository.prepared_view.call_args.kwargs["parameters"] == {"kind": "DRAFT", "revision": 7, "start_date": "2026-11-09"}


def test_speed_segment_preview_uses_saved_prepared_selection(repository):
    key = "act_" + "a" * 32
    prepared_repository(repository, {"activity_ref": key, "selection": {"speed_kmh": 18}}, "speed-preview")
    result = context.read(repository, ALIAS, FULL, {"kind": "speed", "view": "preview", "activity_ref": key, "start_s": 30, "duration_s": 300}, now=NOW)
    assert result["data"]["selection"]["speed_kmh"] == 18
    assert repository.prepared_view.call_args.kwargs["parameters"] == {"activity_ref": key, "start_s": 30, "duration_s": 300}


def test_planning_job_status_uses_authenticated_actor_not_model_scope(repository):
    repository.planning_job = Mock(return_value={"job": None})
    actor = "12345678-1234-1234-1234-123456789abc"
    result = context.read(repository, ALIAS, FULL, {"kind": "management", "view": "generation-jobs"}, now=NOW, actor=actor)
    assert result["data"]["job"] is None
    assert repository.planning_job.call_args.kwargs == {"alias": ALIAS, "actor": actor}
    with pytest.raises(HTTPException) as error:
        context.read(repository, ALIAS, PLAN, {"kind": "management", "view": "generation-jobs"}, now=NOW, actor=actor)
    assert error.value.status_code == 403


def test_session_entry_fetches_compact_generation_calendar_without_snapshot(repository):
    key = "act_" + "a" * 32
    repository._rpc_row = Mock(return_value={"generation_id": "generation", "revision": 4,
        "activities": [{"activity_ref": key, "local_date": "2026-11-11", "sport": "Run", "elapsed_time_s": 3600}]})
    repository._request.return_value = []
    repository.active_activity_calendar = Mock(side_effect=AssertionError("Large snapshot path must not be used"))
    result = context.read(repository, ALIAS, FULL, {"kind": "inputs", "input_kind": "SESSION"}, now=NOW)
    assert result["entries"][0]["entry_key"] == key and result["entries"][0]["suggested_duration_minutes"] == 60
    assert repository._rpc_row.call_args.args == (
        "active_onflows_activity_calendar?select=generation_id,revision,activities",
        {"p_athlete_alias": ALIAS, "p_period_start": "2026-11-11", "p_period_end": "2026-11-11"})
    repository.active_activity_calendar.assert_not_called()


def test_full_management_input_contract_exposes_nested_app_controls():
    contract = context.input_contract("MANAGEMENT_PROFILE")
    assert "individual_learning" in contract["properties"] and "method_library" in contract["properties"]
    assert "planning_controls" in contract["properties"]
    assert "IndividualLearning" in contract["$defs"] and "MethodLibraryPreferences" in contract["$defs"]
    assert "maintenance_fraction" not in contract["properties"]


def test_nested_array_pages_have_exact_retrievable_paths(repository):
    prepared_repository(repository, {"tests": [{"entry_key": "t", "samples": list(range(35))}]}, "speed")
    result = context.read(repository, ALIAS, FULL, {"kind": "speed", "view": "tests"}, now=NOW)
    page = next(row for row in result["pagination"] if row["section"] == "tests.0.samples")
    assert page["next_offset"] == 30
    rest = context.read(repository, ALIAS, FULL, {"kind": "speed", "section": page["section"], "offset": page["next_offset"]}, now=NOW)
    assert rest["data"] == [30, 31, 32, 33, 34]


def test_activity_view_uses_canonical_pinned_detail_without_leaking_raw_catalog(repository, monkeypatch):
    from apps.api.routes import activities
    key = "act_" + "b" * 32
    raw = {"catalog_payload": {"private_provider_identity": "not-a-display-field"}}
    repository.active_activity_view = Mock(return_value=raw)
    canonical = {"revision": 3, "generation_id": "g", "activity": {"activity_ref": key, "name": "Intervals", "intervals": [{"duration_s": 30}]},
        "series": {"series": [{"elapsed_s": 0, "speed_kmh": 12}, {"elapsed_s": 30, "speed_kmh": 18}, {"elapsed_s": 60, "speed_kmh": 10}]},
        "shadow": {"trainability_index": {"general": {"index": 1.1}}, "summary": {"duration_s": 60}, "samples": [{"hrmod": 160}]}}
    projected = Mock(return_value=canonical)
    monkeypatch.setattr(activities, "_activity_view_payload", projected)
    result = context.read(repository, ALIAS, FULL, {"kind": "activity", "activity_ref": key}, now=NOW)
    assert result["data"]["activity"]["name"] == "Intervals"
    assert result["data"]["analysis"]["trainability_index"]["general"]["index"] == 1.1
    assert "private_provider_identity" not in json.dumps(result) and "samples" not in result["data"]["analysis"]
    projected.assert_called_once_with(raw, key)
    series = context.read(repository, ALIAS, FULL, {"kind": "activity", "activity_ref": key, "view": "series", "start_s": 15, "duration_s": 30}, now=NOW)
    assert series["data"]["series"]["series"] == [{"elapsed_s": 30, "speed_kmh": 18}]
    assert len(canonical["series"]["series"]) == 3


def test_athlete_settings_input_requires_edit_grant_but_not_recovery(repository, monkeypatch):
    from apps.api import assistant_app_actions
    read_input = Mock(return_value={"availability": "AVAILABLE", "kind": "ATHLETE_SETTINGS", "entry_key": "athlete-settings", "revision": 1, "payload": {"hrmax_bpm": 200}})
    monkeypatch.setattr(assistant_app_actions, "read_input", read_input)
    access = {**PLAN, "can_edit_plan": True}
    result = context.read(repository, ALIAS, access, {"kind": "inputs", "input_kind": "ATHLETE_SETTINGS"}, now=NOW)
    assert result["payload"]["hrmax_bpm"] == 200
    with pytest.raises(HTTPException) as error:
        context.read(repository, ALIAS, PLAN, {"kind": "inputs", "input_kind": "ATHLETE_SETTINGS"}, now=NOW)
    assert error.value.status_code == 403


def test_speed_exposure_period_filter_does_not_mislabel_full_window_totals(repository):
    prepared_repository(repository, {"start_date": "2026-10-02", "end_date": "2026-11-10", "recorded_minutes": 200,
        "zones": [{"zone": "Z1", "minutes": 200}], "activities": [
            {"date": "2026-10-15", "recorded_minutes": 100}, {"date": "2026-11-09", "recorded_minutes": 100}]}, "speed-history")
    result = context.read(repository, ALIAS, FULL, {"kind": "speed", "view": "history", "start": "2026-11-01", "end": "2026-11-10"}, now=NOW)["data"]
    assert result["activities"] == [{"date": "2026-11-09", "recorded_minutes": 100}]
    assert "recorded_minutes" not in result
    assert result["full_window_summary"]["recorded_minutes"] == 200
    assert result["full_window_summary"]["start"] == "2026-10-02"


@pytest.mark.parametrize("view", ["overview", "outlook", "profile"])
def test_plan_only_management_projection_blocks_private_evidence_and_section_bypass(repository, view):
    profile = {"sport": "Run", "planning_controls": {"sessions_per_week": 8}, "individual_learning": {"mode": "CONTROL"},
        "unrecognized_private_observation": {"score": "private-sentinel"}}
    outlook = {"profile_revision": 4, "long_term": {"weeks": [{"start_date": "2026-11-11", "end_date": "2026-11-17", "components": {"Z1": {"target_weekly_q": 200}}}],
        "baseline": {"private-sentinel": True}}, "component_history": {"private-sentinel": True}}
    payload = {"profile": {"configured": True, "revision": 4, "profile": profile}, "outlook": {"outlook": outlook},
        "unrecognized_private_observation": {"score": "private-sentinel"}}
    if view == "profile":
        payload = {"configured": True, "revision": 4, "profile": profile, "history": [{"score": "private-sentinel"}]}
    if view == "outlook":
        payload = {"outlook": outlook}
    prepared_repository(repository, payload, "management-view" if view == "overview" else "management-" + view)
    result = context.read(repository, ALIAS, PLAN, {"kind": "management", "view": view}, now=NOW)
    assert "private-sentinel" not in json.dumps(result)
    if view == "outlook":
        assert result["data"]["outlook"]["long_term"]["weeks"][0]["components"]["Z1"]["target_weekly_q"] == 200
    if view == "profile":
        assert result["data"]["profile"]["individual_learning"]["mode"] == "CONTROL"
    with pytest.raises(HTTPException) as error:
        context.read(repository, ALIAS, PLAN, {"kind": "management", "view": view, "section": "unrecognized_private_observation"}, now=NOW)
    assert error.value.status_code == 422
