#!/usr/bin/env python3
"""Real PostgreSQL races against synthetic fixtures on local Supabase only.

No database driver, Docker control, production connection or timing-only
barriers. Independent psql backends perform the writes; a third backend checks
actual blocking edges. All held transactions are closed before fixture cleanup.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid


LOCAL_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
DEADLINE = 12.0
FIXTURE_SQL = Path(__file__).with_name("history_generation_fixture.sql")


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class SqlFailure(RuntimeError):
    def __init__(self, stderr: str):
        self.stderr = stderr
        match = re.search(r"ERROR:\s+([A-Z0-9]{5}):", stderr)
        self.state = match.group(1) if match else None
        super().__init__(f"psql failed ({self.state or 'unknown SQLSTATE'}): {stderr[-2000:]}")


class Psql:
    def __init__(self, dsn: str, label: str):
        self.label = label
        env = {**os.environ, "PGCONNECT_TIMEOUT": "5", "PGAPPNAME": label, "PGPAGER": "cat"}
        self.process = subprocess.Popen(
            ["psql", "--no-psqlrc", "--no-align", "--tuples-only", "--quiet",
             "--set", "ON_ERROR_STOP=1", "--set", "VERBOSITY=verbose", "--dbname", dsn],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=env,
        )
        self.output: queue.Queue[str | None] = queue.Queue()
        self.errors: list[str] = []
        self.pending: str | None = None
        self.stdout_thread = threading.Thread(target=self._stdout, daemon=True)
        self.stderr_thread = threading.Thread(target=self._stderr, daemon=True)
        self.stdout_thread.start()
        self.stderr_thread.start()
        try:
            self.run("set statement_timeout='30s'; set lock_timeout='20s'; "
                     "set idle_in_transaction_session_timeout='45s';")
            self.pid = int(self.run("select pg_backend_pid();")[0])
        except BaseException:
            self.close()
            raise

    def _stdout(self):
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.output.put(line.rstrip("\n"))
        self.output.put(None)

    def _stderr(self):
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.errors.append(line)

    def start(self, sql: str):
        assert self.pending is None, "Concurrent commands on one backend are not allowed"
        assert self.process.stdin is not None
        self.pending = "history_barrier_" + uuid.uuid4().hex
        self.process.stdin.write(sql + "\n\\echo " + self.pending + "\n")
        self.process.stdin.flush()

    def finish(self, timeout: float = DEADLINE) -> list[str]:
        assert self.pending is not None
        until = time.monotonic() + timeout
        result = []
        while True:
            remaining = until - time.monotonic()
            if remaining <= 0:
                raise AssertionError("Database barrier timed out; possible lock inversion")
            try:
                line = self.output.get(timeout=remaining)
            except queue.Empty as error:
                raise AssertionError("Database barrier timed out; possible lock inversion") from error
            if line is None:
                self.process.wait(timeout=3)
                self.stderr_thread.join(timeout=3)
                self.pending = None
                raise SqlFailure("".join(self.errors))
            if line == self.pending:
                self.pending = None
                return result
            if line:
                result.append(line)

    def run(self, sql: str, timeout: float = DEADLINE) -> list[str]:
        self.start(sql)
        return self.finish(timeout)

    def json(self, sql: str):
        rows = self.run(sql)
        assert len(rows) == 1, f"Expected one JSON result, got {rows[:3]}"
        return json.loads(rows[0])

    def expect_error(self, state: str, *, hint: str | None = None):
        try:
            self.finish()
        except SqlFailure as error:
            assert error.state == state, f"Expected {state}, got {error}"
            if hint:
                assert f"HINT:  {hint}" in error.stderr or f"HINT: {hint}" in error.stderr, error.stderr
        else:
            raise AssertionError(f"Expected SQLSTATE {state}, but statement succeeded")

    def close(self):
        if self.process.poll() is None:
            # Closing the client also rolls back a still-open transaction.
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.stdout_thread.join(timeout=1)
        self.stderr_thread.join(timeout=1)


class Case:
    def __init__(self, dsn: str, label: str):
        self.dsn = dsn
        self.label = label
        self.sessions: list[Psql] = []
        try:
            self.control = self.session("coordinator")
            self.control.run(FIXTURE_SQL.read_text())
            self.fixture = self.control.json(
                "select pg_temp.history_generation_fixture(" + literal("history-race-" + label) + ");")
        except BaseException:
            for session in reversed(self.sessions):
                session.close()
            raise
        self.a = self.fixture["alias"]

    def session(self, label: str) -> Psql:
        session = Psql(self.dsn, "history-test-" + self.label + "-" + label)
        self.sessions.append(session)
        return session

    def rows(self):
        return self.control.json("select pg_temp.history_generation_rows(" + literal(self.a) + ");")

    def exists(self, role: str) -> bool:
        return self.control.run("select exists(select 1 from public.onflows_analysis_generations "
                                "where generation_id=" + literal(self.fixture[role]) + "::uuid);") == ["t"]

    def pinned(self, role: str) -> bool:
        return self.control.run("select exists(select 1 from onflows_private.history_generation_pins "
                                "where athlete_alias=" + literal(self.a) + " and generation_id="
                                + literal(self.fixture[role]) + "::uuid);") == ["t"]

    def draft(self, role: str = "control", day: str = "2098-01-01", *, rpc=False) -> str:
        f = self.fixture
        payload = json.dumps({"start_date": day, "source": {"generation_id": f[role]}}, separators=(",", ":"))
        if rpc:
            return ("set local role service_role; select public.save_onflows_management_entry("
                    + literal(self.a) + ",'DRAFT'," + literal(day) + "," + literal(payload)
                    + "::jsonb,0," + literal(f["actor"]) + "::uuid,1,null,false); reset role;")
        return ("set local role service_role; insert into public.onflows_management_entries"
                "(athlete_alias,kind,entry_key,revision,payload,actor_id) values("
                + literal(self.a) + ",'DRAFT'," + literal(day) + ",1," + literal(payload)
                + "::jsonb," + literal(f["actor"]) + "::uuid); reset role;")

    def delete(self, role="control") -> str:
        return "delete from public.onflows_analysis_generations where generation_id=" + literal(self.fixture[role]) + "::uuid;"

    def wait_blocked(self, waiter: Psql, blocker: Psql):
        until = time.monotonic() + DEADLINE
        while time.monotonic() < until:
            blocked = self.control.run(f"select {blocker.pid}=any(pg_blocking_pids({waiter.pid}));")
            if blocked == ["t"]:
                return
            if waiter.process.poll() is not None:
                raise AssertionError("Backend exited before the expected lock edge was observed")
            time.sleep(.025)
        raise AssertionError("Expected database blocking edge did not appear")

    def initialize_plan(self):
        result = self.control.json(self.plan_save("active", revision=0, operation="ACTIVATE"))
        assert result.get("saved") is True, result

    def plan_save(self, role: str, *, revision=1, operation="REVIEW") -> str:
        payload = json.dumps({"status": "REVIEW_REQUIRED", "mode": "REVIEW",
                              "approved_by": self.fixture["actor"], "approved_profile_revision": 1,
                              "plan": {"source": {"generation_id": self.fixture[role]}}}, separators=(",", ":"))
        # Date/checkpoint are evaluated by the actual supported writer at call time.
        return ("select public.save_onflows_management_plan(" + literal(self.a) + ",(" + literal(payload)
                + "::jsonb || jsonb_build_object('evaluated_on',current_date::text))," + str(revision)
                + "," + literal(self.fixture["actor"]) + "::uuid," + literal(operation)
                + ",1,public.onflows_management_checkpoint(" + literal(self.a) + "),false);")

    def close(self):
        workers = self.sessions[1:]
        try:
            if workers:
                pids = ",".join(str(session.pid) for session in workers)
                own_backends = ("select pid from pg_stat_activity where pid in (" + pids + ") "
                                "and application_name like " + literal("history-test-" + self.label + "-%"))
                # Client termination alone need not interrupt a blocked backend.
                # Signal only this case's PIDs AND application names on the
                # hard-limited local DSN, then verify their server-side exit.
                self.control.run("select pg_terminate_backend(pid) from (" + own_backends + ") own;")
                until = time.monotonic() + DEADLINE
                while self.control.run(own_backends + ";"):
                    if time.monotonic() >= until:
                        raise AssertionError("Synthetic backend survived termination; refusing fixture cleanup")
                    time.sleep(.025)
            # Fixtures may be removed only after all other backends/held
            # transactions are gone, not merely after killing a client process.
            self.control.run("begin; delete from public.onflows_intervals_connections where athlete_alias in ("
                             + literal(self.a) + "," + literal(self.fixture["other_alias"]) + "); "
                             "delete from auth.users where id=" + literal(self.fixture["actor"]) + "::uuid; commit;")
        finally:
            for session in reversed(workers):
                session.close()
            self.control.close()


def insert_first(dsn: str, commit: bool):
    case = Case(dsn, "insert-first-" + str(commit).lower())
    try:
        writer, deleter = case.session("writer"), case.session("delete")
        writer.run("begin; " + case.draft(rpc=True))
        original = case.rows()  # Uncommitted draft is intentionally not visible here.
        deleter.start("begin; " + case.delete() + " commit;")
        case.wait_blocked(deleter, writer)
        writer.run("commit;" if commit else "rollback;")
        if commit:
            deleter.expect_error("23503")
            assert case.exists("control") and case.pinned("control")
            assert len(case.rows()) == len(original) + 1, "Committed historical row was lost"
        else:
            deleter.finish()
            assert not case.exists("control") and not case.pinned("control")
            assert case.rows() == original, "Rolled-back draft leaked or rewrote history"
    finally:
        case.close()


def delete_first(dsn: str, commit: bool):
    case = Case(dsn, "delete-first-" + str(commit).lower())
    try:
        deleter, writer = case.session("delete"), case.session("writer")
        original = case.rows()
        deleter.run("begin; " + case.delete())
        writer.start("begin; " + case.draft(rpc=True) + " commit;")
        writer.expect_error("PT409", hint="ONFLOWS_HISTORY_REFERENCE_CONFLICT")
        assert case.rows() == original, "Busy reference inserted partial history"
        deleter.run("commit;" if commit else "rollback;")
        retry = case.session("retry")
        retry.start("begin; " + case.draft(rpc=True) + " commit;")
        if commit:
            retry.expect_error("PT409", hint="ONFLOWS_HISTORY_REFERENCE_CONFLICT")
            assert not case.exists("control") and case.rows() == original
        else:
            retry.finish()
            assert case.exists("control") and case.pinned("control")
            assert len(case.rows()) == len(original) + 1
    finally:
        case.close()


def existing_pin(dsn: str):
    case = Case(dsn, "existing-pin")
    try:
        case.control.run("select onflows_private.pin_history_generations(" + literal(case.a)
                         + ",array[" + literal(case.fixture["control"]) + "::uuid]);")
        locker, writer = case.session("locker"), case.session("writer")
        locker.run("begin; select 1 from public.onflows_analysis_generations where generation_id="
                   + literal(case.fixture["control"]) + "::uuid for update;")
        writer.run("begin; " + case.draft() + " commit;")
        assert case.pinned("control") and len(case.rows()) == 2
        locker.run("rollback;")
    finally:
        case.close()


def opposing_new_pins(dsn: str):
    case = Case(dsn, "opposing-new-pins")
    try:
        original = case.rows()
        case.control.run("select onflows_private.pin_history_generations(" + literal(case.a)
                         + ",array[" + literal(case.fixture["parent"]) + "::uuid]);")
        first, opposing, existing = case.session("first"), case.session("opposing"), case.session("existing")
        first.run("begin; " + case.draft("control", "2098-01-01"))
        opposing.start("begin; " + case.draft("extra", "2098-01-02") + " commit;")
        opposing.expect_error("PT409", hint="ONFLOWS_HISTORY_REFERENCE_CONFLICT")
        # The nonblocking alias guard is for NEW pins, not every history row.
        existing.run("begin; " + case.draft("parent", "2098-01-03") + " commit;")
        first.run(case.draft("extra", "2098-01-02") + " commit;")
        after = case.rows()
        assert len(after) == len(original) + 3 and all(row in after for row in original)
        assert all(case.pinned(role) for role in ("parent", "control", "extra")), "Multi-row transaction lost a pin"
    finally:
        case.close()


def lifecycle_inversion(dsn: str, kind: str):
    case = Case(dsn, kind + "-state-inversion")
    try:
        case.initialize_plan()
        original = case.rows()
        publisher, lifecycle = case.session("publisher"), case.session(kind)
        publisher.run("begin; select 1 from public.onflows_athlete_analysis_state where athlete_alias="
                      + literal(case.a) + " for share;")
        target = "rollback" if kind == "rollback" else "ready"
        if kind == "activation":
            lifecycle.run("begin; select 1 from public.onflows_sync_jobs where job_id="
                          + literal(case.fixture["ready_job"]) + "::uuid for update; "
                          "select 1 from public.onflows_analysis_generations where generation_id="
                          + literal(case.fixture[target]) + "::uuid for update;")
            call = ("select outcome from public.activate_onflows_analysis_generation("
                    + literal(case.fixture["ready_job"]) + "::uuid," + literal(case.fixture[target])
                    + "::uuid," + literal(case.fixture["lease"]) + "::uuid);")
            expected = "ACTIVATED"
        else:
            lifecycle.run("begin; select 1 from public.onflows_analysis_generations where generation_id="
                          + literal(case.fixture[target]) + "::uuid for update;")
            call = ("select outcome from public.rollback_onflows_analysis_generation("
                    + literal(case.a) + "," + literal(case.fixture[target]) + "::uuid);")
            expected = "ROLLED_BACK"
        lifecycle.start(call + " commit;")
        case.wait_blocked(lifecycle, publisher)
        # Real publication already holds state SHARE while the lifecycle holds
        # generation UPDATE. New pins must refuse immediately, not deadlock.
        publisher.start("set local role service_role; " + case.plan_save(target))
        publisher.expect_error("PT409", hint="ONFLOWS_HISTORY_REFERENCE_CONFLICT")
        assert expected in lifecycle.finish(), "Supported lifecycle did not complete after conflict"
        assert case.rows() == original, "Conflicted publication changed historical tuple identity"
        active = case.control.run("select active_generation_id::text from public.onflows_athlete_analysis_state "
                                  "where athlete_alias=" + literal(case.a) + ";")
        assert active == [case.fixture[target]], "Lifecycle pointer is inconsistent"
        snapshot = case.control.run("select generation_id::text from public.onflows_training_snapshots "
                                    "where athlete_alias=" + literal(case.a) + ";")
        assert snapshot == active, "Lifecycle snapshot and pointer diverged"
    finally:
        case.close()


def parent_link_race(dsn: str, pin_first: bool):
    case = Case(dsn, "parent-link-" + str(pin_first).lower())
    try:
        pin, link = case.session("pin"), case.session("link")
        update = ("update public.onflows_analysis_generations set base_generation_id="
                  + literal(case.fixture["extra"]) + "::uuid where generation_id="
                  + literal(case.fixture["control"]) + "::uuid;")
        if pin_first:
            pin.run("begin; " + case.draft())
            link.start("begin; " + update + " commit;")
            link.expect_error("PT409", hint="ONFLOWS_HISTORY_REFERENCE_CONFLICT")
            pin.run("commit;")
            original = case.rows()
            retry = case.session("link-retry")
            retry.run("begin; " + update + " commit;")
            assert case.rows() == original, "Parent-link extension rewrote history"
        else:
            link.run("begin; " + update)
            pin.start("begin; " + case.draft() + " commit;")
            pin.expect_error("PT409", hint="ONFLOWS_HISTORY_REFERENCE_CONFLICT")
            link.run("commit;")
            retry = case.session("pin-retry")
            retry.run("begin; " + case.draft() + " commit;")
        assert case.pinned("control") and case.pinned("extra"), "Concurrent parent link escaped pin closure"
    finally:
        case.close()


def local_dsn(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in ("postgres", "postgresql") or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.port != 54322 or parsed.path != "/postgres" or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError("Concurrency fixtures require local Supabase on loopback port54322/postgres")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", type=local_dsn, default=LOCAL_DSN)
    args = parser.parse_args()
    tests = [("history INSERT first commits", lambda: insert_first(args.dsn, True)),
             ("history INSERT first rolls back", lambda: insert_first(args.dsn, False)),
             ("generation DELETE first commits", lambda: delete_first(args.dsn, True)),
             ("generation DELETE first rolls back", lambda: delete_first(args.dsn, False)),
             ("existing pin does not reacquire generation row lock", lambda: existing_pin(args.dsn)),
             ("opposing new-pin transactions refuse without blocking existing pins", lambda: opposing_new_pins(args.dsn)),
             ("rollback/publication lock inversion", lambda: lifecycle_inversion(args.dsn, "rollback")),
             ("activation/publication lock inversion", lambda: lifecycle_inversion(args.dsn, "activation")),
             ("new pin first versus changed parent link", lambda: parent_link_race(args.dsn, True)),
             ("changed parent link first versus new pin", lambda: parent_link_race(args.dsn, False))]
    for name, test in tests:
        test()
        print("PASS " + name, flush=True)
    print(f"{len(tests)} real PostgreSQL concurrency scenarios passed", flush=True)


if __name__ == "__main__":
    main()
