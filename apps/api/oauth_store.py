"""Persistent, server-only storage for OAuth grants and aggregate snapshots.

The Supabase secret key and the token-encryption key never cross this module's
server boundary.  Raw provider payloads are deliberately not persisted.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
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
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        try:
            response = self._client.request(
                method, self._base_url + path, headers=headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise PersistentStoreFailure("Persistent store is unavailable") from exc
        if not 200 <= response.status_code < 300:
            raise PersistentStoreFailure(
                f"Persistent store request failed ({response.status_code})"
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

    def replace(self, athlete_alias: str, snapshot: Mapping[str, Any]) -> None:
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

    def activity_shadow(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
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
        alias = quote(athlete_alias, safe="")
        selected = quote(
            "activity_ref,run_key,created_at,vflat_model_version,hrmod_model_version,terrain_model_version",
            safe=",",
        )
        response = self._request(
            "GET",
            f"/onflows_activity_derived_runs?select={selected}&athlete_alias=eq.{alias}"
            "&order=created_at.desc&limit=250",
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
        return tuple(dict(row) for row in payload)

    def activity_detail(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
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
        return {
            **dict(row),
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
            "shadow_available": bool(row.get("latest_shadow_run_key")),
        }

    def activity_series(
        self, athlete_alias: str, activity_ref: str
    ) -> Mapping[str, Any] | None:
        from .activity_catalog import downsample_model_input

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


__all__ = [
    "OAuthConnection",
    "PendingOAuthState",
    "PersistentStoreConfigurationError",
    "PersistentStoreFailure",
    "SupabasePilotRepository",
    "TokenCipher",
]
