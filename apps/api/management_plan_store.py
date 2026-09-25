"""Immutable active-plan revisions and a compare-and-swap publication pointer."""
from urllib.parse import quote

from fastapi import HTTPException
from .oauth_store import PersistentStoreFailure


class PlanStore:
    def __init__(self, repository):
        self.repository = repository

    def request(self, method, path, **kwargs):
        return self.repository._json(self.repository._request(method, path, **kwargs))

    def checkpoint(self, alias):
        result = self.request("POST", "/rpc/onflows_management_checkpoint", json={"p_alias": alias})
        if not isinstance(result, dict) or not isinstance(result.get("models"), list):
            raise PersistentStoreFailure("Invalid planning checkpoint")
        return result

    def current(self, alias):
        rows = self.request("GET", "/onflows_management_plan_state?select=revision"
                            f"&athlete_alias=eq.{quote(alias, safe='')}&limit=1")
        if not isinstance(rows, list):
            raise PersistentStoreFailure("Invalid active plan pointer")
        if not rows:
            return None
        revision = rows[0].get("revision")
        if type(revision) is not int or revision < 1:
            raise PersistentStoreFailure("Invalid active plan revision")
        records = self.request("GET", "/onflows_management_plan_revisions?select=revision,operation,payload,recorded_at"
                               f"&athlete_alias=eq.{quote(alias, safe='')}&revision=eq.{revision}&limit=1")
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0].get("payload"), dict):
            raise PersistentStoreFailure("Invalid active plan record")
        return records[0]

    def history(self, alias):
        result = self.request("GET", "/onflows_management_plan_revisions?select=revision,operation,recorded_at,"
                              "status:payload->status,changes:payload->changes,reason:payload->reason"
                              f"&athlete_alias=eq.{quote(alias, safe='')}&order=revision.desc&limit=10")
        if not isinstance(result, list):
            raise PersistentStoreFailure("Invalid active plan history")
        # Full evidence remains in immutable storage; the UI gets a compact log.
        return [{"revision": r["revision"], "operation": r["operation"], "recorded_at": r["recorded_at"],
                 "status": r["status"], "changes": r.get("changes") or [],
                 "reason": r.get("reason")} for r in result]

    def save(self, alias, payload, expected_revision, actor, operation, profile_revision, checkpoint, *, automatic=False):
        result = self.request("POST", "/rpc/save_onflows_management_plan", json={
            "p_alias": alias, "p_payload": payload, "p_expected_revision": expected_revision,
            "p_actor": str(actor), "p_operation": operation,
            "p_expected_profile_revision": profile_revision, "p_checkpoint": checkpoint, "p_automatic": automatic,
        })
        if not isinstance(result, dict):
            raise PersistentStoreFailure("Invalid active plan save result")
        if result.get("conflict"):
            raise HTTPException(409, "The plan or its inputs changed; reload and try again")
        if result.get("saved") is not True or type(result.get("revision")) is not int or not isinstance(result.get("payload"), dict):
            raise PersistentStoreFailure("Invalid active plan save result")
        return result

    def due(self, now):
        rows = self.request("GET", "/onflows_management_plan_state?select=athlete_alias,revision,approved_by"
                            f"&status=in.(ACTIVE,REVIEW_REQUIRED)&next_check_at=lte.{quote(now.isoformat(), safe='')}"
                            "&order=next_check_at.asc&limit=10")
        if not isinstance(rows, list):
            raise PersistentStoreFailure("Invalid due plan queue")
        return rows

    def defer_check(self, alias, revision):
        self.request("POST", "/rpc/defer_onflows_management_check", json={"p_alias": alias, "p_revision": revision})

    def queue_import(self, alias):
        return self.request("POST", "/rpc/queue_onflows_management_import", json={"p_alias": alias})
