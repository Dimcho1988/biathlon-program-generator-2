"""Saved profile values cannot be mistaken for the planner's operative limits."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.api import assistant_context, assistant_reads
from apps.api.assistant_read_projection import profile_availability
from biathlon.planning_history import LEGACY_AVAILABILITY, availability, availability_mode


@pytest.mark.parametrize("profile,mode,limits,days", [
    ({"available_minutes": LEGACY_AVAILABILITY, "availability_mode": None, "training_days": None}, "AUTO_HISTORY", None, list(range(7))),
    ({"available_minutes": LEGACY_AVAILABILITY, "availability_mode": "MANUAL"}, "MANUAL", LEGACY_AVAILABILITY, list(range(6))),
    ({"available_minutes": [45, 0, 60, 0, 75, 0, 0]}, "MANUAL", [45, 0, 60, 0, 75, 0, 0], [0, 2, 4]),
    ({"available_minutes": [0] * 7}, "MANUAL", [0] * 7, []),
    ({"available_minutes": [30] * 7, "availability_mode": "AUTO_HISTORY", "training_days": [1, 3]}, "AUTO_HISTORY", None, [1, 3]),
    ({"available_minutes": [30] * 7, "availability_mode": "MANUAL", "training_days": [1, 3]}, "MANUAL", [0, 30, 0, 30, 0, 0, 0], [1, 3]),
])
def test_resolution_matches_canonical_rules_without_changing_saved_profile(profile, mode, limits, days):
    before = deepcopy(profile)
    result = profile_availability({"profile": {"configured": True, "profile": profile}})
    assert result["effective_mode"] == mode == availability_mode(profile)
    assert result["manual_daily_limits_minutes"] == limits
    assert result["manual_weekly_availability_minutes"] == (sum(limits) if limits is not None else None)
    assert result["profile_allowed_weekdays"] == days == [i for i, value in enumerate(availability(profile)) if value > 0]
    assert result["stored_available_minutes_role"] == ("MANUAL_DAY_LIMITS" if mode == "MANUAL" else "INACTIVE_IN_AUTO_HISTORY")
    assert profile == before


@pytest.mark.parametrize("recovery", [True, False])
@pytest.mark.parametrize("section", [None, "profile.available_minutes"])
def test_profile_read_keeps_explanation_even_when_selecting_raw_legacy_minutes(monkeypatch, recovery, section):
    saved = {"configured": True, "revision": 14, "profile": {"available_minutes": deepcopy(LEGACY_AVAILABILITY),
        "availability_mode": None, "training_days": None}}
    before = deepcopy(saved)
    prepared = Mock(return_value=saved)
    monkeypatch.setattr(assistant_reads, "_prepared", prepared)
    repository = SimpleNamespace(athlete_settings=Mock(return_value=SimpleNamespace(timezone="Europe/Sofia")))
    query = {"kind": "management", "view": "profile", **({"section": section} if section else {})}
    result = assistant_context.read(repository, "athlete", {"can_view_plan": True, "can_view_recovery": recovery}, query)
    resolution = result["availability_resolution"]
    assert resolution["effective_mode"] == "AUTO_HISTORY"
    assert resolution["stored_mode"] is None
    assert resolution["resolution_basis"] == "LEGACY_TEMPLATE_AUTO_HISTORY"
    assert resolution["manual_daily_limits_minutes"] is None
    assert resolution["manual_weekly_availability_minutes"] is None
    assert "неактивни" in resolution["interpretation"]
    shown = result["data"] if section else result["data"]["profile"]["available_minutes"]
    assert shown == LEGACY_AVAILABILITY
    if not section:
        assert result["data"]["profile"] == saved["profile"]
    assert saved == before
    repository.athlete_settings.assert_called_once_with("athlete")
    prepared.assert_called_once_with(repository, "athlete", "management-profile", {})


@pytest.mark.parametrize("payload", [{}, {"profile": None}, {"profile": {"availability_mode": None}}, {"available_minutes": None}])
def test_missing_profile_fields_do_not_invent_capacity(payload):
    assert profile_availability(payload) is None


def test_editable_profile_read_keeps_raw_patch_source_and_shares_resolution(monkeypatch):
    saved = {"configured": True, "revision": 14, "profile": {"available_minutes": deepcopy(LEGACY_AVAILABILITY),
        "availability_mode": None, "training_days": None}}
    profile_reader = Mock(return_value=saved)
    monkeypatch.setattr(assistant_context, "ManagementStore", lambda repo: SimpleNamespace(profile=profile_reader))
    repository = SimpleNamespace(athlete_settings=Mock(return_value=SimpleNamespace(timezone="Europe/Sofia")))
    query = {"kind": "inputs", "input_kind": "MANAGEMENT_PROFILE"}
    result = assistant_context.read(repository, "athlete", {"can_view_plan": True}, query)
    assert result["payload"] == saved["profile"]
    assert "availability_resolution" not in result["payload"]
    assert result["availability_resolution"] == profile_availability(saved)
    assert result["availability_resolution"]["manual_weekly_availability_minutes"] is None
    profile_reader.assert_called_once_with("athlete")


def test_profile_metadata_requires_plan_permission(monkeypatch):
    from fastapi import HTTPException
    prepared = Mock(side_effect=AssertionError("No unauthorized data read"))
    monkeypatch.setattr(assistant_reads, "_prepared", prepared)
    with pytest.raises(HTTPException) as exc:
        assistant_context.read(SimpleNamespace(), "athlete", {"can_view_plan": False, "can_view_recovery": True},
            {"kind": "management", "view": "profile"})
    assert exc.value.status_code == 403
    prepared.assert_not_called()
