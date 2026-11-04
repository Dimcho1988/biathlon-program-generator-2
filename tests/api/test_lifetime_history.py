from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.api import lifetime_history, prepared_learning
from apps.api.oauth_store import PersistentStoreFailure
from biathlon.constants import COMPONENTS
from tests.api.test_recovery_policy import fixture


def records():
    data = fixture()
    source = data["source"]
    result = []
    for strength in source["strength"]["daily"]:
        day = strength["date"]
        result.append({"day": day, "source_revision": 7, "source_generation_id": "synthetic",
            "payload": {"schema_version": "history-day-input-v1",
                "activities": [a for a in source["activities"] if a["date"] == day],
                "daily": [r for r in data["rows"] if r["date"] == day and r["zone"] != "STR"],
                "strength": {**strength,"effective_load":0.}, "catalog": [], "wellness": None, "exclusions": []}})
    return data, result


def test_year_preparation_keeps_original_recent_projection_and_decision(monkeypatch):
    from apps.api.response_service import ResponseStore
    data, stored = records()
    reader = Mock(return_value=stored)
    repo = SimpleNamespace(history_day_inputs=reader)
    monkeypatch.setattr(ResponseStore, "entries", lambda *args: [])
    last = stored[-1]["day"]
    current = {"as_of": data["today"].isoformat(), "series": [{"day": last, "q3": 999.,
        "actual_q": 111., "q3_by_component": dict.fromkeys(COMPONENTS, 1.)}],
        "decision": {"component": "Z1", "proposed_change_percent": 5.}, "components": [{"component": "Z1"}]}
    original = deepcopy(current)
    report = lifetime_history.extend_report(repo, "athlete", current)
    assert report["decision"] == original["decision"]
    assert report["components"] == original["components"]
    assert report["history_version"] == lifetime_history.VERSION
    assert next(r for r in report["series"] if r["day"] == last)["q3"] == 999.
    assert report["series"][0]["day"] == stored[0]["day"]
    assert len(report["series"]) == len(stored)
    point = report["series"][3]
    assert point["q3_by_component"]["Z1"] == 10.
    assert point["q3_observed_days"]["Z1"] == 3
    assert point["source_input_revision"] == 7
    assert report["series"][0]["q3_observed_days"]["Z1"] == 1
    first, end = reader.call_args.args[1:]
    assert end == data["today"] and (end-first).days == 449


def test_archived_gap_stays_unknown_instead_of_synthesized_rest(monkeypatch):
    from apps.api.response_service import ResponseStore
    data, stored = records()
    missing = stored.pop(4)["day"]
    monkeypatch.setattr(ResponseStore, "entries", lambda *args: [])
    report = lifetime_history.extend_report(SimpleNamespace(history_day_inputs=lambda *a: stored),
        "athlete", {"as_of": data["today"].isoformat(), "series": []})
    assert missing not in {r["day"] for r in report["series"]}
    next_day = (date.fromisoformat(missing)+timedelta(days=1)).isoformat()
    assert next(r for r in report["series"] if r["day"] == next_day)["q3_observed_days"]["Z1"] == 2


def test_history_reconstruction_preserves_original_fields_and_unidentified_exclusion():
    _, stored = records()
    stored[0]["payload"]["exclusions"] = [{"reason": "missing unlocated activity"}]
    original = deepcopy(stored)
    source, _, _, revisions = lifetime_history.source_from_days(stored)
    assert source["history_uncertain_days"] == [stored[0]["day"]]
    assert source["activities"][0] == original[0]["payload"]["activities"][0]
    assert revisions[stored[0]["day"]] == 7 and stored == original


def test_duplicate_dates_and_unsupported_payloads_are_not_published():
    _, stored = records()
    with pytest.raises(PersistentStoreFailure):
        lifetime_history.source_from_days([stored[0], stored[0]])
    stored[0]["payload"]["schema_version"] = "unknown"
    with pytest.raises(PersistentStoreFailure):
        lifetime_history.source_from_days(stored)


def test_historical_selection_is_a_bounded_read_without_model_work():
    report = {"schema_version": "recovery-learning-view-v1", "as_of": "2026-11-04", "series": [
        {"day": "2026-01-01", "actual_q": 100}, {"day": "2026-01-14", "actual_q": 50}]}
    history_reader = Mock(return_value={"ready": True, "day": "2026-11-04",
        "payload": {"athlete_alias": "athlete", "report": report}})
    operational = Mock(side_effect=AssertionError("Wrong read path"))
    repo = SimpleNamespace(learning_preparation=operational, learning_history_view=history_reader)
    result = prepared_learning.read_view(repo, "athlete", days=14, end=date(2026, 1, 14))
    assert len(result["series"]) == 2
    history_reader.assert_called_once_with("athlete", days=14, end=date(2026, 1, 14), model_key=prepared_learning.model_key())
    operational.assert_not_called()
