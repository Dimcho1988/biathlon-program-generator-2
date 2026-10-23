"""Immutable active-plan revisions and a compare-and-swap publication pointer."""
from urllib.parse import quote
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from .oauth_store import HistoricalGenerationReferenceConflict, PersistentStoreFailure


def _zoned_timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def _uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value.lower()
    except ValueError:
        return False


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

    def record_unchanged_check(self, alias, *, expected_revision, actor_id, expected_profile_revision, checkpoint):
        # The full candidate was compared by the lifecycle. Only its immutable
        # revision identity and the small publication checkpoint cross the wire.
        # POST has no transport retry; an ambiguous failure never falls back to
        # another plan save. The nonce also makes a future same-RPC replay safe.
        check_id = str(uuid4())
        result = self.request("POST", "/rpc/record_onflows_management_check", json={
            "p_alias": alias, "p_expected_revision": expected_revision, "p_actor": str(actor_id),
            "p_expected_profile_revision": expected_profile_revision, "p_checkpoint": checkpoint,
            "p_check_id": check_id,
        })
        if isinstance(result, dict) and result.get("conflict"):
            raise HTTPException(409, "The plan or its inputs changed; reload and try again")
        if (not isinstance(result, dict)
                or set(result) != {"unchanged", "checked_revision", "check_id", "recorded_at", "replayed"}
                or result["unchanged"] is not True
                or type(result["checked_revision"]) is not int or result["checked_revision"] < 1
                or result["checked_revision"] != expected_revision or result["check_id"] != check_id
                or not _zoned_timestamp(result["recorded_at"]) or type(result["replayed"]) is not bool):
            raise PersistentStoreFailure("Invalid unchanged plan check result")
        return result

    def checks(self, alias, *, before=None, limit=10):
        if type(limit) is not int or not 1 <= limit <= 50 or (before is not None and (type(before) is not int or before < 1)):
            raise ValueError("Invalid plan check page")
        path = f"/rpc/read_onflows_management_checks?p_alias={quote(alias, safe='')}&p_limit={limit}"
        if before is not None:
            path += f"&p_before={before}"
        result = self.request("GET", path)
        if not isinstance(result, dict) or set(result) != {"checks", "next_before"} or not isinstance(result["checks"], list):
            raise PersistentStoreFailure("Invalid plan check history")
        rows = result["checks"]
        previous, identifiers = before, set()
        if len(rows) > limit:
            raise PersistentStoreFailure("Invalid plan check history")
        for row in rows:
            if (not isinstance(row, dict)
                    or set(row) != {"id", "sequence", "checked_revision", "operation", "recorded_at", "reason"}
                    or not _uuid(row["id"]) or row["id"].lower() in identifiers
                    or type(row["sequence"]) is not int or not 1 <= row["sequence"] <= 9007199254740991
                    or (previous is not None and row["sequence"] >= previous)
                    or type(row["checked_revision"]) is not int or not 1 <= row["checked_revision"] <= 9007199254740991
                    or row["operation"] != "CHECK_NO_CHANGE" or not _zoned_timestamp(row["recorded_at"])
                    or not isinstance(row["reason"], str) or not row["reason"].strip() or len(row["reason"]) > 500):
                raise PersistentStoreFailure("Invalid plan check history")
            identifiers.add(row["id"].lower())
            previous = row["sequence"]
        cursor = result["next_before"]
        if cursor is not None and (type(cursor) is not int or not rows or len(rows) != limit or cursor != rows[-1]["sequence"]):
            raise PersistentStoreFailure("Invalid plan check history")
        return result

    def save(self, alias, payload, expected_revision, actor, operation, profile_revision, checkpoint, *, automatic=False,
             include_payload=True):
        # Only callers that discard the saved record opt out of its echo. The
        # immutable revision and publication checks are identical in both RPCs.
        rpc = "save_onflows_management_plan" if include_payload else "save_onflows_management_plan_compact"
        try:
            result = self.request("POST", f"/rpc/{rpc}", json={
                "p_alias": alias, "p_payload": payload, "p_expected_revision": expected_revision,
                "p_actor": str(actor), "p_operation": operation,
                "p_expected_profile_revision": profile_revision, "p_checkpoint": checkpoint, "p_automatic": automatic,
            })
        except HistoricalGenerationReferenceConflict as exc:
            raise HTTPException(409, "The plan or its inputs changed; reload and try again") from exc
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
