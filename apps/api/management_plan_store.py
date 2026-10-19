"""Immutable active-plan revisions and a compare-and-swap publication pointer."""
from urllib.parse import quote
from datetime import datetime

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

    def current(self, alias, *, metadata_only=False):
        select = (
            "revision,status:payload->status,fingerprint:payload->processed_input_fingerprint,"
            "plan_generation_id:payload->plan->input_snapshot->generation_id,"
            "plan_analysis_revision:payload->plan->input_snapshot->analysis_revision,"
            "plan_analysis_as_of:payload->plan->input_snapshot->analysis_as_of,"
            "plan_snapshot_fingerprint:payload->plan->input_snapshot->snapshot_fingerprint,"
            "proposal_generation_id:payload->proposal->input_snapshot->generation_id,"
            "proposal_analysis_revision:payload->proposal->input_snapshot->analysis_revision,"
            "proposal_analysis_as_of:payload->proposal->input_snapshot->analysis_as_of,"
            "proposal_snapshot_fingerprint:payload->proposal->input_snapshot->snapshot_fingerprint"
        ) if metadata_only else "revision,operation,payload,recorded_at"
        # Read the publication pointer and its immutable record in one database
        # snapshot. Keep the default LEFT embedding: an invalid/inaccessible
        # record must fail validation rather than look like a missing plan.
        rows = self.request("GET", "/onflows_management_plan_state?select=revision,"
                            "record:onflows_management_plan_revisions!"
                            f"onflows_management_plan_state_athlete_alias_revision_fkey({select})"
                            f"&athlete_alias=eq.{quote(alias, safe='')}&limit=1")
        if not isinstance(rows, list):
            raise PersistentStoreFailure("Invalid active plan pointer")
        if not rows:
            return None
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise PersistentStoreFailure("Invalid active plan pointer")
        revision = rows[0].get("revision")
        if type(revision) is not int or revision < 1:
            raise PersistentStoreFailure("Invalid active plan revision")
        record = rows[0].get("record")
        if (not isinstance(record, dict) or type(record.get("revision")) is not int
                or record["revision"] != revision
                or (not metadata_only and not isinstance(record.get("payload"), dict))):
            raise PersistentStoreFailure("Invalid active plan record")
        if metadata_only and (not isinstance(record.get("fingerprint"), str) or not isinstance(record.get("status"), str)):
            raise PersistentStoreFailure("Invalid active plan metadata")
        return record

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

    def current_overview(self, alias):
        from .management_overview import validate_overview
        result = self.request("GET", "/rpc/read_onflows_management_overview"
                              f"?p_alias={quote(alias, safe='')}")
        return None if result is None else validate_overview(result)

    def save(self, alias, payload, expected_revision, actor, operation, profile_revision, checkpoint, *, automatic=False,
             include_payload=True):
        # Only callers that discard the saved record opt out of its echo. The
        # immutable revision and publication checks are identical in both RPCs.
        rpc = "save_onflows_management_plan" if include_payload else "save_onflows_management_plan_compact"
        result = self.request("POST", f"/rpc/{rpc}", json={
            "p_alias": alias, "p_payload": payload, "p_expected_revision": expected_revision,
            "p_actor": str(actor), "p_operation": operation,
            "p_expected_profile_revision": profile_revision, "p_checkpoint": checkpoint, "p_automatic": automatic,
        })
        if not isinstance(result, dict):
            raise PersistentStoreFailure("Invalid active plan save result")
        if result.get("conflict"):
            raise HTTPException(409, "The plan or its inputs changed; reload and try again")
        if (result.get("saved") is not True or type(result.get("revision")) is not int
                or (include_payload and not isinstance(result.get("payload"), dict))):
            raise PersistentStoreFailure("Invalid active plan save result")
        if not include_payload:
            try:
                recorded_at = datetime.fromisoformat(result["recorded_at"])
            except (KeyError, TypeError, ValueError):
                raise PersistentStoreFailure("Invalid active plan save result") from None
            if (result["revision"] < 1 or result["revision"] != expected_revision + 1
                    or recorded_at.utcoffset() is None or "payload" in result):
                raise PersistentStoreFailure("Invalid active plan save result")
        return result

    def due(self, now):
        rows = self.request("GET", "/onflows_management_plan_state?select=athlete_alias,revision,approved_by"
                            f"&status=in.(ACTIVE,REVIEW_REQUIRED)&next_check_at=lte.{quote(now.isoformat(), safe='')}"
                            "&order=next_check_at.asc&limit=1")
        if not isinstance(rows, list):
            raise PersistentStoreFailure("Invalid due plan queue")
        return rows

    def defer_check(self, alias, revision):
        # This void RPC returns HTTP 204. Decoding JSON turns a successful
        # deferral into a false store failure and repeats it in the error path.
        self.repository._request("POST", "/rpc/defer_onflows_management_check",
                                 json={"p_alias": alias, "p_revision": revision})

    def queue_import(self, alias):
        return self.request("POST", "/rpc/queue_onflows_management_import", json={"p_alias": alias})
