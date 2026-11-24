"""Saved technical evidence stays available without expanding ordinary context."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from apps.api import assistant_context as context, assistant_service, assistant_provider
from tests.api.test_assistant_context import ALIAS, FULL, PLAN, NOW, plan, repository


@pytest.fixture
def saved(repository, monkeypatch):
    record = plan()
    current = record["payload"]["plan"]
    current.update(fingerprint="frozen-plan", parameters={"daily_intensity": {"limit": 1.},
        "individual_learning": {"memory": {"labs": "PRIVATE_OBSERVATION"}, "mode": "SHADOW"}},
        allocation={"segments": [{"components": {"Z5": {"coverage_percent": 87.5}}}],
            "search": {"conclusion": "SEARCH_BUDGET_EXHAUSTED", "hard_limit_proven": False}})
    day = current["days"][0]
    day["rejected_alternatives"] = [{"method_id": f"method-{n}", "code": "MINIMUM_STRUCTURE_NOT_MET"} for n in range(5)]
    day["load_budget"] = {"daily_intensity": {"used": .6, "limit": 1.}}
    day["daily_intensity_budget"] = {"used": .6, "limit": 1.}
    session = day["sessions"][0]
    session["dose_evidence"]["method_structure_contract"] = {"minimum_work_minutes": 12., "maximum_work_minutes": 20.}
    session["dose_evidence"]["selection"] = {"component_allocation": {"Z5": 7.}}
    session["method_library"]["source"]["url"] = "https://example.org/original-method"
    current["lookahead_days"] = [{**deepcopy(day), "date": "2026-11-18"}]
    repository.plan_read.side_effect = lambda alias, **kwargs: deepcopy(record)
    # These diagnostics must not replace a frozen decision with a fresh engine
    # result, model view or method catalog after a deploy.
    from apps.api import training_plan_engine
    monkeypatch.setattr(training_plan_engine, "generate_plan", Mock(side_effect=AssertionError("No recalculation")))
    monkeypatch.setattr(context.method_library, "entries", Mock(side_effect=AssertionError("No current catalog substitution")))
    return record


def read(repository, **changes):
    return context.read(repository, ALIAS, FULL, {"kind": "management", "view": "diagnostics", **changes}, now=NOW)


def test_default_is_a_small_index_with_saved_revision_and_separate_plan_roles(repository, saved):
    result = read(repository)
    assert result["revision"] == 7
    data = result["data"]
    assert data["basis"] == "FROZEN_SAVED_PLAN_DIAGNOSTICS"
    assert data["plan"]["plan_role"] == "CURRENT" and data["proposal"]["plan_role"] == "CANDIDATE"
    assert "plan.allocation" in data["plan"]["sections"]
    assert "plan.days.0.sessions.0.dose_evidence" in data["plan"]["days"][0]["sessions"][0]["sections"]
    assert "MINIMUM_STRUCTURE_NOT_MET" not in json.dumps(data)
    assert "method_structure_contract" not in json.dumps(data)
    assert "PRIVATE_OBSERVATION" not in json.dumps(data)
    repository.plan_read.assert_called_once_with(ALIAS)
    repository.active_analysis.assert_not_called()
    repository._request.assert_not_called()


@pytest.mark.parametrize("section", ["allocation", "allocation.search", "parameters.daily_intensity",
    "days.0.load_budget", "days.0.sessions.0.dose_evidence.method_structure_contract",
    "days.0.sessions.0.dose_evidence.selection", "days.0.sessions.0.method_library.source"])
def test_exact_saved_diagnostics_are_accessible_on_demand(repository, saved, section):
    expected = saved["payload"]["plan"]
    for part in section.split("."):
        expected = expected[int(part)] if isinstance(expected, list) else expected[part]
    result = read(repository, section="plan."+section, revision=7)
    assert result["revision"] == 7 and result["data"] == expected
    repository.plan_read.assert_called_once_with(ALIAS)
    repository.active_analysis.assert_not_called()


def test_rejected_methods_are_paged_without_loss_or_revision_mixing(repository, saved):
    first = read(repository, section="plan.days.0.rejected_alternatives", revision=7, limit=2)
    assert first["pagination"] == [{"section": "plan.days.0.rejected_alternatives", "total": 5,
        "offset": 0, "returned": 2, "next_offset": 2}]
    second = read(repository, section="plan.days.0.rejected_alternatives", revision=7, offset=2, limit=2)
    assert [row["method_id"] for row in first["data"]+second["data"]] == [f"method-{n}" for n in range(4)]
    saved["revision"] = 8
    with pytest.raises(HTTPException) as error:
        read(repository, section="plan.days.0.rejected_alternatives", revision=7, offset=4, limit=2)
    assert error.value.status_code == 409


def test_lookahead_constraints_and_frozen_sessions_remain_explainable(repository, saved):
    index = read(repository)["data"]["plan"]
    assert index["lookahead_days"][0]["date"] == "2026-11-18"
    assert "plan.lookahead_days.0.daily_intensity_budget" in index["lookahead_days"][0]["sections"]
    result = read(repository, section="plan.lookahead_days.0", revision=7)
    assert result["data"]["daily_intensity_budget"] == {"used": .6, "limit": 1.}
    assert result["data"]["sessions"][0]["session_id"] == "2026-11-18:1"
    assert result["data"]["sessions"][0]["dose_evidence"]["method_structure_contract"]["minimum_work_minutes"] == 12.


def test_index_never_projects_full_evidence(repository, saved, monkeypatch):
    from apps.api import assistant_plan_diagnostics
    monkeypatch.setattr(assistant_plan_diagnostics, "_plan", Mock(side_effect=AssertionError("Index must stay metadata-only")))
    assert read(repository)["data"]["plan"]["sections"]


def test_allocation_detail_does_not_expand_frozen_sessions(repository, saved, monkeypatch):
    monkeypatch.setattr(context, "_card", Mock(side_effect=AssertionError("No session projection for allocation")))
    result = read(repository, section="plan.allocation.search", revision=7)
    assert result["data"] == saved["payload"]["plan"]["allocation"]["search"]


def test_diagnostics_keep_existing_private_learning_projection_and_do_not_mutate_record(repository, saved):
    original = deepcopy(saved)
    result = read(repository, section="plan", revision=7)
    assert "PRIVATE_OBSERVATION" not in json.dumps(result)
    assert result["data"]["parameters"]["individual_learning"] == {"mode": "SHADOW"}
    assert "response_revisions" not in result["data"]["input_snapshot"]
    assert saved == original


@pytest.mark.parametrize("rights", [PLAN, {**FULL, "can_view_plan": False}, {}, {"can_edit_plan": True}])
def test_both_permissions_are_required_before_any_athlete_read(repository, rights):
    with pytest.raises(HTTPException) as error:
        context.read(repository, ALIAS, rights, {"kind": "management", "view": "diagnostics",
            "section": "plan.allocation"}, now=NOW)
    assert error.value.status_code == 403
    repository.athlete_settings.assert_not_called()
    repository.plan_read.assert_not_called()
    assert "diagnostics" not in assistant_service._capabilities(rights)["read_views"].get("management", [])


@pytest.mark.parametrize("selectors", [{"athlete_alias": "another-athlete"}, {"plan_kind": "DRAFT"},
    {"date": "2026-01-01"}, {"revision": True}, {"revision": -1}])
def test_diagnostics_never_ignore_scope_or_version_selectors(repository, selectors):
    with pytest.raises(HTTPException) as error:
        read(repository, **selectors)
    assert error.value.status_code == 422
    repository.plan_read.assert_not_called()


def test_missing_saved_plan_is_not_replaced_with_generation(repository):
    repository.plan_read.side_effect = lambda _: None
    result = read(repository)
    assert result["availability"] == "UNAVAILABLE" and result["reason"] == "NO_SAVED_PLAN"
    repository.active_analysis.assert_not_called()


def test_week_stays_compact_and_full_diagnostics_are_discoverable_only_for_authorized_reader(repository, saved):
    result = context.read(repository, ALIAS, FULL, {"kind": "management", "view": "week"}, now=NOW)
    assert "allocation" not in result["data"]["plan"]
    assert "dose_evidence" not in result["data"]["plan"]["days"][0]["sessions"][0]
    repository.plan_read.assert_called_once_with(ALIAS, schedule_only=True)
    assert "diagnostics" in assistant_service._capabilities(FULL)["read_views"]["management"]
    assert "view=diagnostics" in assistant_provider._INSTRUCTIONS
    properties = assistant_provider._REPLY_TOOL["parameters"]["properties"]["query"]["properties"]
    assert "diagnostics" in properties["section"]["description"]
    assert "revision" in properties["revision"]["description"]
