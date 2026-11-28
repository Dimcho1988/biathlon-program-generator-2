from types import SimpleNamespace
from unittest.mock import Mock
from fastapi import HTTPException
import pytest
from apps.api.routes import management
from apps.api import prepared_views
from apps.api.oauth_store import PersistentStoreFailure


def test_status_reads_only_small_result_and_matches_requested_variant(monkeypatch):
    operation = Mock(return_value={'ready': False, 'preparation': None})
    monkeypatch.setattr(management, '_proposal_access', lambda *args: (SimpleNamespace(management_preparation_status=operation), 'ath'))
    result = management.preparation_status(view='overview')
    assert result == {'ready': False, 'preparation': None}
    operation.assert_called_once_with('ath', prepared_views.model_key(), 'overview')


def test_status_never_reads_after_permission_denial(monkeypatch):
    def denied(*args): raise HTTPException(403)
    monkeypatch.setattr(management, '_proposal_access', denied)
    with pytest.raises(HTTPException) as exc: management.preparation_status()
    assert exc.value.status_code == 403


def test_status_store_failure_does_not_claim_ready(monkeypatch):
    operation = Mock(side_effect=PersistentStoreFailure('unavailable'))
    monkeypatch.setattr(management, '_proposal_access', lambda *args: (SimpleNamespace(management_preparation_status=operation), 'ath'))
    with pytest.raises(HTTPException) as exc: management.preparation_status()
    assert exc.value.status_code == 503


def test_screen_and_assistant_share_profile_interpretation():
    from apps.api.assistant_read_projection import profile_availability
    payload = {'profile': {'available_minutes': [60,60,60,60,60,90,0]}}
    repo = SimpleNamespace(prepared_view=lambda *args, **kwargs: {
        'ready':True,'athlete_alias':'ath','kind':'management-profile','payload':payload})
    result = prepared_views.read(repo, 'ath', 'management-profile')
    assert result['availability_resolution'] == profile_availability(payload)
    assert result['profile'] == payload['profile']
