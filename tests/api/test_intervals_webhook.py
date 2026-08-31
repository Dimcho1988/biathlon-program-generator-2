from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from apps.api import intervals_webhook
from apps.api.application import app


WEBHOOK_URL = "/api/v2/integrations/intervals/webhook"


def _payload(secret: str = "webhook-secret") -> dict:
    return {
        "secret": secret,
        "events": [
            {
                "athlete_id": "2049151",
                "type": "ACTIVITY_ANALYZED",
                "timestamp": "2026-08-31T10:00:00+00:00",
                "activity": {"id": "i123", "type": "Run"},
            }
        ],
    }


def test_intervals_webhook_requires_configured_matching_secret(monkeypatch):
    client = TestClient(app)
    monkeypatch.delenv("INTERVALS_WEBHOOK_SECRET", raising=False)
    assert client.post(WEBHOOK_URL, json=_payload()).status_code == 503

    monkeypatch.setenv("INTERVALS_WEBHOOK_SECRET", "webhook-secret")
    assert client.post(WEBHOOK_URL, json=_payload("wrong-secret")).status_code == 401


def test_intervals_analyzed_webhook_enqueues_durable_full_sync(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 31, 21, 30, tzinfo=timezone.utc)

    class Repository:
        def __init__(self):
            self.calls = []

        def alias_for_provider(self, provider_athlete_id):
            assert provider_athlete_id == "2049151"
            return "ath-webhook-test"

        def athlete_settings(self, athlete_alias):
            assert athlete_alias == "ath-webhook-test"
            return {"timezone": "Europe/Sofia"}

        def enqueue_sync_job(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "job_id": "job-webhook-1",
                "status": "QUEUED",
                "deduplicated": False,
            }

    repository = Repository()
    monkeypatch.setenv("INTERVALS_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setattr(intervals_webhook.api_main, "_repository", lambda: repository)
    monkeypatch.setattr(intervals_webhook, "datetime", FixedDateTime)

    response = TestClient(app).post(WEBHOOK_URL, json=_payload())

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "scheduled": 1}
    assert len(repository.calls) == 1
    call = repository.calls[0]
    assert call["athlete_alias"] == "ath-webhook-test"
    assert call["job_kind"] == "FULL_SYNC"
    assert len(call["idempotency_key"]) == 64
    assert call["request_payload"] == {
        "schema_version": "sync-request-v1",
        "scope": "FULL",
        "as_of": "2026-09-01",
    }


def test_intervals_webhook_retry_uses_same_idempotency_key(monkeypatch):
    class Repository:
        def __init__(self):
            self.keys = []

        def alias_for_provider(self, provider_athlete_id):
            return "ath-webhook-test"

        def athlete_settings(self, athlete_alias):
            return {"timezone": "UTC"}

        def enqueue_sync_job(self, **kwargs):
            self.keys.append(kwargs["idempotency_key"])
            return {
                "job_id": "job-webhook-1",
                "status": "RUNNING",
                "deduplicated": True,
            }

    repository = Repository()
    monkeypatch.setenv("INTERVALS_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setattr(intervals_webhook.api_main, "_repository", lambda: repository)

    client = TestClient(app)
    assert client.post(WEBHOOK_URL, json=_payload()).status_code == 200
    assert client.post(WEBHOOK_URL, json=_payload()).status_code == 200
    assert len(repository.keys) == 2
    assert repository.keys[0] == repository.keys[1]


def test_unconsumed_webhook_type_is_acknowledged_without_storage(monkeypatch):
    monkeypatch.setenv("INTERVALS_WEBHOOK_SECRET", "webhook-secret")
    payload = {
        "secret": "webhook-secret",
        "events": [
            {
                "athlete_id": "2049151",
                "type": "CALENDAR_UPDATED",
                "timestamp": "2026-08-31T10:00:00+00:00",
            }
        ],
    }

    def fail_if_called():
        raise AssertionError("repository should not be opened")

    monkeypatch.setattr(intervals_webhook.api_main, "_repository", fail_if_called)
    response = TestClient(app).post(WEBHOOK_URL, json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "scheduled": 0}
