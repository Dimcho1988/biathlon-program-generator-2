"""Append-only planning profiles and reviewable, frozen management drafts.

Only the trusted API calls this store. The SQL RPC independently checks the
actor's planning access and rejects inputs changed during draft generation.
There is deliberately no activation, calendar publication or update operation.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import HTTPException

from .oauth_store import PersistentStoreFailure


PROFILE_KEY = "management-profile-v1"
MAX_HISTORY = 20
_SELECT = "kind,entry_key,revision,payload,actor_id,recorded_at"


class ManagementStore:
    def __init__(self, repository):
        self.repository = repository

    def _rows(self, alias, kind, limit, *, entry_key=None):
        selected_key = PROFILE_KEY if kind == "PROFILE" else entry_key
        key_filter = f"&entry_key=eq.{quote(selected_key, safe='')}" if selected_key is not None else ""
        ordering = "revision.desc" if kind == "PROFILE" else "recorded_at.desc,entry_key.desc,revision.desc"
        result = self.repository._json(self.repository._request(
            "GET", f"/onflows_management_entries?select={_SELECT}"
            f"&athlete_alias=eq.{quote(alias, safe='')}&kind=eq.{kind}{key_filter}"
            f"&order={ordering}&limit={limit}",
        ))
        if not isinstance(result, list) or len(result) > limit:
            raise PersistentStoreFailure("Invalid management history")
        for row in result:
            if (not isinstance(row, dict) or row.get("kind") != kind
                    or not isinstance(row.get("entry_key"), str)
                    or type(row.get("revision")) is not int or row["revision"] < 1
                    or not isinstance(row.get("payload"), dict)
                    or (selected_key is not None and row["entry_key"] != selected_key)):
                raise PersistentStoreFailure("Invalid management history")
        return result

    def profile(self, alias):
        rows = self._rows(alias, "PROFILE", 1)
        if not rows:
            return {"configured": False, "profile": None, "revision": 0}
        return {"configured": True, "profile": rows[0]["payload"], "revision": rows[0]["revision"]}

    def progression_reference(self, alias):
        # Only the last server-created draft's small anchor, not twenty plans.
        result = self.repository._json(self.repository._request(
            "GET", "/onflows_management_entries?select=payload->parameters->load_progression->anchor"
            f"&athlete_alias=eq.{quote(alias, safe='')}&kind=eq.DRAFT"
            "&order=recorded_at.desc,revision.desc&limit=1"))
        if not isinstance(result, list):
            raise PersistentStoreFailure("Invalid progression reference")
        value = result[0].get("anchor") if result else None
        return value if isinstance(value, dict) else None

    def drafts(self, alias, limit=10, *, start_date: date | None = None):
        if type(limit) is not int:
            raise ValueError("History limit must be an integer")
        if start_date is not None and type(start_date) is not date:
            raise ValueError("Draft history start_date must be a calendar date")
        return self._rows(alias, "DRAFT", max(1, min(limit, MAX_HISTORY)),
                          entry_key=start_date.isoformat() if start_date is not None else None)

    def _save(self, alias, kind, key, payload, revision, actor, *,
              profile_revision=None, expected_generation_id=None, check_generation=False):
        result = self.repository._json(self.repository._request(
            "POST", "/rpc/save_onflows_management_entry", json={
                "p_alias": alias, "p_kind": kind, "p_key": key, "p_payload": payload,
                "p_expected_revision": revision, "p_actor": str(actor),
                "p_expected_profile_revision": profile_revision,
                "p_expected_generation_id": str(expected_generation_id) if expected_generation_id is not None else None,
                "p_check_generation": check_generation,
            },
        ))
        if not isinstance(result, dict):
            raise PersistentStoreFailure("Invalid management save result")
        if result.get("conflict"):
            messages = {
                "PROFILE_CHANGED": "Planning profile changed; regenerate the draft",
                "ANALYSIS_CHANGED": "Athlete analysis changed; regenerate the draft",
                "REVISION_CHANGED": "Planning input changed; reload before saving",
            }
            raise HTTPException(409, messages.get(result.get("reason"), messages["REVISION_CHANGED"]))
        if (result.get("saved") is not True or type(result.get("revision")) is not int
                or result["revision"] < 1 or result.get("entry_key") != key
                or not isinstance(result.get("payload"), dict)
                or not isinstance(result.get("recorded_at"), str)):
            raise PersistentStoreFailure("Invalid management save result")
        return result

    def save_profile(self, alias, payload, expected_revision, actor):
        return self._save(alias, "PROFILE", PROFILE_KEY, payload, expected_revision, actor)

    def save_draft(self, alias, payload, actor, expected_profile_revision, expected_revision=0, *,
                   expected_generation_id=None, check_generation=False):
        key = payload.get("start_date") if isinstance(payload, dict) else None
        try:
            if not isinstance(key, str) or date.fromisoformat(key).isoformat() != key:
                raise ValueError
        except ValueError as exc:
            raise ValueError("A draft requires an ISO start_date") from exc
        return self._save(
            alias, "DRAFT", key, payload, expected_revision, actor,
            profile_revision=expected_profile_revision,
            expected_generation_id=expected_generation_id, check_generation=check_generation,
        )
