from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException
import pytest

from apps.api import assistant_context, assistant_history
from apps.api.assistant_provider import OpenAIAssistantProvider, OpenAIProviderConfig
from apps.api.oauth_store import PersistentStoreFailure
from apps.api.real_service import completed_work_from_load_history
from apps.api.schemas import LoadHistoryResponse

TODAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)
ALIAS = "ath-history-test"
ACCESS = {"can_view_recovery": True}


def history():
    days = [(TODAY - timedelta(days=n)).isoformat() for n in range(90)]
    return {"schema_version": "load-history-v1", "athlete_id": ALIAS,
        "period_start": days[-1], "period_end": days[0],
        "quality": {"processed_activities": 180, "limited_activities": 100, "excluded_activities": 2,
            "no_activity_days": 0, "warnings": ["A warning for the whole 90-day source"]},
        "zones": [], "daily": [{"date": day, "zone": f"Z{z}", "effective_load": 9999,
            "e7_daily": 100, "e40_daily": 100, "status_7_40": 1} for day in days for z in range(1, 6)],
        "activities": [{"activity_ref": f"act_{n:032x}", "date": day, "sport": "Run", "duration_min": 75,
            "quality_status": "limited" if n % 3 == 0 else "valid", "hr_coverage_percent": 99,
            "zones": [{"zone": f"Z{z}", "raw_time_min": 30 if z == 1 else 5,
                "equivalent_time_min": 15 if z == 1 else 3, "effective_load": 300 if z == 1 else 200,
                "mean_effective_hr_bpm": None, "average_minute_value_percent": None} for z in range(1, 6)]}
            for n, day in enumerate(days * 2)]}


@pytest.fixture
def repo():
    envelope = {"snapshot_payload": {"load_history": history()}, "analysis_as_of": TODAY.isoformat(),
        "revision": 7, "generation_id": "saved-generation"}
    return SimpleNamespace(athlete_settings=lambda _: SimpleNamespace(timezone="Europe/Sofia"),
        active_analysis=Mock(side_effect=lambda _: deepcopy(envelope)))


def read(repo, **query):
    return assistant_context.read(repo, ALIAS, ACCESS, {"kind": "history", **query}, now=NOW)


def test_month_report_matches_canonical_status_report_and_does_not_sum_effective_load(repo):
    result = read(repo, days=30)
    expected = completed_work_from_load_history(LoadHistoryResponse.model_validate(history()), date(2026, 10, 15), TODAY)
    assert result["start"] == "2026-10-15" and result["end"] == "2026-11-13"
    assert result["period_coverage"]["full_requested_range"] is True
    for actual, canonical in zip(result["report"]["zones"], expected.zones):
        assert actual["raw_time_min"] == canonical.raw_time_min
        assert actual["raw_time_hours"] == canonical.raw_time_min / 60
        assert actual["equivalent_time_hours"] == canonical.equivalent_time_min / 60
        assert actual["effective_load"] == canonical.effective_load
    assert result["report"]["zones"][0]["raw_time_hours"] == 30
    assert result["report"]["zones"][0]["equivalent_time_hours"] == 15
    assert result["report"]["quality"]["modeled_activities"] == 60
    assert result["report"]["quality"]["limited_activities"] == 20
    assert "A warning for the whole" not in json.dumps(result)
    assert "daily" not in result and "activities" not in result


@pytest.mark.parametrize("days", [1, 7, 14, 30, 60, 90])
def test_report_windows_are_inclusive_and_totals_do_not_depend_on_pagination(repo, days):
    result = read(repo, days=days, offset=30, limit=1)
    assert result["requested_days"] == days
    assert result["report"]["zones"][0]["raw_time_hours"] == days
    assert result["report"]["quality"]["modeled_activities"] == days * 2


def test_explicit_dates_override_window_and_can_select_a_calendar_month(repo):
    result = read(repo, start="2026-10-13", end="2026-11-13", days=30)
    assert result["requested_days"] == 32 and result["report"]["zones"][0]["raw_time_hours"] == 32
    previous = read(repo, start="2026-10-01", end="2026-10-31")
    assert previous["requested_days"] == 31 and previous["report"]["zones"][0]["raw_time_hours"] == 31


@pytest.mark.parametrize("days", [0, 91, True, "30", 1.5])
def test_invalid_window_is_rejected_before_reading_private_data(repo, days):
    with pytest.raises(HTTPException) as caught:
        read(repo, days=days)
    assert caught.value.status_code == 422
    repo.active_analysis.assert_not_called()


def test_partial_source_never_claims_a_full_month_or_invents_missing_time(repo):
    data = history()
    data["period_start"] = "2026-10-31"
    data["activities"] = [a for a in data["activities"] if a["date"] >= "2026-10-31"]
    data["daily"] = [a for a in data["daily"] if a["date"] >= "2026-10-31"]
    repo.active_analysis.side_effect = lambda _: {"snapshot_payload": {"load_history": data}}
    result = read(repo, days=30)
    assert not result["period_coverage"]["full_requested_range"]
    assert result["start"] == "2026-10-15" and result["report"]["period_start"] == "2026-10-31"
    assert result["report"]["zones"][0]["raw_time_hours"] == 14
    absent = read(repo, start="2026-09-01", end="2026-09-30")
    assert absent["availability"] == "UNAVAILABLE" and "report" not in absent


def test_cold_preparation_reads_saved_history_without_calculating_current_models(repo):
    repo.dashboard_preparation = Mock(return_value={"ready": False})
    result = read(repo, days=30)
    assert result["report"]["zones"][0]["raw_time_hours"] == 30
    assert result["preparation"]["status"] == "UPDATING" and result["analysis_revision"] == 7
    repo.dashboard_preparation.assert_called_once()
    assert repo.dashboard_preparation.call_args.args == ("READ",)
    repo.active_analysis.assert_called_once_with(ALIAS)


def test_history_privacy_and_scope_checks_precede_every_read(repo):
    with pytest.raises(HTTPException) as caught:
        assistant_context.read(repo, ALIAS, {"can_view_plan": True}, {"kind": "history", "days": 30}, now=NOW)
    assert caught.value.status_code == 403
    repo.active_analysis.assert_not_called()
    data = history()
    data["athlete_id"] = "another-athlete"
    repo.dashboard_preparation = Mock(return_value={"ready": False})
    repo.active_analysis.side_effect = lambda _: {"snapshot_payload": {"load_history": data}}
    with pytest.raises(PersistentStoreFailure):
        read(repo, days=30)


def test_empty_saved_period_is_zero_while_missing_source_is_pending(repo):
    data = history()
    data["activities"] = []
    repo.active_analysis.side_effect = lambda _: {"snapshot_payload": {"load_history": data}}
    assert read(repo, days=30)["report"]["zones"][0]["raw_time_hours"] == 0
    repo.dashboard_preparation = Mock(return_value={"ready": False})
    repo.active_analysis.side_effect = None
    repo.active_analysis.return_value = None
    assert read(repo, days=30)["availability"] == "PENDING"


def test_large_history_is_a_small_model_report_and_details_remain_paged(repo):
    result = read(repo, days=90)
    assert len(json.dumps(result).encode()) < 6000
    assert "athlete_id" not in result["report"]
    context = {"results": [result], "completed_queries": [{"kind": "history", "days": 90}], "read_only_turn": True}
    provider = OpenAIAssistantProvider(OpenAIProviderConfig(api_key="unused-test-key"))
    payload = provider._payload([{"role": "assistant", "content": "старо обяснение " * 20000},
        {"role": "user", "content": "Обем по зони за последните 90 дни в часове"}], context, {"read": ["history"]})
    assert len(json.dumps(payload, ensure_ascii=False).encode()) <= 65536
    details = read(repo, days=90, view="activities", section="activities", limit=10, offset=10)
    assert len(details["data"]) == 10 and details["pagination"][0]["total"] == 180
    assert details["pagination"][0]["next_offset"] == 20
    assert details["data"][0]["zones"][0]["raw_time_min"] == 30
    daily = read(repo, days=90, view="daily", section="daily", limit=10)
    assert daily["pagination"][0]["total"] == 450


def test_summary_is_pure_reporting_and_preserves_saved_source():
    source = history()
    original = deepcopy(source)
    assistant_history.summary(source, TODAY - timedelta(days=29), TODAY)
    assert source == original


def test_sport_filter_is_applied_to_the_same_canonical_report_and_not_ignored(repo):
    source = history()
    for index, activity in enumerate(source["activities"]):
        activity["sport"] = "Run" if index % 2 else "NordicSki"
    repo.active_analysis.side_effect = lambda _: {"snapshot_payload": {"load_history": source}}
    result = read(repo, days=30, sport="NordicSki")
    assert result["sport"] == "NordicSki" and result["report"]["zones"][0]["raw_time_hours"] == 15
    assert [r["sport"] for r in result["report"]["sports"]] == ["NordicSki"]
    unknown = read(repo, days=30, sport="invented")
    assert unknown["availability"] == "UNAVAILABLE" and "report" not in unknown
    with pytest.raises(HTTPException):
        read(repo, days=30, sport="NordicSki", view="daily")
