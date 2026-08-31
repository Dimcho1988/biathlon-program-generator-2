"""Secure Intervals.icu webhook ingress for automatic analysis refreshes.

The webhook never runs scientific work inline. It validates the app-level
secret, maps the provider athlete to an existing onFlows alias, and enqueues
one durable FULL_SYNC job per affected athlete. The existing worker remains the
only component that imports provider data and activates analysis generations.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request

from . import main as api_main
from .cloud import service_token_valid
from .oauth_store import PersistentStoreFailure, SupabasePilotRepository


logger = logging.getLogger(__name__)
router = APIRouter()

# Intervals documents ACTIVITY_ANALYZED as a delayed/consolidated webhook. Using
# it avoids doing a full refresh both at upload time and again after analysis.
_ACTIVITY_SYNC_EVENTS = frozenset({"ACTIVITY_ANALYZED"})
_MAX_WEBHOOK_EVENTS = 100


def _athlete_analysis_date(
    repository: SupabasePilotRepository,
    athlete_alias: str,
) -> str:
    settings = repository.athlete_settings(athlete_alias)
    timezone_name = (
        settings.get("timezone")
        if isinstance(settings, Mapping)
        else getattr(settings, "timezone", None)
    )
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ValueError("Athlete timezone is unavailable")
    try:
        athlete_timezone = ZoneInfo(timezone_name.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Athlete timezone is unavailable") from exc
    return datetime.now(timezone.utc).astimezone(athlete_timezone).date().isoformat()


def _webhook_idempotency_key(
    provider_athlete_id: str,
    events: list[Mapping[str, Any]],
) -> str:
    """Build a stable key so Intervals retries cannot create duplicate work."""

    try:
        event_rows = sorted(
            json.dumps(
                dict(event),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            for event in events
        )
        rendered = json.dumps(
            {
                "schema_version": "intervals-webhook-sync-v1",
                "provider_athlete_id": provider_athlete_id,
                "events": event_rows,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Intervals webhook event is invalid") from exc
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _activity_events_by_athlete(
    payload: Mapping[str, Any],
) -> dict[str, list[Mapping[str, Any]]]:
    events = payload.get("events")
    if not isinstance(events, list) or len(events) > _MAX_WEBHOOK_EVENTS:
        raise ValueError("Intervals webhook event list is invalid")

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError("Intervals webhook event is invalid")
        if event.get("type") not in _ACTIVITY_SYNC_EVENTS:
            continue
        provider_athlete_id = event.get("athlete_id")
        if (
            not isinstance(provider_athlete_id, str)
            or not provider_athlete_id.strip()
            or len(provider_athlete_id) > 128
        ):
            raise ValueError("Intervals webhook athlete identity is invalid")
        grouped.setdefault(provider_athlete_id.strip(), []).append(event)
    return grouped


@router.post(
    "/api/v2/integrations/intervals/webhook",
    include_in_schema=False,
)
async def intervals_webhook(request: Request) -> dict[str, object]:
    """Acknowledge Intervals events after durably enqueueing refreshes."""

    expected_secret = os.environ.get("INTERVALS_WEBHOOK_SECRET", "").strip()
    if not expected_secret:
        raise HTTPException(status_code=503, detail="Intervals webhook is not configured")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc
    if not isinstance(payload, Mapping):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    supplied_secret = payload.get("secret")
    provided = supplied_secret if isinstance(supplied_secret, str) else None
    if not service_token_valid(provided, expected_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    try:
        grouped = _activity_events_by_athlete(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc

    # A valid webhook can contain only event types onFlows does not consume.
    if not grouped:
        return {"status": "ok", "scheduled": 0}

    try:
        repository = api_main._repository()
        scheduled = 0
        for provider_athlete_id, events in grouped.items():
            athlete_alias = repository.alias_for_provider(provider_athlete_id)
            if athlete_alias is None:
                # A delayed webhook can arrive after disconnect. Do not create
                # an unscoped job and do not expose connection state.
                continue
            row = repository.enqueue_sync_job(
                athlete_alias=athlete_alias,
                job_kind="FULL_SYNC",
                idempotency_key=_webhook_idempotency_key(
                    provider_athlete_id, events
                ),
                request_payload={
                    "schema_version": "sync-request-v1",
                    "scope": "FULL",
                    "as_of": _athlete_analysis_date(repository, athlete_alias),
                },
            )
            status = str(row.get("status") or "")
            if status not in {"QUEUED", "RETRY_WAIT", "RUNNING"}:
                raise PersistentStoreFailure(
                    "Sync queue returned an invalid webhook state"
                )
            scheduled += 1
        logger.info(
            "intervals_webhook_processed athletes=%d scheduled=%d",
            len(grouped),
            scheduled,
        )
        return {"status": "ok", "scheduled": scheduled}
    except (PersistentStoreFailure, ValueError) as exc:
        logger.warning(
            "intervals_webhook_enqueue_failed error_type=%s",
            type(exc).__name__,
        )
        # Non-2xx asks Intervals to retry. The deterministic idempotency key
        # makes a partial prior enqueue safe to repeat.
        raise HTTPException(
            status_code=503,
            detail="Intervals webhook refresh could not be queued",
        ) from exc
