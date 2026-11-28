"""Actual Intervals queue progress follows the same grants as enqueueing."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException
import pytest

from apps.api import assistant_context


def repository():
    return SimpleNamespace(athlete_settings=Mock(return_value=SimpleNamespace(timezone="UTC")),
        sync_state=Mock(return_value={"status": "RUNNING", "job_kind": "FULL_SYNC", "job_id": "test-job",
            "progress_stage": "ACTIVITIES", "progress_percent": 35, "active_revision": 8,
            "active_generation_id": "test-generation", "raw_token": "MUST_NOT_LEAK"}))


@pytest.mark.parametrize("access", [
    {"can_view_recovery": True, "can_view_plan": False, "can_edit_plan": False},
    {"can_view_recovery": False, "can_view_plan": True, "can_edit_plan": True},
])
def test_progress_read_uses_actual_state_and_keeps_canonical_public_fields(access):
    repo = repository()
    result = assistant_context.read(repo, "athlete-one", access, {"kind": "management", "view": "sync-state"},
        now=datetime(2026, 11, 12, tzinfo=timezone.utc))
    sync = result["data"]["sync"]
    assert sync["state"] == "RUNNING" and sync["progress_percent"] == 35 and sync["scope"] == "FULL"
    assert "MUST_NOT_LEAK" not in str(result)
    repo.sync_state.assert_called_once_with("athlete-one")


@pytest.mark.parametrize("access", [{}, {"can_view_plan": True}, {"can_edit_plan": True}, {"can_view_recovery": "true"}])
def test_progress_read_does_not_broaden_plan_only_viewer_permissions(access):
    repo = repository()
    with pytest.raises(HTTPException) as exc:
        assistant_context.read(repo, "athlete-one", access, {"kind": "management", "view": "sync-state"})
    assert exc.value.status_code == 403
    repo.sync_state.assert_not_called()
