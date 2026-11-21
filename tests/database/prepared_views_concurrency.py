#!/usr/bin/env python3
"""Prepared page events must survive commit reordering without locking inputs."""
import argparse
import json
from uuid import uuid4

from history_generation_concurrency import Case, LOCAL_DSN, literal, local_dsn


def reordered_events(dsn):
    case = Case(dsn, "prepared-page-events")
    try:
        alias, key = literal(case.a), literal("c" * 64)
        scope = alias + ",'management-state'"
        read = "select public.onflows_prepared_view('READ'," + scope + ",p_model_key=>" + key + ");"
        case.control.json(read)
        case.control.run("delete from onflows_private.prepared_views where athlete_alias=" + alias
                         + " and kind<>'management-state';")
        first, later = case.session("early-uncommitted"), case.session("later-committed")
        event = "select onflows_private.request_prepared_views(" + alias + ",false);"
        first.run("begin; " + event)
        later.run(event)
        token = literal(str(uuid4()))
        claim_sql = "select public.onflows_prepared_view('CLAIM',p_alias=>" + alias + ",p_token=>" + token + ",p_model_key=>" + key + ");"

        def store(claim):
            return case.control.json("select public.onflows_prepared_view('STORE'," + scope
                + ",p_token=>" + token + ",p_revision=>" + str(claim["revision"])
                + ",p_day=>" + literal(claim["day"]) + "::date,p_model_key=>" + key
                + ",p_payload=>'{}'::jsonb);")

        claim = case.control.json(claim_sql)
        assert store(claim)["stored"]
        assert case.control.json(read)["ready"]
        first.run("commit;")
        assert not case.control.json(read)["ready"], "Late lower-ID event disappeared"
        newer = case.control.json(claim_sql)
        assert newer["revision"] > claim["revision"]
        assert store(newer)["stored"]
        assert case.control.json(read)["ready"]

        # Holding a prepared source/queue does not block an input transaction.
        locker = case.session("projection-lock")
        locker.run("begin; select 1 from onflows_private.prepared_view_sources where athlete_alias=" + alias + " for update;")
        later.run(event)
        locker.run("rollback;")
        assert not case.control.json(read)["ready"]
        claim = case.control.json(claim_sql)
        later.run(event)
        assert not store(claim)["stored"], "Changed inputs published an in-flight result"
        print(json.dumps({"scenario":"prepared-page-events","commit_reordering":"passed","nonblocking_inputs":"passed","publication_fence":"passed"}))
    finally:
        case.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", type=local_dsn, default=LOCAL_DSN)
    reordered_events(parser.parse_args().dsn)
