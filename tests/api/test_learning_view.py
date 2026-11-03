from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api import dependencies, learning_view, main
from apps.api.oauth_store import PersistentStoreFailure
from tests.api.test_load_progression import configured


NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


def report():
    return {"mode": "SHADOW", "status": "COLLECTING", "summary": "Събираме наблюдения.",
        "evidence_count": 0, "validation": {"status": "WARMUP", "evaluated": 0},
        "memory": {"private": "not for transport"}, "recovery_policy": {
            "series": [{"day": "2026-11-02", "actual_q": 10., "stress": None, "baseline": None,
                        "raw_hrv": 35., "private_source": "secret"}],
            "episodes": [{"id": "one", "start": "2026-10-15", "load_end": "2026-10-28",
                "end": "2026-11-02", "loading_days": 14, "recovery_limit_days": 5., "recovery_days": None,
                "recovered": False, "ratio": None, "status": "OBSERVING", "proposed_change_percent": 0.,
                "reason": "Няма потвърдено връщане.", "performance_change_percent": None, "private_note": "hidden"}],
            "decision": {"component": None, "status": "COLLECTING", "proposed_change_percent": 0.,
                         "applied_change_percent": 0., "reason": "Нужни са данни.", "raw_lab": 99},
            "observations": {"stress_days": 0, "load_days": 1, "private_raw": 99},
            "channels": [{"key": "hrv", "label": "HRV", "observed_days": 0, "raw_values": [1, 2]}],
        }}


class Repository:
    def capture_planning_inputs(self, alias):
        return None

    def athlete_settings(self, alias):
        return SimpleNamespace(timezone="Europe/Sofia")

    def active_planning_calendar(self, alias, first, today):
        return {"generation_id": "g", "revision": 1, "activities": [], "snapshot_payload": {
            "load_history": {"period_end": "2026-11-03", "daily": []}}}

    def active_analysis(self, alias):
        return {"generation_id": "g", "revision": 1}

    def athlete_planning_calendar(self, alias):
        return {"events": []}


def test_view_transports_only_dedicated_observation_fields_and_never_publishes(monkeypatch):
    repo = Repository()
    original = report()
    monkeypatch.setattr(learning_view.ReadSession, "planning", lambda _: repo)
    monkeypatch.setattr(learning_view, "ManagementStore", lambda _: SimpleNamespace(profile=lambda alias: {
        "configured": True, "profile": configured(discipline="5000 m")}))
    monkeypatch.setattr(learning_view, "project_snapshot", lambda _, alias, snapshot: snapshot)
    monkeypatch.setattr(learning_view.learning_service, "context", lambda *args, **kwargs: deepcopy(original))
    result = learning_view.view(repo, "ath-test", now=NOW)
    assert result["schema_version"] == "recovery-learning-view-v1"
    assert result["mode"] == "SHADOW" and result["decision"]["applied_change_percent"] == 0
    assert result["series"][0]["stress"] is None
    assert "raw_hrv" not in result["series"][0]
    assert "private_note" not in result["episodes"][0]
    assert "raw_lab" not in result["decision"]
    assert "private_raw" not in result["observations"]
    assert "raw_values" not in result["channels"][0]
    assert "memory" not in result
    assert original == report()


def test_generation_change_is_not_silently_joined(monkeypatch):
    repo = Repository()
    repo.active_analysis = lambda alias: {"generation_id": "new", "revision": 2}
    monkeypatch.setattr(learning_view.ReadSession, "planning", lambda _: repo)
    with pytest.raises(PersistentStoreFailure, match="changed"):
        learning_view.view(repo, "ath-test", now=NOW)


def test_learning_http_requires_service_and_explicit_athlete_and_no_store(monkeypatch):
    monkeypatch.setenv("ONFLOWS_SERVICE_TOKEN", "test-service")
    monkeypatch.setattr(dependencies, "repository", lambda: object())
    monkeypatch.setattr(learning_view, "view", lambda repo, alias, days: {"alias": alias, "days": days})
    headers = {"Authorization": "Bearer test-service", "X-OnFlows-Athlete-Alias": "ath-test"}
    with TestClient(main.app) as client:
        assert client.get("/api/v2/athlete/learning").status_code == 401
        assert client.get("/api/v2/athlete/learning", headers={"Authorization": "Bearer test-service"}).status_code == 401
        response = client.get("/api/v2/athlete/learning?days=30", headers=headers)
        assert response.status_code == 200 and response.json()["days"] == 30
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/v2/athlete/learning?days=365", headers=headers).status_code == 422
