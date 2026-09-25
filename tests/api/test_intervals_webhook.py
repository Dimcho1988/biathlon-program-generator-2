from __future__ import annotations

from apps.api import dependencies


from datetime import datetime, timezone

from fastapi.testclient import TestClient

from apps.api import intervals_webhook
from apps.api.application import app


WEBHOOK_URL = "/api/v2/integrations/intervals/webhook"


def _payload(
    secret: str = "webhook-secret",
    event_type: str = "ACTIVITY_ANALYZED",
) -> dict:
    return {
        "secret": secret,
        "events": [
            {
                "athlete_id": "2049151",
                "type": event_type,
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
    monkeypatch.setattr(dependencies, "repository", lambda: repository)
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


def test_intervals_uploaded_webhook_enqueues_durable_full_sync(monkeypatch):
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
                "job_id": "job-webhook-upload-1",
                "status": "QUEUED",
                "deduplicated": False,
            }

    repository = Repository()
    monkeypatch.setenv("INTERVALS_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setattr(dependencies, "repository", lambda: repository)

    response = TestClient(app).post(
        WEBHOOK_URL,
        json=_payload(event_type="ACTIVITY_UPLOADED"),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "scheduled": 1}
    assert len(repository.calls) == 1
    assert repository.calls[0]["athlete_alias"] == "ath-webhook-test"
    assert repository.calls[0]["job_kind"] == "FULL_SYNC"


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
    monkeypatch.setattr(dependencies, "repository", lambda: repository)

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

    monkeypatch.setattr(dependencies, "repository", fail_if_called)
    response = TestClient(app).post(WEBHOOK_URL, json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "scheduled": 0}


def test_waiting_for_webhook_storage_does_not_block_health(monkeypatch):
    import asyncio
    from threading import Event
    import httpx

    entered, release = Event(), Event()
    class SlowRepository:
        def alias_for_provider(self, provider):
            entered.set()
            assert release.wait(2), 'storage was not released'
            return 'ath-webhook-test'
        def athlete_settings(self, alias):
            return {'timezone': 'UTC'}
        def enqueue_sync_job(self, **kwargs):
            return {'status': 'QUEUED'}
    monkeypatch.setenv('INTERVALS_WEBHOOK_SECRET', 'webhook-secret')
    monkeypatch.setattr(dependencies, 'repository', SlowRepository)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            webhook = asyncio.create_task(client.post(WEBHOOK_URL, json=_payload()))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                response = await asyncio.wait_for(client.get('/health'), .5)
                assert response.status_code == 200
                assert not webhook.done(), 'health waited for the blocked storage request'
            finally:
                release.set()
            assert (await webhook).json() == {'status': 'ok', 'scheduled': 1}
    asyncio.run(run())
