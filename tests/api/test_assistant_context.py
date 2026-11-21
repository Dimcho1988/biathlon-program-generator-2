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
    repo.plan_read = Mock(side_effect=lambda _, **kwargs: deepcopy(plan()))
    monkeypatch.setattr(context, "PlanStore", lambda _: SimpleNamespace(current=repo.plan_read))
    return repo


def test_two_exact_sessions_are_read_from_approved_revision_in_athlete_timezone(repository):
    result = context.read(repository, ALIAS, FULL, {"kind": "day"}, now=NOW)
    assert result["today"] == result["date"] == "2026-11-11"
    assert result["timezone"] == "Europe/Sofia" and result["plan_revision"] == 7
    assert [s["total_minutes"] for s in result["sessions"]] == [21, 22]
    assert [s["session_id"] for s in result["sessions"]] == ["2026-11-11:1", "2026-11-11:2"]
    assert result["proposal_available"] and result["prescription_active"] is False
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


@pytest.mark.parametrize("kind", ["day", "method"])
def test_saved_ski_modality_has_exact_app_label_without_changing_prescription(repository, kind):
    saved = plan()
    sessions = saved["payload"]["plan"]["days"][0]["sessions"]
    sessions[0]["sport"], sessions[1]["sport"] = "NordicSki", "RollerSki"
    original = deepcopy(saved)
    repository.plan_read.side_effect = lambda _: saved
    result = context.read(repository, ALIAS, FULL, {"kind": kind}, now=NOW)
    assert [(s["sport"], s["sport_label_bg"]) for s in result["sessions"]] == [
        ("NordicSki", "Ски бягане"), ("RollerSki", "Ролкови ски")]
    for shown, source in zip(result["sessions"], sessions):
        assert shown["total_minutes"] == source["total_minutes"]
        assert shown["blocks"][0]["target_speed_kmh"] == source["blocks"][0]["target_speed_kmh"]
    assert saved == original


def test_unknown_or_absent_sport_is_not_given_an_invented_label():
    for sport in ("UnknownDiscipline", None):
        saved = {"sport": sport, "blocks": []}
        result = context._session(saved, "2026-11-11:1", FULL)
        assert result["sport"] == sport and "sport_label_bg" not in result


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
    assert result["availability"] == "UNAVAILABLE" and result["reason"] == "NO_SAVED_PLAN" and result["sessions"] == []


def test_missing_date_does_not_mean_rest(repository):
    result = context.read(repository, ALIAS, FULL, {"kind": "day", "date": "2026-11-12"}, now=NOW)
    assert result["reason"] == "DATE_NOT_IN_SAVED_PLAN" and result["sessions"] == []
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
    result = context.read(repository, ALIAS, FULL, {"kind": "history", "view": "daily", "start": "2026-11-08", "end": "2026-11-10"}, now=NOW)
    assert result["unobserved_days"] == ["2026-11-08"] and result["missing_day_means_rest"] is False
    assert len(result["data"]["daily"]) == 2 and result["units"]["effective_load"] == "E_EFFECTIVE_LOAD"
    activities = context.read(repository, ALIAS, FULL, {"kind": "history", "view": "activities", "start": "2026-11-08", "end": "2026-11-10"}, now=NOW)
    assert "note" not in activities["data"]["activities"][0]


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
    result = context.read(repository, ALIAS, FULL, {"kind": "history", "view": "daily", "start": "2026-11-08", "end": "2026-11-10"}, now=NOW)
    assert result["estimated_days"] == ["2026-11-09"]
    assert result["data"]["daily"][0]["estimated"] is True
    assert result["data"]["daily"][0]["estimation_basis"] == "INITIAL_HEURISTIC"
    assert result["data"]["daily"][0]["effective_load"] == 40
    assert "estimated" not in result["data"]["daily"][1]
    activities = context.read(repository, ALIAS, FULL, {"kind": "history", "view": "activities", "start": "2026-11-08", "end": "2026-11-10"}, now=NOW)["data"]["activities"]
    assert activities[0]["quality_status"] == "limited"
    assert activities[0]["hr_coverage_percent"] == 65
    assert activities[0]["load_source"] == {
        "initial_heuristic": False, "calibration_source": "INITIAL_HEURISTIC", "speed_role": "RAW_SPEED", "calibration_eligible": False}
    assert "private omitted detail" not in json.dumps(activities)
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
    assert data["plan"]["plan_role"] == "CURRENT" and data["proposal"]["plan_role"] == "CANDIDATE"
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


@pytest.mark.parametrize("date_field", ["date", "start"])
def test_plan_activate_input_forwards_exact_draft_selector_without_changing_active_identity(repository, monkeypatch, date_field):
    from apps.api import assistant_app_actions
    exact = {"availability": "AVAILABLE", "kind": "PLAN_ACTIVATE", "entry_key": "active-plan", "revision": 7,
        "payload": {"status": "ACTIVE"}, "preview_summary": {"drafts": [{"start_date": "2026-11-01", "draft_revision": 2}]}}
    read_input = Mock(return_value=exact)
    monkeypatch.setattr(assistant_app_actions, "read_input", read_input)
    result = context.read(repository, ALIAS, PLAN, {"kind": "inputs", "input_kind": "PLAN_ACTIVATE",
        "key": "active-plan", date_field: "2026-11-01", "revision": 2}, now=NOW)
    read_input.assert_called_once_with(repository, ALIAS, "PLAN_ACTIVATE", "active-plan",
        draft_date="2026-11-01", draft_revision=2)
    assert result["entry_key"] == "active-plan" and result["revision"] == 7
    assert result["preview_summary"]["drafts"] == [{"start_date": "2026-11-01", "draft_revision": 2}]


def strict_provider_query(**values):
    """The real strict reply includes all optional fields, with null defaults."""
    from apps.api.assistant_provider import _REPLY_TOOL
    fields = _REPLY_TOOL["parameters"]["properties"]["query"]["properties"]
    return {**dict.fromkeys(fields), **values}


def test_strict_nullable_settings_query_uses_pagination_defaults(repository, monkeypatch):
    from apps.api.routes import settings
    canonical = {"configured": True, "hrmax_bpm": 200, "timezone": "Europe/Sofia"}
    monkeypatch.setattr(settings, "_settings_response", lambda _: SimpleNamespace(model_dump=lambda **_: deepcopy(canonical)))
    query = strict_provider_query(kind="settings")
    result = context.read(repository, ALIAS, FULL, query, now=NOW)
    assert result["data"] == canonical and result["pagination"] == []
    assert query["offset"] is None and query["limit"] is None  # caller input is not mutated


def test_strict_nullable_learning_query_uses_default_days(repository):
    repository.learning_preparation = Mock()
    repository.learning_history_view = Mock(return_value={"ready": True, "day": "2026-11-11",
        "payload": {"athlete_alias": ALIAS, "report": {"schema_version": "recovery-learning-view-v1",
            "as_of": "2026-11-11", "series": [{"day": "2026-11-11", "stress_index": 0.2}]}}})
    result = context.read(repository, ALIAS, FULL, strict_provider_query(kind="learning"), now=NOW)
    assert result["data"]["series"] == [{"day": "2026-11-11", "stress_index": 0.2}]
    assert repository.learning_history_view.call_args.kwargs["days"] == 30


def test_strict_nullable_management_checks_query_uses_default_page(repository, monkeypatch):
    from apps.api.management_plan_store import PlanStore
    checks = Mock(return_value={"checks": [], "next_before": None})
    monkeypatch.setattr(PlanStore, "checks", checks)
    result = context.read(repository, ALIAS, FULL, strict_provider_query(kind="management", view="checks"), now=NOW)
    assert result["data"] == {"checks": [], "next_before": None}
    checks.assert_called_once_with(ALIAS, before=None, limit=30)


def test_strict_nullable_activity_segment_query_uses_zero_start_and_open_duration(repository, monkeypatch):
    from apps.api.routes import activities
    key = "act_" + "c" * 32
    repository.active_activity_view = Mock(return_value={})
    series = [{"elapsed_s": 0, "speed_kmh": 12}, {"elapsed_s": 30, "speed_kmh": 18}]
    monkeypatch.setattr(activities, "_activity_view_payload", lambda *_: {
        "activity": {"activity_ref": key}, "series": {"series": deepcopy(series)}, "shadow": None})
    result = context.read(repository, ALIAS, FULL, strict_provider_query(kind="activity", view="series", activity_ref=key), now=NOW)
    assert result["data"]["series"]["series"] == series


def test_strict_nullable_speed_query_uses_same_default_cache_key(repository):
    prepared_repository(repository, {"status": "READY", "speed_zones": {"zones": []}}, "speed")
    result = context.read(repository, ALIAS, FULL, strict_provider_query(kind="speed"), now=NOW)
    assert result["data"]["status"] == "READY"
    assert repository.prepared_view.call_args.kwargs["parameters"] == {}


def interval_session(repetitions=17, work_seconds=30, rest_seconds=30):
    """Synthetic saved prescription with the same long-interval shape as UI."""
    value = session(1)
    value["blocks"] = [{"kind": "WARMUP", "label": "Загрявка", "duration_s": 600,
                        "zone": "Z1", "target_hr_bpm": 125, "instructions": "Плавно загряване."}]
    for number in range(1, repetitions + 1):
        value["blocks"].append({"kind": "WORK", "label": f"Отсечка {number}/{repetitions}",
            "repetition": number, "duration_s": work_seconds, "duration_min": work_seconds / 60,
            "target_speed_kmh": 18.25, "target_speed_range_kmh": [18.0, 18.5],
            "target_hr_bpm": None, "primary_control": "SPEED_AND_QUALITY", "zone": "Z4",
            "parent_method_card_ref": "method_library.components[0]",
            "instructions": "Запази равномерната скорост и техниката. Пулсът е наблюдение; не ускорявай, за да достигнеш число. " * 2})
        if number < repetitions:
            value["blocks"].append({"kind": "RECOVERY", "label": "Активна почивка", "repetition": None,
                "duration_s": rest_seconds, "duration_min": rest_seconds / 60, "zone": "Z1",
                "target_hr_bpm": 125, "passive_rest": False,
                "instructions": "Използвай цялата почивка с леко движение. Не добавяй почивка след последната отсечка."})
    value["blocks"].append({"kind": "COOLDOWN", "label": "Разпускане", "duration_s": 300,
                            "zone": "Z1", "instructions": "Постепенно намали усилието."})
    value["main_work_minutes"] = repetitions * work_seconds / 60
    value["total_minutes"] = sum(b["duration_s"] for b in value["blocks"]) / 60
    return value


def test_repetition_groups_round_trip_exact_doses_order_labels_and_missing_final_rest():
    value = interval_session()
    original = deepcopy(value)
    result = context._session(value, "2026-11-11:1", FULL)
    assert context.expand_blocks(result["blocks"]) == original["blocks"]
    assert value == original
    assert result["block_count"] == 35
    group = result["blocks"][1]
    assert group["kind"] == "REPEAT_GROUP" and group["repeat_count"] == 16
    assert group["total_duration_s"] == 960
    assert [block["kind"] for block in group["sequence"]] == ["WORK", "RECOVERY"]
    assert result["blocks"][2]["kind"] == "WORK" and result["blocks"][2]["repetition"] == 17
    assert result["blocks"][3]["kind"] == "COOLDOWN"
    assert result["main_work_minutes"] == 8.5 and result["total_minutes"] == 31.5
    assert result["dose_evidence"]["explanation"] == original["dose_evidence"]["explanation"]


@pytest.mark.parametrize("field,value", [
    ("duration_s", 25), ("target_speed_kmh", 18.1), ("target_speed_range_kmh", [17.9, 18.6]),
    ("target_hr_bpm", 170), ("instructions", "Контролирай различната цел на тази отсечка."),
    ("label", "Самостоятелна различна задача"), ("parent_method_card_ref", "method_library.components[1]")])
def test_repetition_group_never_merges_different_execution_contracts(field, value):
    blocks = interval_session()["blocks"]
    blocks[11][field] = value
    result = context.compact_blocks(blocks)
    assert context.expand_blocks(result) == blocks
    assert any(block.get(field) == value and block.get("repetition") == 6 for block in result)


def test_changed_recovery_splits_groups_and_preserves_all_recovery_times():
    blocks = interval_session()["blocks"]
    blocks[12]["duration_s"] = 45
    blocks[12]["duration_min"] = 0.75
    result = context.compact_blocks(blocks)
    assert context.expand_blocks(result) == blocks
    assert sum(block["duration_s"] for block in context.expand_blocks(result) if block["kind"] == "RECOVERY") == 495


def test_grouping_preserves_absent_null_and_nonsequential_repetition_metadata():
    blocks = [{"kind": "WORK", "label": "Равномерна част", "duration_min": 1.5,
               "instructions": "Запази зададеното усилие и техниката. " * 12,
               **({"repetition": value} if index else {})} for index, value in enumerate((None, None, 8, 2))]
    result = context.compact_blocks(blocks)
    assert context.expand_blocks(result) == blocks
    assert result[0]["repeat_count"] == 4 and result[0]["total_duration_s"] == 360


def test_two_large_session_day_with_long_chat_fits_real_provider_payload_losslessly(repository):
    from apps.api.assistant_provider import OpenAIAssistantProvider, OpenAIProviderConfig
    from apps.api.assistant_service import _capabilities
    from biathlon import method_library
    first, second = interval_session(), interval_session(3, 480, 120)
    # Real public catalog cards exercise structured scientific guidance; all
    # athlete prescriptions and observations in this fixture are synthetic.
    first["method_library"] = next(deepcopy(card) for card in method_library.entries() if card["id"] == "R15")
    second["method_library"]["guidance"]["execution"] = "Контролирано изпълнение със запазена техника. " * 25
    for number, saved in enumerate((first, second), 1):
        saved["dose_evidence"].update({"explanation": "Основанието е запазено с утвърдения план. " * 12,
            "limits": [{"code": f"LIMIT_{number}_{i}", "limit_minutes": 10 + i,
                        "explanation": "Запазено ограничение на индивидуалната доза. " * 5} for i in range(6)],
            "effort_profile": {"work_seconds": 30 if number == 1 else 480, "recovery_seconds": 30 if number == 1 else 120,
                               "effort": "Запази усилието според индивидуалното предписание. " * 8}})
    record = plan()
    record["payload"]["plan"]["days"][0]["sessions"] = [first, second]
    repository.plan_read.side_effect = lambda _: deepcopy(record)
    result = context.read(repository, ALIAS, FULL, {"kind": "day"}, now=NOW)
    for shown, saved in zip(result["sessions"], (first, second)):
        assert context.expand_blocks(shown["blocks"]) == saved["blocks"]
        for key in ("explanation", "limits", "effort_profile"):
            assert shown["dose_evidence"][key] == saved["dose_evidence"][key]
    data = {"results": [result], "completed_queries": [{"kind": "day", "date": "2026-11-11"}]}
    original = deepcopy(data)
    messages = [{"role": "user", "content": "Обясни предишната тренировка."},
                {"role": "assistant", "content": "Подробно предишно обяснение за тренировъчната методика. " * 160},
                {"role": "user", "content": "Как точно да изпълня двете сесии днес?"}]
    provider = OpenAIAssistantProvider(OpenAIProviderConfig(api_key="unit-test-only"))
    payload = provider._payload(messages, data, _capabilities(FULL))
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()) <= 64 * 1024
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) < 32 * 1024
    assert data == original


@pytest.mark.parametrize("kind", ["ATHLETE_SETTINGS", "PLANNING_PROFILE", "MESOCYCLE_PREFERENCES"])
def test_editable_app_form_inputs_work_with_edit_permission_without_recovery(repository, monkeypatch, kind):
    from apps.api import assistant_app_actions
    reader = Mock(return_value={"availability": "AVAILABLE", "kind": kind, "revision": 3, "payload": {}})
    monkeypatch.setattr(assistant_app_actions, "read_input", reader)
    rights = {"can_edit_plan": True, "can_view_plan": False, "can_view_recovery": False}
    result = context.read(repository, ALIAS, rights, {"kind": "inputs", "input_kind": kind}, now=NOW)
    assert result["revision"] == 3 and result["input_kind"] == kind
    reader.assert_called_once_with(repository, ALIAS, kind, None)


@pytest.mark.parametrize("rights,allowed", [
    ({"can_view_recovery": True}, True), ({"can_view_plan": True, "can_edit_plan": True}, True),
    ({"can_view_plan": True}, False), ({"can_edit_plan": True}, False), ({}, False)])
def test_sync_input_has_same_combined_app_access_before_data_is_read(repository, monkeypatch, rights, allowed):
    from apps.api import assistant_app_actions
    reader = Mock(return_value={"availability": "AVAILABLE", "kind": "SYNC_DATA", "revision": 0, "payload": {}})
    monkeypatch.setattr(assistant_app_actions, "read_input", reader)
    query = {"kind": "inputs", "input_kind": "SYNC_DATA"}
    if allowed:
        assert context.read(repository, ALIAS, rights, query, now=NOW)["input_kind"] == "SYNC_DATA"
        reader.assert_called_once()
    else:
        with pytest.raises(HTTPException) as error:
            context.read(repository, ALIAS, rights, query, now=NOW)
        assert error.value.status_code == 403
        reader.assert_not_called()
        repository.athlete_settings.assert_not_called()


@pytest.mark.parametrize("rights,allowed", [
    ({"can_view_recovery": True}, True), ({"can_view_plan": True, "can_edit_plan": True}, True),
    ({"can_view_plan": True}, False), ({"can_edit_plan": True}, False)])
def test_sync_state_read_applies_same_permissions_as_app_without_requiring_plan_view(repository, monkeypatch, rights, allowed):
    reader = Mock(return_value={"data": {"state": "IDLE"}})
    monkeypatch.setattr(context.assistant_reads, "read", reader)
    query = {"kind": "management", "view": "sync-state"}
    if allowed:
        assert context.read(repository, ALIAS, rights, query, now=NOW)["data"]["state"] == "IDLE"
        reader.assert_called_once()
    else:
        with pytest.raises(HTTPException) as error:
            context.read(repository, ALIAS, rights, query, now=NOW)
        assert error.value.status_code == 403
        reader.assert_not_called()
        repository.athlete_settings.assert_not_called()


@pytest.mark.parametrize('status,active', [('ACTIVE', True), ('REVIEW_REQUIRED', False), ('PAUSED', False), ('COMPLETED', False)])
def test_saved_day_stays_readable_while_current_execution_status_is_truthful(repository, status, active):
    saved = plan()
    saved['payload']['status'] = status
    saved['payload']['reason'] = 'Изчаква се актуална оценка на възстановяването.'
    repository.plan_read.side_effect = lambda _: saved
    result = context.read(repository, ALIAS, FULL, {'kind': 'day'}, now=NOW)
    assert result['availability'] == 'AVAILABLE' and len(result['sessions']) == 2
    assert result['prescription_active'] is active
    assert result['plan_status_detail'] == saved['payload']['reason']
    assert result['kind'] == 'SAVED_PLAN'


def test_training_proposal_preview_is_read_only_and_available_with_plan_read_permission(repository, monkeypatch):
    from apps.api import training_proposals
    preview = Mock(return_value={'availability': 'AVAILABLE', 'kind': 'TRAINING_PROPOSAL',
        'entry_key': 'management-profile-v1', 'revision': 7, 'payload': {}, 'preview': {}})
    monkeypatch.setattr(training_proposals, 'preview', preview)
    proposal = {'action': 'SESSION_METHOD', 'target_date': '2026-11-11', 'session_index': 1, 'method_ids': ['R04']}
    result = context.read(repository, ALIAS, PLAN, {'kind': 'inputs', 'input_kind': 'TRAINING_PROPOSAL',
        'proposal_json': json.dumps(proposal)}, now=NOW)
    preview.assert_called_once_with(repository, ALIAS, proposal)
    assert result['input_kind'] == 'TRAINING_PROPOSAL' and result['revision'] == 7
    assert '$defs' in result['input_contract'] and result['today'] == '2026-11-11'
    repository._request.assert_not_called()


def test_training_proposal_requires_plan_permission_before_reading_any_state(repository, monkeypatch):
    from apps.api import training_proposals
    preview = Mock()
    monkeypatch.setattr(training_proposals, 'preview', preview)
    with pytest.raises(HTTPException) as error:
        context.read(repository, ALIAS, {**FULL, 'can_view_plan': False},
            {'kind': 'inputs', 'input_kind': 'TRAINING_PROPOSAL'}, now=NOW)
    assert error.value.status_code == 403
    preview.assert_not_called()
    repository.athlete_settings.assert_not_called()


@pytest.mark.parametrize('raw', ['[]', '{}', 'null', '{broken', 'а' * 13000])
def test_training_proposal_rejects_invalid_payload_before_canonical_read(repository, monkeypatch, raw):
    from apps.api import training_proposals
    preview = Mock()
    monkeypatch.setattr(training_proposals, 'preview', preview)
    with pytest.raises(HTTPException) as error:
        context.read(repository, ALIAS, FULL, {'kind': 'inputs', 'input_kind': 'TRAINING_PROPOSAL',
            'proposal_json': raw}, now=NOW)
    assert error.value.status_code == 422
    preview.assert_not_called()


def test_week_summary_does_not_expand_full_blocks_that_are_not_returned(repository, monkeypatch):
    monkeypatch.setattr(context, "_session", Mock(side_effect=AssertionError("Full session projection is unnecessary")))
    result = context.read(repository, ALIAS, FULL, {"kind": "management", "view": "week"}, now=NOW)
    assert result["data"]["plan"]["days"][0]["sessions"][0]["total_minutes"] == 21
    repository.plan_read.assert_called_once_with(ALIAS, schedule_only=True)
    assert "blocks" not in result["data"]["plan"]["days"][0]["sessions"][0]
