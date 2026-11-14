from copy import deepcopy
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException
import pytest

from apps.api import assistant_context
from apps.api.assistant_activity_summary import compact_context
from apps.api.assistant_provider import OpenAIAssistantProvider, OpenAIProviderConfig


DAY = date(2026, 11, 14)
FULL = {"can_view_recovery": True, "can_view_plan": True, "can_edit_plan": True, "is_owner": True}


def row(day, number):
    return {"activity_ref": "act_" + f"{number:032x}", "local_date": day,
            "start_at_utc": day + "T09:00:00Z", "start_local": day + "T10:00:00",
            "sport": "Run", "name": "Контролно", "moving_time_s": 160,
            "distance_m": 1000, "quality_status": "limited", "quality_reason": "HR_GAPS",
            "canonical_summary": {"zones": []}}


def read(query, rows=None):
    repo = SimpleNamespace(athlete_settings=Mock(return_value=SimpleNamespace(timezone="Europe/Vienna")),
        active_activity_calendar=Mock(return_value={"revision": 9, "activities": rows or [],
            "snapshot_payload": {"wellness_calendar": [{"date": str(DAY), "diagnostics": "x" * 70000}]}}))
    result = assistant_context.read(repo, "ath-test", FULL, {"kind": "activities", **query},
                                    now=datetime(2026, 11, 14, 12, tzinfo=timezone.utc))
    return repo, result


def test_specific_day_filters_before_paging_and_excludes_unrelated_wellness():
    repo, result = read({"date": str(DAY)}, [row("2026-11-13", 1), row(str(DAY), 2)])
    repo.active_activity_calendar.assert_called_once_with("ath-test", DAY, DAY)
    assert result["data"]["activity_count"] == 1
    actual = result["data"]["activities"][0]
    assert actual["distance_m"] == 1000 and actual["duration_min"] == pytest.approx(160 / 60)
    assert actual["quality_status"] == "limited" and actual["quality_reason"] == "HR_GAPS"
    assert result["revision"] == 9 and "wellness_days" not in result["data"]
    assert "activity_ref" in actual


@pytest.mark.parametrize("query,start,end", [
    ({"date": "2026-11-13"}, "2026-11-13", "2026-11-13"),
    ({"start": "2026-11-01", "end": "2026-11-07"}, "2026-11-01", "2026-11-07"),
    ({"days": 1}, "2026-11-14", "2026-11-14"),
    ({"days": 7, "end": "2026-11-13"}, "2026-11-07", "2026-11-13"),
    ({}, "2026-11-01", "2026-11-14"),
])
def test_period_selectors_are_exact(query, start, end):
    repo, result = read(query)
    repo.active_activity_calendar.assert_called_once_with("ath-test", date.fromisoformat(start), date.fromisoformat(end))
    assert result["data"]["activity_count"] == 0
    assert result["data"]["period_start"] == start and result["data"]["period_end"] == end


@pytest.mark.parametrize("query", [
    {"date": "2026-11-15"}, {"date": "invalid"}, {"date": str(DAY), "start": str(DAY)},
    {"date": str(DAY), "end": str(DAY)}, {"date": str(DAY), "days": 1},
    {"days": True}, {"days": 0}, {"days": 91}, {"days": "7"}, {"days": 1, "start": str(DAY)},
    {"start": "2026-11-14", "end": "2026-11-13"}, {"view": "unknown"},
])
def test_invalid_or_conflicting_selectors_are_rejected(query):
    with pytest.raises(HTTPException) as error:
        read(query)
    assert error.value.status_code == 422


def test_calendar_and_explicit_sections_remain_accessible():
    _, calendar = read({"date": str(DAY), "view": "calendar"})
    _, selected = read({"date": str(DAY), "section": "wellness_days"})
    assert calendar["data"]["wellness_days"] == selected["data"]


def test_compaction_keeps_exact_pagination_and_does_not_touch_write_contracts():
    source = {"query_kind": "activities", "revision": 9, "data": {
        "activities": [row(str(DAY), n) for n in range(10)], "wellness_days": ["x" * 100000]},
        "pagination": [{"section": "activities", "total": 100, "offset": 30, "returned": 10, "next_offset": 40}]}
    before = deepcopy(source)
    result = compact_context(source)
    assert len(result["data"]["activities"]) == 5 and result["revision"] == 9
    assert result["pagination"] == [{"section": "activities", "total": 100, "offset": 30, "returned": 5, "next_offset": 35}]
    assert source == before
    contract = {"query_kind": "inputs", "payload": {"secret_contract": "x" * 100000}}
    assert compact_context(contract) is contract


def test_two_race_day_activities_and_long_history_fit_funded_payload_limit():
    _, result = read({"date": str(DAY)}, [row(str(DAY), 1), row(str(DAY), 2)])
    from apps.api.assistant_service import _capabilities
    provider = OpenAIAssistantProvider(OpenAIProviderConfig(api_key="unused"))
    messages = [{"role": "assistant", "content": "Стара справка. " * 3000},
                {"role": "user", "content": "Провери днешното състезание и подготовката за утрешния старт."}]
    payload = provider._payload(messages, {"results": [result],
        "completed_queries": [{"kind": "activities", "date": str(DAY)}]}, _capabilities(FULL))
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()) <= 64 * 1024
    assert len(result["data"]["activities"]) == 2
