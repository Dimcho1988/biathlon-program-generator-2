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
    assert result["entries"] == [{"kind": "CALENDAR_EVENT", "entry_key": "race-one", "revision": 9, "payload": event}]


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


def test_effective_profile_controls_exclude_scientific_and_approval_settings(repository, monkeypatch):
    monkeypatch.setattr(context, "ManagementStore", lambda _: SimpleNamespace(profile=lambda _: {"revision": 5, "profile": {
        "sport": "Run", "planning_controls": {"double_threshold_days": [2], "capacity_policy": "MODEL_WITH_PRIOR"},
        "individual_learning": {"mode": "CONTROL"}, "approval_mode": "AUTO", "building_fraction": 0.5}}))
    result = context.read(repository, ALIAS, PLAN, {"kind": "inputs", "input_kind": "MANAGEMENT_PROFILE"}, now=NOW)
    assert result["revision"] == 5 and result["payload"]["planning_controls"] == {"double_threshold_days": [2]}
    assert "individual_learning" not in result["payload"] and "approval_mode" not in result["payload"] and "building_fraction" not in result["payload"]


@pytest.mark.parametrize("quality", [{"complete": True, "estimated": True, "readiness_basis": "RECORDED_LOAD"},
    {"complete": True, "estimated": False, "readiness_basis": "ESTIMATED_LOAD"},
    {"readiness_available": True, "readiness_basis": "RECORDED_LOAD"}])
def test_contradictory_or_unknown_explicit_quality_does_not_admit_raw_numbers(quality):
    result = context.present_recovery({"data_quality": quality, "current": [{"zone": "Z1", "readiness_percent": 92}]})
    assert result["availability"] == "UNAVAILABLE" and "readiness_percent" not in json.dumps(result)
