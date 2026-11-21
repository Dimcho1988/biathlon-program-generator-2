"""What-if previews use canonical arithmetic without saving or rebuilding models."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException
import pytest

from apps.api import assistant_race_duration as preview, assistant_reads, model_service, race_duration


FULL = {"can_view_plan": True, "can_edit_plan": True, "can_view_recovery": True}
QUERY = {"kind": "management", "view": "race-duration", "sport": "Run", "discipline": "1500 m", "race_duration_min": 15}


def curve():
    return {"sport": "Run", "status": "CALIBRATED", "active_test_count": 2,
        "model_version": "v1", "source_generation_id": "private-generation", "source_revision": 9,
        "active_test_keys": ["private-test-a", "private-test-b"], "tests": [],
        "points": [{"distance_m": 1000, "duration_s": 180}, {"distance_m": 2000, "duration_s": 420}]}


def test_unsaved_preview_matches_canonical_resolver_and_never_rebuilds_speed(monkeypatch):
    repository = SimpleNamespace(athlete_settings=Mock(return_value=SimpleNamespace(timezone="UTC")))
    saved = curve()
    before = deepcopy(saved)
    prepared = Mock(return_value=saved)
    monkeypatch.setattr(preview.prepared_views, "read", prepared)
    monkeypatch.setattr(model_service, "speed_view", lambda *a, **k: pytest.fail("No synchronous speed model calculation"))
    result = preview.read(repository, "athlete-one", FULL, QUERY)
    expected = race_duration.preview(repository, "athlete-one", result["input"], speed_view=saved)
    assert result["estimate"] == expected
    assert result["estimate"]["source"] == "SPEED_DURATION"
    assert 3 < result["estimate"]["duration_min"] < 7
    assert result["input"]["race_duration_min"] == 15
    assert result["saved"] is False and result["preview_only"] is True
    prepared.assert_called_once_with(repository, "athlete-one", "speed", {"sport": "Run"})
    assert saved == before


def test_plan_only_projection_keeps_target_estimate_without_private_test_identities(monkeypatch):
    repository = SimpleNamespace(athlete_settings=lambda _: SimpleNamespace(timezone="UTC"))
    monkeypatch.setattr(preview.prepared_views, "read", lambda *a: curve())
    result = preview.read(repository, "athlete-one", {**FULL, "can_view_recovery": False}, QUERY)
    assert result["estimate"]["source"] == "SPEED_DURATION"
    assert result["estimate"]["duration_min"] > 0
    assert "private" not in str(result)
    assert "source_revision" not in result["estimate"]


@pytest.mark.parametrize("access", [{}, {"can_view_plan": True}, {"can_edit_plan": True}, {**FULL, "can_edit_plan": "true"}])
def test_preview_matches_profile_editor_permissions_before_any_read(access):
    with pytest.raises(HTTPException) as exc:
        preview.read(None, "athlete-one", access, QUERY)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("patch", [{"sport": "Bike"}, {"discipline": ""}, {"discipline": "x" * 101},
    {"race_duration_min": True}, {"race_duration_min": -1}, {"race_duration_min": float("nan")}, {"race_duration_min": 1441}])
def test_invalid_preview_rejected_before_read(patch):
    with pytest.raises(HTTPException) as exc:
        preview.read(None, "athlete-one", FULL, {**QUERY, **patch})
    assert exc.value.status_code == 422


def test_cold_curve_is_pending_not_a_synchronous_calculation(monkeypatch):
    monkeypatch.setattr(preview.prepared_views, "read", Mock(side_effect=HTTPException(503, "Preparing")))
    monkeypatch.setattr(race_duration, "preview", lambda *a, **k: pytest.fail("No fabricated estimate before the curve is ready"))
    assert preview.read(None, "athlete-one", FULL, QUERY) == {
        "availability": "PENDING", "reason": "PREPARED_VIEW_PENDING", "view_kind": "speed"}


def test_ambiguous_distance_uses_manual_fallback_without_reading_curve(monkeypatch):
    monkeypatch.setattr(preview.prepared_views, "read", lambda *a: pytest.fail("Ambiguous distance has no model input"))
    result = preview.read(None, "athlete-one", FULL, {**QUERY, "discipline": "5 km / 10 km"})
    assert result["estimate"]["source"] == "MANUAL" and result["estimate"]["duration_min"] == 15
    assert result["estimate"]["reason"] == "DISTANCE_REQUIRED"


def test_read_adapter_keeps_preview_label_and_plan_only_estimate(monkeypatch):
    repository = SimpleNamespace(athlete_settings=lambda _: SimpleNamespace(timezone="UTC"))
    monkeypatch.setattr(preview.prepared_views, "read", lambda *a: curve())
    result = assistant_reads.read(repository, "athlete-one", {**FULL, "can_view_recovery": False},
        QUERY, None, date(2026, 11, 12), lambda value, default: default)
    assert result["source"] == "CANONICAL_WHAT_IF_PREVIEW"
    assert result["data"]["estimate"]["duration_min"] > 0
    assert result["data"]["saved"] is False
    assert "private" not in str(result)
