"""Realistic-volume synthetic planning contexts retain useful strategy facts."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from apps.api import assistant_context, assistant_reads, assistant_strategy


FULL = {"is_owner": True, "can_view_plan": True, "can_edit_plan": True, "can_view_recovery": True}
PLAN = {"can_view_plan": True, "can_view_recovery": False}
NOW = datetime(2026, 11, 12, 20, tzinfo=timezone.utc)
PRIVATE = "PRIVATE_MEASUREMENT_NO_STRATEGY_REASON"


def sample():
    first = date(2026, 11, 12)
    events = [{"event_id": "A1", "event_type": "MAIN_RACE", "name": "Main goal", "start_date": "2027-02-05", "end_date": "2027-02-06"},
        {"event_id": "A2", "event_type": "MAIN_RACE", "name": "Second goal", "start_date": "2027-03-07", "end_date": "2027-03-07"}]
    weeks = [{"start_date": (first + timedelta(days=7*i)).isoformat(), "end_date": (first + timedelta(days=7*i+6)).isoformat(),
        "phases": ["GENERAL_PREPARATION" if i < 10 else "SPECIAL_PREPARATION"], "accents": ["Z2", "Z3"],
        "volume_budget_minutes": 600 + 10*i, "cycle": {"kind": "RECOVERY" if i % 4 == 3 else "BUILD"},
        "components": {z: {"target_weekly_q": i + 20, "target_index_7_40": 1.5, "duplicated_detail": PRIVATE * 500} for z in assistant_strategy.COMPONENTS}}
        for i in range(52)]
    outlook = {"profile_revision": 4, "source": {"generation_id": "g-1", "revision": 21, "as_of": "2026-11-11"},
        "input_snapshot": {"calendar": {"events": events}}, "generated_at": "2026-11-12T12:00:00Z",
        "periodization": {"phases": [{"kind": "GENERAL_PREPARATION", "start_date": "2026-11-12", "end_date": "2027-01-20", "reason": "Prepare for A1"}],
            "calendar_context": events, "next_main_race": events[0],
            "taper_windows": [{"kind": "TAPER", "start_date": "2027-01-29", "end_date": "2027-02-04", "volume_factor": .7, "prevent_deficit_refill": True, "reason": "Do not refill planned taper"}]},
        "long_term": {"weeks": weeks, "goal_role": "COACH_TARGETS_BEFORE_DAILY_GATES", "readiness_forecast": False,
            "baseline": {"Z2": {"c40": 30, "known": True}}, "raw_observations": PRIVATE * 2000},
        "volume_context": {"baseline_weekly_minutes": 650, "weekly_volume_source": "ACTUAL_HISTORY"},
        "history_comparison": [{"start_date": "2026-11-02", "end_date": "2026-11-08", "covered_days": 7, "actual_minutes": 630}],
        "component_history": PRIVATE * 1000}
    return {"revision": 21, "profile": {"configured": True, "revision": 4, "profile": {
        "sport": "NordicSki", "actual_sport": "RollerSki", "discipline": "Biathlon", "program_start": "2026-11-12", "program_end": "2027-03-10",
        "planning_controls": {"wave": [.96, 1.04, 1.1, .78], "sessions_per_week": 12, "double_threshold_days": [2]},
        "lactate_profiles": [{"large_observations": PRIVATE * 2000}]}},
        "outlook": {"configured": True, "outlook": outlook},
        "overview_active": {"revision": 8, "status": "REVIEW_REQUIRED", "has_proposal": True, "days": [
            {"date": "2026-11-13", "status": "TRAINING", "sessions": [{"title": "Intervals", "sport": "NordicSki", "total_minutes": 90,
                "blocks": [{"duration_min": 1, "details": PRIVATE * 100} for _ in range(100)]}]}]}}


def repo(payload=None):
    return SimpleNamespace(athlete_settings=Mock(return_value=SimpleNamespace(timezone="Europe/Sofia")),
        prepared_view=Mock(return_value={"ready": True, "athlete_alias": "test-athlete", "kind": "management-view", "payload": payload or sample()}),
        active_analysis=Mock(side_effect=AssertionError("No model read/recalculation")))


def test_default_owner_overview_is_semantically_bounded_and_retains_all_horizon_groups():
    source = sample()
    original = deepcopy(source)
    repository = repo(source)
    result = assistant_context.read(repository, "test-athlete", FULL, {"kind": "management"}, now=NOW)
    assert result["data"]["revision"] == 21
    outlook = result["data"]["outlook"]["outlook"]
    assert outlook["source"]["revision"] == 21
    assert outlook["periodization"]["main_races"]["events"] == source["outlook"]["outlook"]["periodization"]["calendar_context"]
    assert outlook["periodization"]["taper_windows"][0]["volume_factor"] == .7
    assert outlook["long_term"]["week_coverage"]["total"] == 52
    assert len(outlook["long_term"]["weeks"]) == 4
    assert [row["week_count"] for row in outlook["long_term"]["phase_ranges"]] == [10, 42]
    assert outlook["long_term"]["phase_ranges"][-1]["end_date"] == source["outlook"]["outlook"]["long_term"]["weeks"][-1]["end_date"]
    assert result["data"]["overview_active"]["has_proposal"] is True
    assert result["data"]["overview_active"]["days_basis"] == "PROPOSAL_NOT_APPROVED"
    assert result["data"]["overview_active"]["days"][0]["sessions"][0]["total_minutes"] == 90
    assert outlook["recent_actual_weeks"][0]["actual_minutes"] == 630
    assert len(json.dumps(result, ensure_ascii=False).encode()) < 16000
    assert PRIVATE not in json.dumps(result)
    assert source == original
    repository.active_analysis.assert_not_called()


def test_plan_only_summary_keeps_taper_reason_and_targets_but_no_actual_evidence():
    result = assistant_context.read(repo(), "test-athlete", PLAN, {"kind": "management"}, now=NOW)
    outlook = result["data"]["outlook"]["outlook"]
    assert "baseline" not in outlook["long_term"] and "recent_actual_weeks" not in outlook
    assert outlook["periodization"]["taper_windows"][0]["reason"] == "Do not refill planned taper"
    assert outlook["long_term"]["weeks"][0]["components"]["Z2"]["target_weekly_q"] == 20
    assert PRIVATE not in json.dumps(result)


def test_explicit_sections_keep_exact_rows_beyond_summary_and_original_index_paging():
    source = sample()
    repository = repo(source)
    result = assistant_context.read(repository, "test-athlete", FULL,
        {"kind": "management", "section": "outlook.outlook.long_term.weeks", "offset": 40, "limit": 3}, now=NOW)
    assert result["data"][0] == source["outlook"]["outlook"]["long_term"]["weeks"][40]
    assert result["pagination"][0]["total"] == 52
    assert result["pagination"][0]["next_offset"] == 43


@pytest.mark.parametrize("access", [FULL, PLAN])
def test_strategy_uses_current_inputs_and_labels_prepared_revision_mismatch(monkeypatch, access):
    source = sample()
    repository = repo(source)
    fresh_profile = {"configured": True, "revision": 5, "profile": {"sport": "NordicSki", "discipline": "New goal"}}
    monkeypatch.setattr("apps.api.management_store.ManagementStore.profile", lambda *_: fresh_profile)
    event = {"event_id": "A1", "event_type": "MAIN_RACE", "name": "Moved goal", "start_date": "2027-02-12", "end_date": "2027-02-12"}
    repository.athlete_planning_calendar_snapshot = Mock(return_value={"revision": 12, "calendar": {"events": [event]}})
    result = assistant_context.read(repository, "test-athlete", access, {"kind": "management", "view": "strategy"}, now=NOW)
    assert result["data"]["profile"]["revision"] == 5
    assert result["data"]["current_calendar"]["revision"] == 12
    assert result["data"]["current_calendar"]["main_races"]["events"][0] == event
    assert result["data"]["prepared_basis"]["matches_current_profile"] is False
    assert result["data"]["prepared_basis"]["matches_current_calendar"] is False
    assert PRIVATE not in json.dumps(result)
    repository.active_analysis.assert_not_called()


def test_strategy_current_goals_survive_cold_prepared_view(monkeypatch):
    repository = repo()
    monkeypatch.setattr(assistant_reads, "_prepared", lambda *_: {"availability": "PENDING", "reason": "PREPARED_VIEW_PENDING"})
    monkeypatch.setattr("apps.api.management_store.ManagementStore.profile", lambda *_: {"configured": True, "revision": 6, "profile": {"sport": "Run", "discipline": "1500m"}})
    repository.athlete_planning_calendar_snapshot = Mock(return_value={"revision": 0, "calendar": None})
    result = assistant_context.read(repository, "test-athlete", FULL, {"kind": "management", "view": "strategy"}, now=NOW)
    assert result["availability"] == "AVAILABLE"
    assert result["data"]["profile"]["profile"]["discipline"] == "1500m"
    assert result["data"]["prepared_availability"] == "PENDING"
    assert "outlook" not in result["data"]
    compact = assistant_strategy.compact_management_context(result)
    assert compact["data"]["current_calendar"] == result["data"]["current_calendar"]
    assert compact["data"]["prepared_availability"] == "PENDING"


def test_strategy_still_requires_plan_rights_before_current_input_reads():
    repository = repo()
    with pytest.raises(HTTPException) as error:
        assistant_context.read(repository, "test-athlete", {"can_view_recovery": True}, {"kind": "management", "view": "strategy"}, now=NOW)
    assert error.value.status_code == 403
    repository.athlete_settings.assert_not_called()


def test_budget_recovery_is_pure_idempotent_and_preserves_context_identity():
    context = {"query_kind": "management", "source": "SAVED_APPLICATION_VIEW", "revision": 21,
        "timezone": "Europe/Sofia", "data": sample()}
    original = deepcopy(context)
    result = assistant_strategy.compact_management_context(context)
    assert result["revision"] == 21 and result["source"] == context["source"]
    assert assistant_strategy.compact_management_context(result) == result
    assert context == original


def test_aggregate_unknown_values_do_not_become_zero_or_full_coverage():
    weeks = [{"start_date": "2026-11-12", "end_date": "2026-11-18", "phases": ["GENERAL_PREPARATION"],
        "components": {"Z1": {"target_weekly_q": None}}},
        {"start_date": "2026-11-19", "end_date": "2026-11-25", "phases": ["GENERAL_PREPARATION"],
        "components": {"Z1": {"target_weekly_q": 25}}}]
    result = assistant_strategy._phase_ranges(weeks)[0]["components"]["Z1"]
    assert result["target_weekly_q_range"] == [25, 25]
    assert result["unknown_q_weeks"] == 1 and result["unknown_index_weeks"] == 2


def test_strategy_preserves_actual_return_dates_and_component_growth_not_only_config():
    profile = assistant_strategy._profile({"training_return": {"mode": "CONFIRMED_BREAK", "pause_start": "2026-10-01", "resume_date": "2026-11-01", "historical_weekly_q": {"Z1": 100}}})
    assert profile["training_return"]["pause_start"] == "2026-10-01"
    assert profile["training_return"]["resume_date"] == "2026-11-01"
    result = assistant_strategy._long_term({"weeks": [], "progression": {"history_usable": True,
        "trajectory": PRIVATE, "components": {"Z1": {"annual_rate_percent": 8, "governed_annual_rate_percent": 6, "raw_samples": PRIVATE}}}})
    assert result["progression"]["components"]["Z1"]["governed_annual_rate_percent"] == 6
    assert PRIVATE not in json.dumps(result)


def test_volume_range_carries_its_historical_mix_role():
    result = assistant_strategy._phase_ranges([{"start_date": "2026-11-12", "end_date": "2026-11-18", "volume_budget_minutes": 600,
        "volume_role": "HISTORICAL_MIX_EQUIVALENT_NOT_TIME_LIMIT"}])
    assert result[0]["volume_roles"] == ["HISTORICAL_MIX_EQUIVALENT_NOT_TIME_LIMIT"]


def test_recovery_of_already_paged_context_never_claims_full_horizon_ranges():
    payload = sample()
    payload["outlook"]["outlook"]["long_term"]["weeks"] = payload["outlook"]["outlook"]["long_term"]["weeks"][:30]
    context = {"query_kind": "management", "data": payload, "pagination": [{"section": "outlook.outlook.long_term.weeks",
        "offset": 0, "total": 52, "returned": 30, "next_offset": 30}]}
    result = assistant_strategy.compact_management_context(context)
    coverage = result["data"]["outlook"]["outlook"]["long_term"]["week_coverage"]
    assert coverage["total"] == 52 and coverage["source_rows"] == 30
    assert "not the whole saved horizon" in coverage["range_scope"]
    assert result["data"]["source_page_limits"] == context["pagination"]
    assert assistant_strategy.compact_management_context(result) == result


def test_current_strategy_prioritizes_next_goal_over_large_past_archive(monkeypatch):
    repository = repo()
    monkeypatch.setattr("apps.api.management_store.ManagementStore.profile", lambda *_: {"configured": False, "revision": 0, "profile": None})
    past = [{"event_id": f"past-{i}", "event_type": "MAIN_RACE", "name": "Archive", "start_date": "2025-01-01", "end_date": "2025-01-01"} for i in range(50)]
    future = {"event_id": "next", "event_type": "MAIN_RACE", "name": "Next", "start_date": "2027-01-01", "end_date": "2027-01-01"}
    repository.athlete_planning_calendar_snapshot = Mock(return_value={"revision": 1, "calendar": {"events": past + [future]}})
    result = assistant_context.read(repository, "test-athlete", FULL, {"kind": "management", "view": "strategy"}, now=NOW)
    calendar = result["data"]["current_calendar"]
    assert calendar["next_main_race"] == future
    assert calendar["main_races"]["events"][0] == future
    assert calendar["main_races"]["omitted_events"] == 11
