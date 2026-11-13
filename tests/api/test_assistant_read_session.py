"""A conversation may reuse settings, but never permissions or editable inputs."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from apps.api import assistant_service as service
from apps.api.assistant_read_session import AssistantReadSession
from apps.api.assistant_provider import ScriptedAssistantProvider
from apps.api.oauth_store import _PlanningSettingsBundle
from tests.api.test_assistant_service import ConversationRepository, ALIAS, ACTOR, OWNER, body, query


def test_phase_reuses_settings_by_athlete_and_never_for_editable_inputs():
    repository = SimpleNamespace(athlete_settings=Mock(side_effect=lambda alias: {"alias": alias, "zone": [1]}))
    phase = AssistantReadSession(repository)
    first = phase.athlete_settings(ALIAS)
    first["zone"].append(2)
    assert phase.athlete_settings(ALIAS)["zone"] == [1]
    assert phase.athlete_settings("other")["alias"] == "other"
    assert repository.athlete_settings.call_count == 2
    assert phase.for_query({"kind": "inputs"}) is repository
    assert phase.for_query({"kind": "settings"}) is phase
    AssistantReadSession(repository).athlete_settings(ALIAS)
    assert repository.athlete_settings.call_count == 3


def _repository():
    repo = ConversationRepository()
    bundle = _PlanningSettingsBundle([{"hr_zone_bounds": [90, 120, 140, 160, 180, 200],
        "timezone": "Europe/Sofia", "hrmax_bpm": 200, "hr_zone_source": "MANUAL", "model_preferences": {}}])
    repo.athlete_settings = Mock(side_effect=lambda alias: bundle.athlete_settings())
    repo.athlete_mesocycle_accent_preferences = Mock(return_value=None)
    return repo


def _provider():
    return ScriptedAssistantProvider([
        {"text": "", "query": query("settings", view="profile")},
        {"text": "", "query": query("settings", view="accents")},
        {"text": "Настройките са прочетени."},
    ])


def test_actual_read_turn_fetches_settings_once_and_next_turn_is_fresh(monkeypatch):
    repo = _repository()
    access = Mock(return_value=deepcopy(OWNER))
    monkeypatch.setattr(service.assistant_commands, "access", access)
    first = service.turn(repo, ALIAS, ACTOR, body("Покажи профила и акцентите"), _provider())
    assert first["message"] == "Настройките са прочетени."
    assert repo.athlete_settings.call_count == 1
    assert repo.athlete_mesocycle_accent_preferences.call_count == 1
    assert access.call_count == 7  # initial, each model step, each read, final
    service.turn(repo, ALIAS, ACTOR, body("Покажи профила и акцентите", first["conversation_id"]), _provider())
    assert repo.athlete_settings.call_count == 2
    assert access.call_count == 14


def test_revoked_rights_after_first_read_are_never_cached(monkeypatch):
    repo = _repository()
    access = Mock(side_effect=[deepcopy(OWNER)] * 3 + [{"can_view_plan": False, "can_view_recovery": False}])
    monkeypatch.setattr(service.assistant_commands, "access", access)
    provider = _provider()
    with pytest.raises(HTTPException) as exc:
        service.turn(repo, ALIAS, ACTOR, body("Покажи профила и акцентите"), provider)
    assert exc.value.status_code == 403
    assert len(provider.calls) == 1
    assert repo.rpc_actions == ["BEGIN"]
