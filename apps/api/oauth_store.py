"""Persistent, server-only storage for OAuth grants and aggregate snapshots.

The Supabase secret key and the token-encryption key never cross this module's
server boundary.  Raw provider payloads are deliberately not persisted.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import os
import re
import secrets
from typing import Any, Mapping
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .cloud import (
    AthleteMesocycleAccentPreferences,
    AthleteModelSettings,
    AthletePlanningCalendar,
    AthletePlanningProfile,
    SnapshotRepository,
)


logger = logging.getLogger(__name__)


def _safe_store_error(response: httpx.Response) -> str:
    """Classify a PostgREST error without retaining its possibly private detail."""

    try:
        payload = response.json()
    except Exception:
        return "code=unknown category=request"
    if not isinstance(payload, Mapping):
        return "code=unknown category=request"
    raw_code = payload.get("code")
    code = (
        raw_code
        if isinstance(raw_code, str) and re.fullmatch(r"[A-Z0-9]{5,12}", raw_code)
        else "unknown"
    )
    message = payload.get("message")
    rendered = message if isinstance(message, str) else ""
    category = "request"
    if "cannot affect row a second time" in rendered:
        category = "duplicate_batch"
    elif "null value in column" in rendered:
        category = "not_null"
    elif "violates check constraint" in rendered:
        category = "check"
    elif "duplicate key value violates unique constraint" in rendered:
        category = "unique"
    elif "invalid input syntax" in rendered:
        category = "invalid_input"
    elif "schema cache" in rendered.lower():
        category = "schema_cache"
    target_match = re.search(
        r'(?:column|constraint) "([a-z][a-z0-9_]*)"', rendered
    )
    target = f" target={target_match.group(1)}" if target_match else ""
    return f"code={code} category={category}{target}"


def _retryable_store_auth_failure(response: httpx.Response) -> bool:
    """Recognize the bounded, safe-to-retry PostgREST auth failure."""

    if response.status_code != 401:
        return False
    try:
        payload = response.json()
    except Exception:
        return False
    return isinstance(payload, Mapping) and payload.get("code") == "PGRST303"


class PersistentStoreConfigurationError(RuntimeError):
    """Safe configuration failure without credential values."""


class PersistentStoreFailure(RuntimeError):
    """Sanitized storage failure without database response bodies."""


@dataclass(frozen=True)
class OAuthConnection:
    athlete_alias: str
    provider_athlete_id: str
    access_token: str
    scopes: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class PendingOAuthState:
    athlete_alias: str | None
    redirect_uri: str


class TokenCipher:
    """AES-256-GCM envelope for provider tokens stored in Postgres."""

    def __init__(self, encoded_key: str) -> None:
        try:
            padding = "=" * (-len(encoded_key) % 4)
            key = base64.urlsafe_b64decode(encoded_key + padding)
        except (ValueError, TypeError) as exc:
            raise PersistentStoreConfigurationError(
                "Token encryption key is invalid"
            ) from exc
        if len(key) != 32:
            raise PersistentStoreConfigurationError(
                "Token encryption key must encode exactly 32 bytes"
            )
        self._cipher = AESGCM(key)

    def encrypt(self, token: str, *, athlete_alias: str) -> str:
        if not token or not athlete_alias:
            raise ValueError("token and athlete alias are required")
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(
            nonce,
            token.encode("utf-8"),
            athlete_alias.encode("utf-8"),
        )
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, envelope: str, *, athlete_alias: str) -> str:
        try:
            packed = base64.urlsafe_b64decode(envelope)
            rendered = self._cipher.decrypt(
                packed[:12], packed[12:], athlete_alias.encode("utf-8")
            ).decode("utf-8")
        except Exception as exc:
            raise PersistentStoreFailure(
                "Stored provider credential could not be decrypted"
            ) from exc
        if not rendered:
            raise PersistentStoreFailure("Stored provider credential is empty")
        return rendered


class SupabasePilotRepository(SnapshotRepository):
    """Minimal PostgREST client using a server-only Supabase secret key."""

    def __init__(
        self,
        *,
        supabase_url: str,
        secret_key: str,
        encryption_key: str,
        client: httpx.Client | None = None,
        generation_reads: bool = False,
    ) -> None:
        if not supabase_url.startswith("https://") or not secret_key:
            raise PersistentStoreConfigurationError(
                "Supabase server configuration is incomplete"
            )
        self._base_url = supabase_url.rstrip("/") + "/rest/v1"
        self._headers = {
            "apikey": secret_key,
            "Content-Type": "application/json",
        }
        # Supabase's current opaque server keys authenticate through `apikey`.
        # Legacy service-role keys are JWTs and still require Bearer auth.
        if not secret_key.startswith("sb_secret_"):
            self._headers["Authorization"] = f"Bearer {secret_key}"
        self._cipher = TokenCipher(encryption_key)
        self._client = client or httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))
        self._generation_reads = generation_reads

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> "SupabasePilotRepository":
        env = environ or os.environ
        return cls(
            supabase_url=env.get("SUPABASE_URL", "").strip(),
            secret_key=env.get("SUPABASE_SECRET_KEY", "").strip(),
            encryption_key=env.get("ONFLOWS_TOKEN_ENCRYPTION_KEY", "").strip(),
            client=client,
            generation_reads=True,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        resource = path.split("?", 1)[0]
        response: httpx.Response | None = None
        attempts = 2 if method.upper() == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self._client.request(
                    method, self._base_url + path, headers=headers, **kwargs
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "persistent_store_request_failed method=%s resource=%s status=network",
                    method,
                    resource,
                )
                raise PersistentStoreFailure("Persistent store is unavailable") from exc
            if 200 <= response.status_code < 300:
                return response
            if attempt == 0 and _retryable_store_auth_failure(response):
                logger.warning(
                    "persistent_store_request_retry method=%s resource=%s "
                    "status=401 code=PGRST303",
                    method,
                    resource,
                )
                continue
            break
        assert response is not None
        if not 200 <= response.status_code < 300:
            diagnostic = _safe_store_error(response)
            logger.warning(
                "persistent_store_request_failed method=%s resource=%s status=%d %s",
                method,
                resource,
                response.status_code,
                diagnostic,
            )
            raise PersistentStoreFailure(
                "Persistent store request failed "
                f"for {method} {resource} ({response.status_code}; {diagnostic})"
            )
        return response

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception as exc:
            raise PersistentStoreFailure(
                "Persistent store returned an invalid response"
            ) from exc

    @staticmethod
    def _secret_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _rpc_rows(self, name: str, payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        response = self._request("POST", f"/rpc/{name}", json=dict(payload))
        value = self._json(response)
        if not isinstance(value, list) or not all(
            isinstance(row, Mapping) for row in value
        ):
            raise PersistentStoreFailure("Persistent RPC response is invalid")
        return value

    def _rpc_row(
        self, name: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        rows = self._rpc_rows(name, payload)
        return dict(rows[0]) if rows else None

    @staticmethod
    def _canonical_payload_hash(payload: Mapping[str, Any]) -> str:
        try:
            rendered = json.dumps(
                dict(payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise PersistentStoreFailure(
                "Generation activity payload is invalid"
            ) from exc
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    def create_oauth_state(
        self,
        *,
        nonce: str,
        athlete_alias: str | None,
        redirect_uri: str,
        expires_at: datetime,
    ) -> None:
        self._request(
            "POST",
            "/onflows_oauth_states",
            json={
                "nonce_hash": self._secret_hash(nonce),
                "athlete_alias": athlete_alias,
                "redirect_uri": redirect_uri,
                "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
            },
            headers={"Prefer": "return=minimal"},
        )

    def consume_oauth_state(self, nonce: str) -> PendingOAuthState | None:
        response = self._request(
            "POST",
            "/rpc/consume_onflows_oauth_state",
            json={"p_nonce_hash": self._secret_hash(nonce)},
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        if not isinstance(row, Mapping):
            raise PersistentStoreFailure("Persistent OAuth state is invalid")
        alias = row.get("athlete_alias")
        redirect_uri = row.get("redirect_uri")
        if alias is not None and not isinstance(alias, str):
            raise PersistentStoreFailure("Persistent OAuth state is invalid")
        if not isinstance(redirect_uri, str):
            raise PersistentStoreFailure("Persistent OAuth state is invalid")
        return PendingOAuthState(alias, redirect_uri)

    def alias_for_provider(self, provider_athlete_id: str) -> str | None:
        provider_id = quote(provider_athlete_id, safe="")
        response = self._request(
            "GET",
            "/onflows_intervals_connections?select=athlete_alias,status"
            f"&provider_athlete_id=eq.{provider_id}&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        alias = row.get("athlete_alias") if isinstance(row, Mapping) else None
        status = row.get("status") if isinstance(row, Mapping) else None
        if not isinstance(alias, str) or not isinstance(status, str):
            raise PersistentStoreFailure("Persistent OAuth connection is invalid")
        return alias

    def provider_for_alias(self, athlete_alias: str) -> str | None:
        alias = quote(athlete_alias, safe="")
        response = self._request(
            "GET",
            "/onflows_intervals_connections?select=provider_athlete_id,status"
            f"&athlete_alias=eq.{alias}&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        provider_id = (
            row.get("provider_athlete_id") if isinstance(row, Mapping) else None
        )
        status = row.get("status") if isinstance(row, Mapping) else None
        if not isinstance(provider_id, str) or not isinstance(status, str):
            raise PersistentStoreFailure("Persistent OAuth connection is invalid")
        return provider_id

    def create_login_ticket(
        self,
        *,
        ticket: str,
        athlete_alias: str,
        expires_at: datetime,
    ) -> None:
        self._request(
            "POST",
            "/onflows_login_tickets",
            json={
                "ticket_hash": self._secret_hash(ticket),
                "athlete_alias": athlete_alias,
                "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
            },
            headers={"Prefer": "return=minimal"},
        )

    def consume_login_ticket(self, ticket: str) -> str | None:
        response = self._request(
            "POST",
            "/rpc/consume_onflows_login_ticket",
            json={"p_ticket_hash": self._secret_hash(ticket)},
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        alias = row.get("athlete_alias") if isinstance(row, Mapping) else None
        if not isinstance(alias, str):
            raise PersistentStoreFailure("Persistent login ticket is invalid")
        return alias

    def save_connection(
        self,
        *,
        athlete_alias: str,
        provider_athlete_id: str,
        access_token: str,
        scopes: tuple[str, ...],
    ) -> None:
        encrypted = self._cipher.encrypt(access_token, athlete_alias=athlete_alias)
        self._request(
            "POST",
            "/onflows_intervals_connections?on_conflict=athlete_alias",
            json={
                "athlete_alias": athlete_alias,
                "provider_athlete_id": provider_athlete_id,
                "encrypted_access_token": encrypted,
                "scopes": list(scopes),
                "status": "CONNECTED",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def connection(self, athlete_alias: str) -> OAuthConnection | None:
        selected = quote(
            "athlete_alias,provider_athlete_id,encrypted_access_token,scopes,status",
            safe=",",
        )
        alias = quote(athlete_alias, safe="")
        response = self._request(
            "GET",
            f"/onflows_intervals_connections?select={selected}&athlete_alias=eq.{alias}&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        if not isinstance(row, Mapping):
            raise PersistentStoreFailure("Persistent OAuth connection is invalid")
        provider_id = row.get("provider_athlete_id")
        envelope = row.get("encrypted_access_token")
        scopes = row.get("scopes")
        status = row.get("status")
        if (
            not isinstance(provider_id, str)
            or not isinstance(envelope, str)
            or not isinstance(scopes, list)
            or not all(isinstance(scope, str) for scope in scopes)
            or not isinstance(status, str)
        ):
            raise PersistentStoreFailure("Persistent OAuth connection is invalid")
        return OAuthConnection(
            athlete_alias=athlete_alias,
            provider_athlete_id=provider_id,
            access_token=self._cipher.decrypt(envelope, athlete_alias=athlete_alias),
            scopes=tuple(scopes),
            status=status,
        )

    def athlete_settings(self, athlete_alias: str) -> AthleteModelSettings | None:
        alias = quote(athlete_alias, safe="")
        response = self._request(
            "GET",
            "/onflows_athlete_settings?select=hr_zone_bounds,timezone,hrmax_bpm"
            f"&athlete_alias=eq.{alias}&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        bounds = row.get("hr_zone_bounds") if isinstance(row, Mapping) else None
        athlete_timezone = row.get("timezone") if isinstance(row, Mapping) else None
        hrmax_bpm = row.get("hrmax_bpm") if isinstance(row, Mapping) else None
        if (
            not isinstance(bounds, list)
            or len(bounds) != 6
            or not all(isinstance(value, int) and not isinstance(value, bool) for value in bounds)
            or not isinstance(athlete_timezone, str)
            or (hrmax_bpm is not None and (
                not isinstance(hrmax_bpm, int) or isinstance(hrmax_bpm, bool)
            ))
        ):
            raise PersistentStoreFailure("Stored athlete settings are invalid")
        try:
            return AthleteModelSettings(
                tuple(bounds), athlete_timezone, hrmax_bpm
            ).validate()
        except ValueError as exc:
            raise PersistentStoreFailure("Stored athlete settings are invalid") from exc

    def save_athlete_settings(
        self, athlete_alias: str, settings: AthleteModelSettings
    ) -> None:
        validated = settings.validate()
        self._request(
            "POST",
            "/onflows_athlete_settings?on_conflict=athlete_alias",
            json={
                "athlete_alias": athlete_alias,
                "hr_zone_bounds": list(validated.zone_bounds_bpm),
                "timezone": validated.timezone,
                "hrmax_bpm": validated.hrmax_bpm,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def athlete_planning_profile(
        self, athlete_alias: str
    ) -> AthletePlanningProfile | None:
        alias = quote(athlete_alias, safe="")
        response = self._request(
            "GET",
            "/onflows_athlete_settings?select=planning_profile"
            f"&athlete_alias=eq.{alias}&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        profile = row.get("planning_profile") if isinstance(row, Mapping) else None
        if profile is None:
            return None
        if not isinstance(profile, Mapping):
            raise PersistentStoreFailure("Stored planning profile is invalid")
        try:
            return AthletePlanningProfile.from_mapping(profile)
        except ValueError as exc:
            raise PersistentStoreFailure("Stored planning profile is invalid") from exc

    def save_athlete_planning_profile(
        self, athlete_alias: str, profile: AthletePlanningProfile
    ) -> None:
        alias = quote(athlete_alias, safe="")
        self._request(
            "PATCH",
            f"/onflows_athlete_settings?athlete_alias=eq.{alias}",
            json={
                "planning_profile": profile.to_payload(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Prefer": "return=minimal"},
        )

    def athlete_mesocycle_accent_preferences(
        self, athlete_alias: str
    ) -> AthleteMesocycleAccentPreferences | None:
        alias = quote(athlete_alias, safe="")
        response = self._request(
            "GET",
            "/onflows_athlete_settings?select=mesocycle_accent_preferences"
            f"&athlete_alias=eq.{alias}&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        preferences = (
            row.get("mesocycle_accent_preferences")
            if isinstance(row, Mapping)
            else None
        )
        if preferences is None:
            return None
        if not isinstance(preferences, Mapping):
            raise PersistentStoreFailure(
                "Stored mesocycle accent preferences are invalid"
            )
        try:
            return AthleteMesocycleAccentPreferences.from_mapping(preferences)
        except ValueError as exc:
            raise PersistentStoreFailure(
                "Stored mesocycle accent preferences are invalid"
            ) from exc

    def save_athlete_mesocycle_accent_preferences(
        self,
        athlete_alias: str,
        preferences: AthleteMesocycleAccentPreferences,
    ) -> None:
        alias = quote(athlete_alias, safe="")
        self._request(
            "PATCH",
            f"/onflows_athlete_settings?athlete_alias=eq.{alias}",
            json={
                "mesocycle_accent_preferences": preferences.to_payload(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Prefer": "return=minimal"},
        )

    def athlete_planning_calendar(
        self, athlete_alias: str
    ) -> AthletePlanningCalendar | None:
        alias = quote(athlete_alias, safe="")
        response = self._request(
            "GET",
            "/onflows_athlete_settings?select=planning_calendar"
            f"&athlete_alias=eq.{alias}&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        calendar = row.get("planning_calendar") if isinstance(row, Mapping) else None
        if calendar is None:
            return None
        if not isinstance(calendar, Mapping):
            raise PersistentStoreFailure("Stored planning calendar is invalid")
        try:
            return AthletePlanningCalendar.from_mapping(calendar)
        except ValueError as exc:
            raise PersistentStoreFailure("Stored planning calendar is invalid") from exc

    def save_athlete_planning_calendar(
        self, athlete_alias: str, calendar: AthletePlanningCalendar
    ) -> None:
        alias = quote(athlete_alias, safe="")
        self._request(
            "PATCH",
            f"/onflows_athlete_settings?athlete_alias=eq.{alias}",
            json={
                "planning_calendar": calendar.to_payload(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Prefer": "return=minimal"},
        )

    def latest(self, athlete_alias: str) -> Mapping[str, Any] | None:
        if self._generation_reads:
            analysis = self.active_analysis(athlete_alias)
            return (
                dict(analysis["snapshot_payload"])
                if analysis is not None
                else None
            )
        alias = quote(athlete_alias, safe="")
        response = self._request(
            "GET",
            "/onflows_training_snapshots?select=payload"
            f"&athlete_alias=eq.{alias}&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        snapshot = row.get("payload") if isinstance(row, Mapping) else None
        if not isinstance(snapshot, Mapping):
            raise PersistentStoreFailure("Stored training snapshot is invalid")
        return dict(snapshot)

    def latest_envelope(self, athlete_alias: str) -> Mapping[str, Any] | None:
        analysis = self.active_analysis(athlete_alias)
        if analysis is None:
            return None
        return {
            "payload": dict(analysis["snapshot_payload"]),
            "generation_id": analysis["generation_id"],
            "revision": analysis["revision"],
            "activated_at": analysis["activated_at"],
        }

    def active_analysis(self, athlete_alias: str) -> Mapping[str, Any] | None:
        row = self._rpc_row(
            "active_onflows_analysis", {"p_athlete_alias": athlete_alias}
        )
        if row is None:
            return None
        snapshot = row.get("snapshot_payload")
        revision = row.get("revision")
        generation_id = row.get("generation_id")
        if (
            not isinstance(snapshot, Mapping)
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
            or (generation_id is not None and not isinstance(generation_id, str))
        ):
            raise PersistentStoreFailure("Stored active analysis is invalid")
        return {
            "generation_id": generation_id,
            "revision": revision,
            "analysis_as_of": row.get("analysis_as_of"),
            "activated_at": row.get("activated_at"),
            "snapshot_payload": dict(snapshot),
        }

    def active_activity_calendar(
        self, athlete_alias: str, period_start: date, period_end: date
    ) -> Mapping[str, Any] | None:
        row = self._rpc_row(
            "active_onflows_activity_calendar",
            {
                "p_athlete_alias": athlete_alias,
                "p_period_start": period_start.isoformat(),
                "p_period_end": period_end.isoformat(),
            },
        )
        if row is None:
            return None
        activities = row.get("activities")
        snapshot = row.get("snapshot_payload")
        revision = row.get("revision")
        if (
            not isinstance(activities, list)
            or not all(isinstance(activity, Mapping) for activity in activities)
            or not isinstance(snapshot, Mapping)
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
        ):
            raise PersistentStoreFailure("Stored active activity calendar is invalid")
        return {
            "generation_id": row.get("generation_id"),
            "revision": revision,
            "analysis_as_of": row.get("analysis_as_of"),
            "activated_at": row.get("activated_at"),
            "snapshot_payload": dict(snapshot),
            "activities": [dict(activity) for activity in activities],
        }

    def active_activity_view(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
        row = self._rpc_row(
            "active_onflows_activity_view",
            {
                "p_athlete_alias": athlete_alias,
                "p_activity_ref": activity_ref,
            },
        )
        if row is None:
            return None
        catalog = row.get("catalog_payload")
        series = row.get("series_payload")
        shadow = row.get("shadow_payload")
        if not isinstance(catalog, Mapping):
            raise PersistentStoreFailure("Pinned activity view is invalid")
        for key, payload in (
            ("input_key", series),
            ("shadow_run_key", shadow),
        ):
            pointer = row.get(key)
            if pointer is not None and (
                not isinstance(pointer, str)
                or len(pointer) != 64
                or not isinstance(payload, Mapping)
            ):
                raise PersistentStoreFailure("Pinned activity view is incomplete")
            if pointer is None and payload is not None:
                raise PersistentStoreFailure("Pinned activity view is inconsistent")
        canonical_key = row.get("canonical_run_key")
        if canonical_key is not None and (
            not isinstance(canonical_key, str) or len(canonical_key) != 64
        ):
            raise PersistentStoreFailure("Pinned activity view is invalid")
        return {
            **dict(row),
            "catalog_payload": dict(catalog),
            "series_payload": dict(series) if isinstance(series, Mapping) else None,
            "shadow_payload": dict(shadow) if isinstance(shadow, Mapping) else None,
        }

    def replace(self, athlete_alias: str, snapshot: Mapping[str, Any]) -> None:
        """Legacy revision-zero projection retained for rollout compatibility."""

        self._request(
            "POST",
            "/onflows_training_snapshots?on_conflict=athlete_alias",
            json={
                "athlete_alias": athlete_alias,
                "payload": dict(snapshot),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def enqueue_sync_job(
        self,
        *,
        athlete_alias: str,
        job_kind: str,
        idempotency_key: str,
        request_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        row = self._rpc_row(
            "enqueue_onflows_sync_job",
            {
                "p_athlete_alias": athlete_alias,
                "p_job_kind": job_kind,
                "p_idempotency_key": idempotency_key,
                "p_request_payload": dict(request_payload),
            },
        )
        if row is None or not isinstance(row.get("job_id"), str):
            raise PersistentStoreFailure("Sync job could not be enqueued")
        return dict(row)

    def sync_state(self, athlete_alias: str) -> Mapping[str, Any]:
        row = self._rpc_row(
            "onflows_sync_state", {"p_athlete_alias": athlete_alias}
        )
        if row is None:
            raise PersistentStoreFailure("Sync state is unavailable")
        revision = row.get("active_revision")
        request_sequence = row.get("request_sequence")
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
            or isinstance(request_sequence, bool)
            or not isinstance(request_sequence, int)
            or request_sequence < 0
        ):
            raise PersistentStoreFailure("Stored sync state is invalid")
        return dict(row)

    def claim_sync_job(
        self, *, worker_id: str, lease_seconds: int = 300
    ) -> Mapping[str, Any] | None:
        return self._rpc_row(
            "claim_onflows_sync_job",
            {
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
            },
        )

    def renew_sync_lease(
        self,
        *,
        job_id: str,
        generation_id: str,
        lease_token: str,
        lease_seconds: int = 300,
    ) -> bool:
        response = self._request(
            "POST",
            "/rpc/renew_onflows_sync_lease",
            json={
                "p_job_id": job_id,
                "p_generation_id": generation_id,
                "p_lease_token": lease_token,
                "p_lease_seconds": lease_seconds,
            },
        )
        value = self._json(response)
        if not isinstance(value, bool):
            raise PersistentStoreFailure("Sync lease response is invalid")
        return value

    def stage_analysis_generation(
        self,
        *,
        job_id: str,
        generation_id: str,
        lease_token: str,
        snapshot_payload: Mapping[str, Any],
        snapshot_hash: str,
        period_start: date,
        period_end: date,
        as_of: date,
        provenance: Mapping[str, Any],
        activities: list[Mapping[str, Any]],
        inherit_activities: bool = False,
    ) -> Mapping[str, Any]:
        normalized: list[dict[str, Any]] = []
        for activity in activities:
            source = activity.get("catalog_payload")
            catalog_payload = (
                dict(source) if isinstance(source, Mapping) else dict(activity)
            )
            activity_ref = activity.get("activity_ref") or catalog_payload.get(
                "activity_ref"
            )
            if not isinstance(activity_ref, str) or not activity_ref:
                raise PersistentStoreFailure(
                    "Generation activity identity is invalid"
                )
            normalized.append(
                {
                    "activity_ref": activity_ref,
                    "start_at_utc": activity.get(
                        "start_at_utc", catalog_payload.get("start_at_utc")
                    ),
                    "local_date": activity.get(
                        "local_date", catalog_payload.get("local_date")
                    ),
                    "catalog_payload": catalog_payload,
                    "payload_hash": activity.get("payload_hash")
                    or self._canonical_payload_hash(catalog_payload),
                    "input_key": activity.get("input_key"),
                    "canonical_run_key": activity.get("canonical_run_key")
                    or catalog_payload.get("latest_canonical_run_key"),
                    "shadow_run_key": activity.get("shadow_run_key")
                    or catalog_payload.get("latest_shadow_run_key"),
                }
            )
        row = self._rpc_row(
            "stage_onflows_analysis_generation",
            {
                "p_job_id": job_id,
                "p_generation_id": generation_id,
                "p_lease_token": lease_token,
                "p_snapshot_payload": dict(snapshot_payload),
                "p_snapshot_hash": snapshot_hash,
                "p_period_start": period_start.isoformat(),
                "p_period_end": period_end.isoformat(),
                "p_as_of": as_of.isoformat(),
                "p_provenance": dict(provenance),
                "p_activities": normalized,
                "p_inherit_activities": inherit_activities,
            },
        )
        if row is None or row.get("outcome") not in {"READY", "ALREADY_READY"}:
            raise PersistentStoreFailure("Analysis generation could not be staged")
        return dict(row)

    def activate_analysis_generation(
        self, *, job_id: str, generation_id: str, lease_token: str
    ) -> Mapping[str, Any]:
        row = self._rpc_row(
            "activate_onflows_analysis_generation",
            {
                "p_job_id": job_id,
                "p_generation_id": generation_id,
                "p_lease_token": lease_token,
            },
        )
        if row is None or row.get("outcome") not in {
            "ACTIVATED",
            "STALE",
            "LEASE_LOST",
        }:
            raise PersistentStoreFailure("Analysis activation response is invalid")
        return dict(row)

    def fail_sync_job(
        self,
        *,
        job_id: str,
        generation_id: str,
        lease_token: str,
        error_code: str,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ) -> Mapping[str, Any]:
        row = self._rpc_row(
            "fail_onflows_sync_job",
            {
                "p_job_id": job_id,
                "p_generation_id": generation_id,
                "p_lease_token": lease_token,
                "p_error_code": error_code,
                "p_retryable": retryable,
                "p_retry_after_seconds": retry_after_seconds,
            },
        )
        if row is None or row.get("status") not in {
            "RETRY_WAIT",
            "FAILED",
            "SUPERSEDED",
            "LEASE_LOST",
        }:
            raise PersistentStoreFailure("Sync failure response is invalid")
        return dict(row)

    def rollback_analysis_generation(
        self, *, athlete_alias: str, target_generation_id: str
    ) -> Mapping[str, Any]:
        row = self._rpc_row(
            "rollback_onflows_analysis_generation",
            {
                "p_athlete_alias": athlete_alias,
                "p_target_generation_id": target_generation_id,
            },
        )
        if row is None or row.get("outcome") != "ROLLED_BACK":
            raise PersistentStoreFailure("Analysis rollback response is invalid")
        return dict(row)

    def prune_analysis_generations(
        self,
        *,
        athlete_alias: str,
        keep_superseded: int = 5,
        terminal_older_than_days: int = 30,
        batch_limit: int = 100,
    ) -> Mapping[str, int]:
        if (
            not 1 <= keep_superseded <= 50
            or not 30 <= terminal_older_than_days <= 365
            or not 1 <= batch_limit <= 1000
        ):
            raise ValueError("invalid analysis retention policy")
        row = self._rpc_row(
            "prune_onflows_analysis_generations",
            {
                "p_athlete_alias": athlete_alias,
                "p_keep_superseded": keep_superseded,
                "p_terminal_older_than": f"{terminal_older_than_days} days",
                "p_batch_limit": batch_limit,
            },
        )
        count_keys = (
            "deleted_generations",
            "deleted_activity_rows",
            "deleted_jobs",
            "deleted_shadow_runs",
            "deleted_canonical_runs",
            "deleted_model_inputs",
            "deleted_catalog_rows",
        )
        if row is None or any(
            isinstance(row.get(key), bool)
            or not isinstance(row.get(key), int)
            or int(row[key]) < 0
            for key in count_keys
        ):
            raise PersistentStoreFailure("Analysis retention response is invalid")
        return {key: int(row[key]) for key in count_keys}

    def publish_activity_shadow(
        self,
        *,
        athlete_alias: str,
        activity_ref: str,
        input_payload: Mapping[str, Any],
        derived_payload: Mapping[str, Any],
    ) -> str:
        """Atomically publish immutable input plus one append-only derived run."""
        input_hash = str(input_payload.get("input_hash") or "")
        result_hash = str(derived_payload.get("result_hash") or "")
        if len(input_hash) != 64 or len(result_hash) != 64:
            raise PersistentStoreFailure("Activity shadow hashes are invalid")
        input_key = hashlib.sha256(
            f"{athlete_alias}|{activity_ref}|{input_hash}".encode("utf-8")
        ).hexdigest()
        versions = "|".join(
            str(derived_payload.get(name) or "")
            for name in (
                "vflat_model_version",
                "vflat_config_version",
                "hrmod_model_version",
                "hrmod_config_version",
                "terrain_model_version",
            )
        )
        run_key = hashlib.sha256(
            f"{input_key}|{versions}|{result_hash}".encode("utf-8")
        ).hexdigest()
        self._request(
            "POST",
            "/rpc/publish_onflows_activity_shadow",
            json={
                "p_input_key": input_key,
                "p_athlete_alias": athlete_alias,
                "p_activity_ref": activity_ref,
                "p_input_hash": input_hash,
                "p_input_schema_version": str(input_payload.get("schema_version") or ""),
                "p_input_payload": dict(input_payload),
                "p_run_key": run_key,
                "p_result_hash": result_hash,
                "p_derived_schema_version": str(derived_payload.get("schema_version") or ""),
                "p_vflat_model_version": derived_payload.get("vflat_model_version"),
                "p_vflat_config_version": derived_payload.get("vflat_config_version"),
                "p_hrmod_model_version": derived_payload.get("hrmod_model_version"),
                "p_hrmod_config_version": derived_payload.get("hrmod_config_version"),
                "p_terrain_model_version": derived_payload.get("terrain_model_version"),
                "p_result_payload": dict(derived_payload),
            },
            headers={"Prefer": "return=minimal"},
            timeout=httpx.Timeout(60.0, connect=5.0),
        )
        return run_key

    def publish_canonical_activity_result(
        self,
        *,
        athlete_alias: str,
        activity_ref: str,
        scientific_input_hash: str,
        result_payload: Mapping[str, Any],
    ) -> str:
        """Publish one immutable canonical result and advance its catalog pointer."""

        if len(scientific_input_hash) != 64:
            raise PersistentStoreFailure(
                "Canonical scientific input hash is invalid"
            )
        rendered = json.dumps(
            dict(result_payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        result_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        run_key = hashlib.sha256(
            (
                f"{athlete_alias}|{activity_ref}|{scientific_input_hash}|"
                f"{result_hash}"
            ).encode("utf-8")
        ).hexdigest()
        self._request(
            "POST",
            "/rpc/publish_onflows_canonical_activity_result",
            json={
                "p_run_key": run_key,
                "p_athlete_alias": athlete_alias,
                "p_activity_ref": activity_ref,
                "p_scientific_input_hash": scientific_input_hash,
                "p_result_hash": result_hash,
                "p_schema_version": str(
                    result_payload.get("schema_version") or ""
                ),
                "p_model_version": str(
                    result_payload.get("model_version") or ""
                ),
                "p_result_payload": dict(result_payload),
            },
            headers={"Prefer": "return=minimal"},
        )
        return run_key

    def store_canonical_activity_result(
        self,
        *,
        athlete_alias: str,
        activity_ref: str,
        scientific_input_hash: str,
        result_payload: Mapping[str, Any],
    ) -> str:
        """Insert one immutable run without advancing the legacy catalog."""

        if len(scientific_input_hash) != 64:
            raise PersistentStoreFailure(
                "Canonical scientific input hash is invalid"
            )
        rendered = json.dumps(
            dict(result_payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        result_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        run_key = hashlib.sha256(
            (
                f"{athlete_alias}|{activity_ref}|{scientific_input_hash}|"
                f"{result_hash}"
            ).encode("utf-8")
        ).hexdigest()
        self._request(
            "POST",
            "/rpc/store_onflows_canonical_activity_result",
            json={
                "p_run_key": run_key,
                "p_athlete_alias": athlete_alias,
                "p_activity_ref": activity_ref,
                "p_scientific_input_hash": scientific_input_hash,
                "p_result_hash": result_hash,
                "p_schema_version": str(
                    result_payload.get("schema_version") or ""
                ),
                "p_model_version": str(result_payload.get("model_version") or ""),
                "p_result_payload": dict(result_payload),
            },
            headers={"Prefer": "return=minimal"},
        )
        return run_key

    def activity_shadow(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
        if self._generation_reads:
            pointer = self._active_activity_pointer(athlete_alias, activity_ref)
            if pointer is None or not pointer.get("shadow_run_key"):
                return None
            alias = quote(athlete_alias, safe="")
            reference = quote(activity_ref, safe="")
            run_key = quote(str(pointer["shadow_run_key"]), safe="")
            response = self._request(
                "GET",
                "/onflows_activity_derived_runs?select=result_payload"
                f"&run_key=eq.{run_key}&athlete_alias=eq.{alias}"
                f"&activity_ref=eq.{reference}&limit=1",
            )
            payload = self._json(response)
            if not isinstance(payload, list) or not payload:
                raise PersistentStoreFailure(
                    "Pinned activity shadow result is unavailable"
                )
            result = (
                payload[0].get("result_payload")
                if isinstance(payload[0], Mapping)
                else None
            )
            if not isinstance(result, Mapping):
                raise PersistentStoreFailure(
                    "Pinned activity shadow result is invalid"
                )
            return dict(result)
        alias = quote(athlete_alias, safe="")
        reference = quote(activity_ref, safe="")
        response = self._request(
            "GET",
            "/onflows_activity_derived_runs?select=result_payload,created_at"
            f"&athlete_alias=eq.{alias}&activity_ref=eq.{reference}"
            "&order=created_at.desc&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        result = row.get("result_payload") if isinstance(row, Mapping) else None
        if not isinstance(result, Mapping):
            raise PersistentStoreFailure("Stored activity shadow result is invalid")
        return dict(result)

    def activity_shadow_index(
        self, athlete_alias: str
    ) -> tuple[Mapping[str, Any], ...]:
        if self._generation_reads:
            return tuple(
                {
                    key: value
                    for key, value in row.items()
                    if key != "zone_summary"
                }
                for row in self._active_activity_shadow_rows(athlete_alias)
            )
        alias = quote(athlete_alias, safe="")
        selected = quote(
            "activity_ref,run_key,created_at,vflat_model_version,hrmod_model_version,terrain_model_version",
            safe=",",
        )
        response = self._request(
            "GET",
            f"/onflows_activity_derived_runs?select={selected}&athlete_alias=eq.{alias}"
            "&order=created_at.desc&limit=1000",
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise PersistentStoreFailure("Stored activity shadow index is invalid")
        latest: dict[str, dict[str, Any]] = {}
        for row in payload:
            if not isinstance(row, Mapping) or not isinstance(row.get("activity_ref"), str):
                raise PersistentStoreFailure("Stored activity shadow index is invalid")
            latest.setdefault(str(row["activity_ref"]), dict(row))
        return tuple(latest.values())

    def activity_shadow_zone_summaries(
        self, athlete_alias: str, activity_refs: tuple[str, ...]
    ) -> Mapping[str, list[Mapping[str, Any]]]:
        if not activity_refs:
            return {}
        if self._generation_reads:
            requested = set(activity_refs)
            result: dict[str, list[Mapping[str, Any]]] = {}
            for row in self._active_activity_shadow_rows(athlete_alias):
                activity_ref = row.get("activity_ref")
                zones = row.get("zone_summary")
                if activity_ref not in requested:
                    continue
                if not isinstance(zones, list) or not all(
                    isinstance(zone, Mapping) for zone in zones
                ):
                    raise PersistentStoreFailure(
                        "Pinned HRmod zone summaries are invalid"
                    )
                result[str(activity_ref)] = [dict(zone) for zone in zones]
            return result
        alias = quote(athlete_alias, safe="")
        response = self._request(
            "GET",
            "/onflows_activity_derived_runs?select=activity_ref,"
            "zone_summary:result_payload->zone_summary,created_at"
            f"&athlete_alias=eq.{alias}&order=created_at.desc&limit=1000",
        )
        payload = self._json(response)
        if not isinstance(payload, list):
            raise PersistentStoreFailure("Stored HRmod zone summaries are invalid")
        requested = set(activity_refs)
        latest: dict[str, list[Mapping[str, Any]]] = {}
        for row in payload:
            if not isinstance(row, Mapping):
                raise PersistentStoreFailure("Stored HRmod zone summaries are invalid")
            activity_ref = row.get("activity_ref")
            zones = row.get("zone_summary")
            if (
                not isinstance(activity_ref, str)
                or activity_ref not in requested
                or activity_ref in latest
            ):
                continue
            if zones is not None and not (
                isinstance(zones, list)
                and all(isinstance(zone, Mapping) for zone in zones)
            ):
                raise PersistentStoreFailure("Stored HRmod zone summaries are invalid")
            latest[activity_ref] = list(zones or [])
        return latest

    def resolve_activity_ref(
        self, athlete_alias: str, provider_activity_key: str
    ) -> str:
        response = self._request(
            "POST",
            "/rpc/resolve_onflows_activity_ref",
            json={
                "p_athlete_alias": athlete_alias,
                "p_provider_activity_key": provider_activity_key,
            },
        )
        payload = self._json(response)
        if not isinstance(payload, str) or not payload.startswith("act_"):
            raise PersistentStoreFailure("Stable activity identity could not be resolved")
        return payload

    def upsert_activity_catalog(
        self, athlete_alias: str, activities: list[Mapping[str, Any]]
    ) -> None:
        if not activities:
            return
        if any(
            len(str(activity.get("provider_activity_key") or "")) != 64
            for activity in activities
        ):
            raise PersistentStoreFailure(
                "Activity catalog provider identity is incomplete"
            )
        payload = [
            {
                **dict(activity),
                "athlete_alias": athlete_alias,
                "metadata_synced_at": datetime.now(timezone.utc).isoformat(),
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
            }
            for activity in activities
        ]
        self._request(
            "POST",
            "/onflows_activity_catalog?on_conflict=activity_ref",
            json=payload,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def activity_calendar(
        self, athlete_alias: str, period_start: Any, period_end: Any
    ) -> tuple[Mapping[str, Any], ...]:
        if self._generation_reads:
            calendar = self.active_activity_calendar(
                athlete_alias, period_start, period_end
            )
            if calendar is None:
                return ()
            return tuple(dict(row) for row in calendar["activities"])
        alias = quote(athlete_alias, safe="")
        selected = quote(
            "activity_ref,start_at_utc,start_local,local_date,timezone,utc_offset_minutes,"
            "sport,activity_type,activity_sub_type,name,moving_time_s,elapsed_time_s,"
            "recording_time_s,distance_m,elevation_gain_m,average_hr_bpm,max_hr_bpm,"
            "average_speed_mps,max_speed_mps,canonical_training_load,quality_status,"
            "quality_reason,hr_coverage_percent,canonical_summary,latest_shadow_run_key",
            safe=",",
        )
        response = self._request(
            "GET",
            f"/onflows_activity_catalog?select={selected}&athlete_alias=eq.{alias}"
            f"&local_date=gte.{period_start.isoformat()}&local_date=lte.{period_end.isoformat()}"
            "&order=start_at_utc.asc,activity_ref.asc&limit=1000",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not all(isinstance(row, Mapping) for row in payload):
            raise PersistentStoreFailure("Stored activity calendar is invalid")
        rows = [dict(row) for row in payload]
        if any(not row.get("latest_shadow_run_key") for row in rows):
            existing_runs = {
                str(run["activity_ref"]): str(run["run_key"])
                for run in self.activity_shadow_index(athlete_alias)
                if run.get("activity_ref") and run.get("run_key")
            }
            for row in rows:
                row["latest_shadow_run_key"] = row.get(
                    "latest_shadow_run_key"
                ) or existing_runs.get(str(row.get("activity_ref") or ""))
        return tuple(rows)

    def activity_detail(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
        if self._generation_reads:
            pointer = self._active_activity_pointer(athlete_alias, activity_ref)
            if pointer is None:
                return None
            catalog = pointer.get("catalog_payload")
            if not isinstance(catalog, Mapping):
                raise PersistentStoreFailure(
                    "Pinned activity catalog payload is invalid"
                )
            return {
                **dict(catalog),
                "activity_ref": activity_ref,
                "latest_canonical_run_key": pointer.get("canonical_run_key"),
                "latest_shadow_run_key": pointer.get("shadow_run_key"),
                "previous_activity_ref": pointer.get("previous_activity_ref"),
                "next_activity_ref": pointer.get("next_activity_ref"),
                "shadow_available": bool(pointer.get("shadow_run_key")),
            }
        alias = quote(athlete_alias, safe="")
        reference = quote(activity_ref, safe="")
        response = self._request(
            "GET",
            "/onflows_activity_catalog?select=*"
            f"&athlete_alias=eq.{alias}&activity_ref=eq.{reference}&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        if not isinstance(row, Mapping):
            raise PersistentStoreFailure("Stored activity detail is invalid")
        start = str(row.get("start_at_utc") or "")
        previous = self._request(
            "GET",
            "/onflows_activity_catalog?select=activity_ref"
            f"&athlete_alias=eq.{alias}&start_at_utc=lt.{quote(start, safe=':-.TZ')}"
            "&order=start_at_utc.desc,activity_ref.desc&limit=1",
        ) if start else None
        following = self._request(
            "GET",
            "/onflows_activity_catalog?select=activity_ref"
            f"&athlete_alias=eq.{alias}&start_at_utc=gt.{quote(start, safe=':-.TZ')}"
            "&order=start_at_utc.asc,activity_ref.asc&limit=1",
        ) if start else None
        previous_payload = self._json(previous) if previous is not None else []
        following_payload = self._json(following) if following is not None else []
        shadow_run_key = (
            row.get("latest_shadow_run_key")
            or self.latest_activity_shadow_run_key(athlete_alias, activity_ref)
        )
        return {
            **dict(row),
            "latest_shadow_run_key": shadow_run_key,
            "previous_activity_ref": (
                previous_payload[0].get("activity_ref")
                if isinstance(previous_payload, list) and previous_payload and isinstance(previous_payload[0], Mapping)
                else None
            ),
            "next_activity_ref": (
                following_payload[0].get("activity_ref")
                if isinstance(following_payload, list) and following_payload and isinstance(following_payload[0], Mapping)
                else None
            ),
            "shadow_available": bool(shadow_run_key),
        }

    def activity_series(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
        from .activity_catalog import downsample_model_input

        if self._generation_reads:
            pointer = self._active_activity_pointer(athlete_alias, activity_ref)
            if pointer is None or not pointer.get("input_key"):
                return None
            alias = quote(athlete_alias, safe="")
            reference = quote(activity_ref, safe="")
            input_key = quote(str(pointer["input_key"]), safe="")
            response = self._request(
                "GET",
                "/onflows_activity_model_inputs?select=input_payload"
                f"&input_key=eq.{input_key}&athlete_alias=eq.{alias}"
                f"&activity_ref=eq.{reference}&limit=1",
            )
            payload = self._json(response)
            if not isinstance(payload, list) or not payload:
                raise PersistentStoreFailure(
                    "Pinned activity series is unavailable"
                )
            source = (
                payload[0].get("input_payload")
                if isinstance(payload[0], Mapping)
                else None
            )
            if not isinstance(source, Mapping):
                raise PersistentStoreFailure("Pinned activity series is invalid")
            return downsample_model_input(source)
        alias = quote(athlete_alias, safe="")
        reference = quote(activity_ref, safe="")
        response = self._request(
            "GET",
            "/onflows_activity_model_inputs?select=input_payload,created_at"
            f"&athlete_alias=eq.{alias}&activity_ref=eq.{reference}"
            "&order=created_at.desc&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        source = row.get("input_payload") if isinstance(row, Mapping) else None
        if not isinstance(source, Mapping):
            raise PersistentStoreFailure("Stored activity series is invalid")
        return downsample_model_input(source)

    def latest_activity_input_hash(
        self, athlete_alias: str, activity_ref: str
    ) -> str | None:
        alias = quote(athlete_alias, safe="")
        reference = quote(activity_ref, safe="")
        response = self._request(
            "GET",
            "/onflows_activity_model_inputs?select=input_hash,created_at"
            f"&athlete_alias=eq.{alias}&activity_ref=eq.{reference}"
            "&order=created_at.desc&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        value = payload[0].get("input_hash") if isinstance(payload[0], Mapping) else None
        if not isinstance(value, str) or len(value) != 64:
            raise PersistentStoreFailure("Stored activity input hash is invalid")
        return value

    def latest_activity_shadow_run_metadata(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
        alias = quote(athlete_alias, safe="")
        reference = quote(activity_ref, safe="")
        response = self._request(
            "GET",
            "/onflows_activity_derived_runs?select=run_key,"
            "configuration_fingerprint:result_payload->>configuration_fingerprint,created_at"
            f"&athlete_alias=eq.{alias}&activity_ref=eq.{reference}"
            "&order=created_at.desc&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        row = payload[0]
        if not isinstance(row, Mapping):
            raise PersistentStoreFailure("Stored activity shadow metadata is invalid")
        run_key = row.get("run_key")
        fingerprint = row.get("configuration_fingerprint")
        if not isinstance(run_key, str) or len(run_key) != 64:
            raise PersistentStoreFailure("Stored activity shadow run key is invalid")
        if fingerprint is not None and (
            not isinstance(fingerprint, str) or len(fingerprint) != 64
        ):
            raise PersistentStoreFailure(
                "Stored activity shadow configuration fingerprint is invalid"
            )
        return {
            "run_key": run_key,
            "configuration_fingerprint": fingerprint,
        }

    def latest_activity_shadow_run_key(
        self, athlete_alias: str, activity_ref: str
    ) -> str | None:
        if self._generation_reads:
            pointer = self._active_activity_pointer(athlete_alias, activity_ref)
            value = pointer.get("shadow_run_key") if pointer is not None else None
            return str(value) if value else None
        alias = quote(athlete_alias, safe="")
        reference = quote(activity_ref, safe="")
        response = self._request(
            "GET",
            "/onflows_activity_derived_runs?select=run_key,created_at"
            f"&athlete_alias=eq.{alias}&activity_ref=eq.{reference}"
            "&order=created_at.desc&limit=1",
        )
        payload = self._json(response)
        if not isinstance(payload, list) or not payload:
            return None
        value = payload[0].get("run_key") if isinstance(payload[0], Mapping) else None
        if not isinstance(value, str) or len(value) != 64:
            raise PersistentStoreFailure("Stored activity shadow run key is invalid")
        return value

    def _active_activity_pointer(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
        row = self._rpc_row(
            "active_onflows_activity",
            {
                "p_athlete_alias": athlete_alias,
                "p_activity_ref": activity_ref,
            },
        )
        if row is None:
            return None
        catalog = row.get("catalog_payload")
        if not isinstance(catalog, Mapping):
            raise PersistentStoreFailure("Pinned activity pointer is invalid")
        for key in ("input_key", "canonical_run_key", "shadow_run_key"):
            value = row.get(key)
            if value is not None and (
                not isinstance(value, str) or len(value) != 64
            ):
                raise PersistentStoreFailure("Pinned activity pointer is invalid")
        return dict(row)

    def _active_activity_shadow_rows(
        self, athlete_alias: str
    ) -> list[Mapping[str, Any]]:
        rows = self._rpc_rows(
            "active_onflows_activity_shadow_index",
            {"p_athlete_alias": athlete_alias},
        )
        rendered: list[Mapping[str, Any]] = []
        for row in rows:
            activity_ref = row.get("activity_ref")
            run_key = row.get("run_key")
            if (
                not isinstance(activity_ref, str)
                or not isinstance(run_key, str)
                or len(run_key) != 64
            ):
                raise PersistentStoreFailure(
                    "Pinned activity shadow index is invalid"
                )
            rendered.append(dict(row))
        return rendered


__all__ = [
    "OAuthConnection",
    "PendingOAuthState",
    "PersistentStoreConfigurationError",
    "PersistentStoreFailure",
    "SupabasePilotRepository",
    "TokenCipher",
]
