#!/usr/bin/env python3
"""Real concurrent budget admissions on disposable local Supabase only."""
from __future__ import annotations

import argparse
import json
import time
import uuid

from history_generation_concurrency import LOCAL_DSN, Psql, literal, local_dsn


def reserve_sql(budget_id, request_id):
    return ("select public.onflows_assistant_budget('RESERVE'," + literal(budget_id)
            + "," + literal(request_id) + "::uuid,60);")


def concurrent_admission(dsn, *, same_request=False, rollback=False):
    budget_id = "budget-race-" + uuid.uuid4().hex
    first_id = str(uuid.uuid4())
    second_id = first_id if same_request else str(uuid.uuid4())
    sessions = []
    try:
        control = Psql(dsn, budget_id + "-control")
        sessions.append(control)
        control.run("insert into onflows_private.assistant_budget_accounts"
                    "(budget_id,limit_nano_usd,enabled) values (" + literal(budget_id) + ",100,true);")
        first = Psql(dsn, budget_id + "-first")
        sessions.append(first)
        second = Psql(dsn, budget_id + "-second")
        sessions.append(second)

        admitted = first.json("begin; set local role service_role; " + reserve_sql(budget_id, first_id))
        assert admitted == {"reserved": True}, admitted
        second.start("begin; set local role service_role; " + reserve_sql(budget_id, second_id) + " commit;")
        # Verify an actual cross-backend wait on the held account lock, rather
        # than hoping scheduler timing produced a concurrent check-and-reserve.
        deadline = time.monotonic() + 8
        while True:
            blocked = control.json("select to_jsonb(" + str(first.pid) + "=any(pg_blocking_pids("
                                   + str(second.pid) + ")));")
            if blocked:
                break
            assert time.monotonic() < deadline, "Second budget admission did not wait on the first"
            time.sleep(.02)
        first.run("rollback;" if rollback else "commit;")
        rows = second.finish()
        assert len(rows) == 1, rows
        result = json.loads(rows[0])
        expected = ({"reserved": True} if rollback else
                    {"error": "invalid_budget" if same_request else "budget_exhausted"})
        assert result == expected, result
        account = control.json("select jsonb_build_object('charged',charged_nano_usd,'reserved',reserved_nano_usd)"
                               " from onflows_private.assistant_budget_accounts where budget_id=" + literal(budget_id) + ";")
        assert account == {"charged": 0, "reserved": 60}, account
        assert control.run("select count(*) from onflows_private.assistant_budget_reservations where budget_id="
                           + literal(budget_id) + ";") == ["1"]
    finally:
        # Close held transactions before deleting this test's uniquely named rows.
        for session in reversed(sessions[1:]):
            session.close()
        if sessions:
            try:
                sessions[0].run("delete from onflows_private.assistant_budget_reservations where budget_id="
                                + literal(budget_id) + "; delete from onflows_private.assistant_budget_accounts"
                                " where budget_id=" + literal(budget_id) + ";")
            finally:
                sessions[0].close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", type=local_dsn, default=LOCAL_DSN)
    args = parser.parse_args()
    concurrent_admission(args.dsn)
    print("PASS shared budget cannot fund two concurrent reservations", flush=True)
    concurrent_admission(args.dsn, same_request=True)
    print("PASS concurrent duplicate request cannot authorize another paid call", flush=True)
    concurrent_admission(args.dsn, rollback=True)
    print("PASS rolled-back admission does not consume the remaining allowance", flush=True)


if __name__ == "__main__":
    main()
