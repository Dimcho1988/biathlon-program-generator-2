#!/usr/bin/env python3
"""Local-only real PostgreSQL event commit-order regressions.

Reuse the existing synthetic history fixture and independent psql backends.
An early allocated event ID deliberately commits after a later event was
prepared. No sequence watermark may erase that late commit.
"""
import argparse
import json
import uuid

from history_generation_concurrency import Case, LOCAL_DSN, literal, local_dsn


def check_commit_order(dsn, commit_early):
    case = Case(dsn, "learning-events-" + str(commit_early).lower())
    try:
        early = case.session("early-event")
        later = case.session("later-event")
        worker = case.session("worker")
        alias = literal(case.a)
        key = literal("a" * 64)
        token = literal(str(uuid.uuid4())) + "::uuid"
        request = "select onflows_private.request_learning_preparation(" + alias + ");"
        early.run("begin; " + request)
        early_id = int(early.run("select id from onflows_private.learning_dirty_events where athlete_alias="
                                + alias + " and transaction_id=txid_current();")[0])
        # This must commit while the earlier alias event is still uncommitted.
        later.run(request)
        claim_sql = "select public.onflows_learning_preparation('CLAIM'," + alias + "," + token + ",p_model_key=>" + key + ");"
        claim = worker.json(claim_sql)
        assert claim is not None, "Visible event was not claimable"
        captured = worker.json("select to_jsonb(claimed_invalidations) from onflows_private.prepared_learning_views where athlete_alias=" + alias + ";")
        assert early_id not in captured and max(captured) > early_id, "Fixture did not establish out-of-order visibility"
        payload = literal(json.dumps({"athlete_alias": case.a, "report": {
            "schema_version": "recovery-learning-view-v1", "as_of": claim["day"], "series": []}})) + "::jsonb"
        def store(current):
            return worker.json("select public.onflows_learning_preparation('STORE'," + alias + "," + token
                + "," + str(current["revision"]) + "," + literal(current["day"]) + "::date," + key + "," + payload + ");")
        read = "select public.onflows_learning_preparation('READ'," + alias + ",p_model_key=>" + key + ");"
        assert store(claim).get("stored") is True
        assert worker.json(read).get("ready") is True, "Uncommitted input must not leak into the current view"
        early.run("commit;" if commit_early else "rollback;")
        if not commit_early:
            assert worker.json(read).get("ready") is True, "Rolled-back event invalidated a valid projection"
            return
        assert worker.json(read).get("ready") is False, "A late commit below the captured maximum was lost"
        assert worker.run("select exists(select 1 from onflows_private.learning_dirty_events where id=" + str(early_id) + ");") == ["t"]
        claim = worker.json(claim_sql)
        assert claim is not None, "Late event waited for the next local day"
        # Another committed input after CLAIM must fence even the fresh worker.
        later.run(request)
        assert store(claim).get("stored") is False, "Input committed during calculation was published"
        assert worker.json(read).get("ready") is False
        claim = worker.json(claim_sql)
        assert claim is not None
        assert store(claim).get("stored") is True
        assert worker.json(read).get("ready") is True
    finally:
        case.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", type=local_dsn, default=LOCAL_DSN)
    args = parser.parse_args()
    for commit in (False, True):
        check_commit_order(args.dsn, commit)
        print("PASS learning event commit order " + ("commit" if commit else "rollback"), flush=True)


if __name__ == "__main__":
    main()
