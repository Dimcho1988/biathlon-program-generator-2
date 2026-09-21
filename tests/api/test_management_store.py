from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import HTTPException

from apps.api.management_store import ManagementStore, PROFILE_KEY
from apps.api.oauth_store import PersistentStoreFailure


ACTOR = UUID("11111111-1111-4111-8111-111111111111")
STAMP = "2026-09-21T12:00:00+00:00"


class Repository:
    def __init__(self, rows=None, save_result=None):
        self.rows = rows or []
        self.save_result = save_result
        self.requests = []

    def _request(self, method, path, **kwargs):
        self.requests.append((method, path, deepcopy(kwargs)))
        if method == "GET":
            return deepcopy(self.rows)
        if self.save_result is not None:
            return deepcopy(self.save_result)
        body = kwargs["json"]
        return {"saved": True, "revision": body["p_expected_revision"] + 1,
                "entry_key": body["p_key"], "payload": deepcopy(body["p_payload"]), "recorded_at": STAMP}

    def _json(self, value):
        return value


def entry(kind="PROFILE", revision=2, key=None):
    return {"kind": kind, "entry_key": key or (PROFILE_KEY if kind == "PROFILE" else "2026-09-21"),
            "revision": revision, "payload": {"sport": "Run"}, "actor_id": str(ACTOR), "recorded_at": STAMP}


def test_missing_profile_is_explicit_and_read_is_scoped():
    repo = Repository()
    assert ManagementStore(repo).profile("ath&a=1") == {"configured": False, "profile": None, "revision": 0}
    method, path, _ = repo.requests[0]
    assert method == "GET" and "athlete_alias=eq.ath%26a%3D1" in path
    assert "kind=eq.PROFILE" in path and f"entry_key=eq.{PROFILE_KEY}" in path
    assert "order=revision.desc&limit=1" in path


def test_profile_returns_latest_revision_without_creating_defaults():
    repo = Repository([entry()])
    assert ManagementStore(repo).profile("ath-test") == {
        "configured": True, "profile": {"sport": "Run"}, "revision": 2,
    }
    assert len(repo.requests) == 1


def test_history_is_bounded_and_keeps_multiple_immutable_revisions():
    rows = [entry("DRAFT", 2), entry("DRAFT", 1)]
    repo = Repository(rows)
    assert ManagementStore(repo).drafts("ath-test", 1000) == rows
    assert "athlete_alias=eq.ath-test&kind=eq.DRAFT" in repo.requests[0][1]
    assert "order=recorded_at.desc,entry_key.desc,revision.desc&limit=20" in repo.requests[0][1]
    empty = Repository()
    ManagementStore(empty).drafts("ath-test", 0)
    assert "limit=1" in empty.requests[0][1]
    with pytest.raises(ValueError):
        ManagementStore(empty).drafts("ath-test", "10")


def test_selected_start_history_is_scoped_before_limiting_and_keeps_revisions():
    rows = [entry("DRAFT", 12), entry("DRAFT", 11)]
    repo = Repository(rows)
    result = ManagementStore(repo).drafts("ath&a=1", 2, start_date=date(2026, 9, 21))
    assert result == rows
    path = repo.requests[0][1]
    assert "athlete_alias=eq.ath%26a%3D1&kind=eq.DRAFT&entry_key=eq.2026-09-21" in path
    assert "limit=2" in path
    # A version for a different week cannot silently become this week's version.
    repo.rows = [entry("DRAFT", 99, "2026-09-22")]
    with pytest.raises(PersistentStoreFailure, match="Invalid management history"):
        ManagementStore(repo).drafts("ath-test", start_date=date(2026, 9, 21))


@pytest.mark.parametrize("start", ["2026-09-21", "20260921", "2026-09-21&limit=10000", datetime(2026, 9, 21)])
def test_selected_start_history_requires_a_parsed_calendar_date(start):
    repo = Repository()
    with pytest.raises(ValueError, match="calendar date"):
        ManagementStore(repo).drafts("ath-test", start_date=start)
    assert repo.requests == []


@pytest.mark.parametrize("rows", [{"error": "private"}, [None], [entry(revision=0)],
                                 [entry(revision=True)], [entry(kind="DRAFT")],
                                 [{**entry(), "payload": []}], [entry(), entry()]])
def test_malformed_profile_storage_never_becomes_a_configured_profile(rows):
    with pytest.raises(PersistentStoreFailure, match="Invalid management history"):
        ManagementStore(Repository(rows)).profile("ath-test")


def test_profile_write_pins_actor_and_expected_revision():
    repo = Repository()
    payload = {"sport": "Run", "weekly_minutes": 300}
    before = deepcopy(payload)
    result = ManagementStore(repo).save_profile("ath-test", payload, 2, ACTOR)
    method, path, options = repo.requests[0]
    assert method == "POST" and path == "/rpc/save_onflows_management_entry"
    assert options["json"] == {
        "p_alias": "ath-test", "p_kind": "PROFILE", "p_key": PROFILE_KEY, "p_payload": payload,
        "p_expected_revision": 2, "p_actor": str(ACTOR), "p_expected_profile_revision": None,
        "p_expected_generation_id": None, "p_check_generation": False,
    }
    assert result["revision"] == 3 and result["payload"] == payload and payload == before


@pytest.mark.parametrize("generation", [None, "22222222-2222-4222-8222-222222222222"])
def test_draft_write_pins_profile_and_explicit_null_or_nonnull_generation(generation):
    repo = Repository()
    result = ManagementStore(repo).save_draft(
        "ath-test", {"start_date": "2026-09-21", "days": []}, ACTOR, 3, 2,
        expected_generation_id=generation, check_generation=True,
    )
    body = repo.requests[0][2]["json"]
    assert body["p_kind"] == "DRAFT" and body["p_key"] == "2026-09-21"
    assert body["p_expected_profile_revision"] == 3 and body["p_expected_revision"] == 2
    assert body["p_expected_generation_id"] == generation and body["p_check_generation"] is True
    assert result["revision"] == 3 and result["recorded_at"] == STAMP


@pytest.mark.parametrize("start", [None, "20260921", "2026-02-30", "2026-09-21T00:00:00", 20260921])
def test_invalid_draft_identity_is_rejected_before_storage(start):
    repo = Repository()
    with pytest.raises(ValueError, match="ISO start_date"):
        ManagementStore(repo).save_draft("ath-test", {"start_date": start}, ACTOR, 1)
    assert repo.requests == []


@pytest.mark.parametrize("reason,message", [
    ("PROFILE_CHANGED", "Planning profile changed"),
    ("ANALYSIS_CHANGED", "Athlete analysis changed"),
    ("REVISION_CHANGED", "Planning input changed"),
])
def test_optimistic_conflicts_are_retriable_409_without_saving_again(reason, message):
    repo = Repository(save_result={"conflict": True, "reason": reason, "revision": 4})
    with pytest.raises(HTTPException) as error:
        ManagementStore(repo).save_profile("ath-test", {}, 3, ACTOR)
    assert error.value.status_code == 409 and message in error.value.detail
    assert len(repo.requests) == 1


@pytest.mark.parametrize("result", [[], {}, {"saved": False}, {"saved": True, "revision": 0},
                                   {"saved": True, "revision": 1, "entry_key": "wrong"}])
def test_malformed_save_cannot_be_reported_as_success(result):
    with pytest.raises(PersistentStoreFailure, match="Invalid management save result"):
        ManagementStore(Repository(save_result=result)).save_profile("ath-test", {}, 0, ACTOR)


def test_migration_contract_restricts_access_and_preserves_history():
    files = list(Path("supabase/migrations").glob("*_management_drafts_v1.sql"))
    assert len(files) == 1
    sql = files[0].read_text().lower()
    assert "alter table public.onflows_management_entries enable row level security" in sql
    assert "revoke all on public.onflows_management_entries from public, anon, authenticated, service_role" in sql
    assert "grant select, insert on public.onflows_management_entries to service_role" in sql
    assert "security invoker set search_path = ''" in sql
    assert "security definer" not in sql and "user_metadata" not in sql
    assert "g.edit_plan" in sql and "assignment.can_edit_plan" in sql
    assert "'admin', 'head_coach'" in sql
    assert "c.role = 'coach' and exists(" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "current_profile_revision <> p_expected_profile_revision" in sql
    assert "active_generation is distinct from p_expected_generation_id" in sql
    assert "'read_comparison'" in sql
    assert "insert into public.onflows_athlete_analysis_state" not in sql
    assert "for share" not in sql and "for update" not in sql
    assert "'persistence'" in sql
    assert "update public.onflows_management_entries" not in sql
    assert "delete from public.onflows_management_entries" not in sql
    assert "insert into public.onflows_management_entries" in sql
