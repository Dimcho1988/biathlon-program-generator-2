#!/usr/bin/env python3
"""Real local PostgreSQL retention races; no live DSN or timing-only barriers."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from history_generation_concurrency import Case, LOCAL_DSN, literal, local_dsn

FIXTURE = Path(__file__).with_name("safe_retention_fixture.sql")
BASE_FIXTURE = Path(__file__).with_name("history_generation_fixture.sql")
HINT = "ONFLOWS_HISTORY_REFERENCE_CONFLICT"


class RetentionCase(Case):
    def __init__(self, dsn, label):
        self.dsn, self.label, self.sessions = dsn, "retention-" + label, []
        try:
            self.control = self.session("coordinator")
            self.control.run(BASE_FIXTURE.read_text())
            self.control.run(FIXTURE.read_text())
            self.fixture = self.control.json("select pg_temp.safe_retention_fixture("
                                             + literal("retention-race-" + label) + ");")
            self.a = self.fixture["alias"]
            # Isolate historical publication from existing generation/native
            # pointer protection. The active graph remains populated separately.
            self.control.run("delete from public.onflows_analysis_generation_activities where generation_id="
                             + literal(self.fixture["control"]) + "::uuid; "
                             "update public.onflows_activity_catalog set latest_shadow_run_key=null,latest_canonical_run_key=null "
                             "where athlete_alias=" + literal(self.a) + " and activity_ref="
                             + literal(self.fixture["control_activity"]) + ";")
        except BaseException:
            if hasattr(self, "a"):
                self.close()
            else:
                for session in reversed(self.sessions):
                    session.close()
            raise

    def rows(self):
        return self.control.json("select pg_temp.safe_retention_history_rows(" + literal(self.a) + ");")

    def model(self, role="control", key="source", *, kind="shadow"):
        payload = {"input_key" if kind == "input" else "source_run_key": self.fixture[role + "_" + kind]}
        return ("set local role service_role; select public.save_onflows_model_entry("
                + literal(self.a) + ",'SPEED_TEST'," + literal(key) + ","
                + literal(json.dumps(payload)) + "::jsonb,0," + literal(self.fixture["actor"])
                + "::uuid); reset role;")

    def run_exists(self, role="control", kind="shadow"):
        table = {"shadow": "derived_runs", "canonical": "canonical_runs", "input": "model_inputs"}[kind]
        column = "input_key" if kind == "input" else "run_key"
        return self.control.run("select exists(select 1 from public.onflows_activity_" + table
                                + " where " + column + "=" + literal(self.fixture[role + "_" + kind]) + ");") == ["t"]

    def run_pinned(self, role="control", kind="shadow"):
        column = "input_key" if kind == "input" else "run_key"
        return self.control.run("select exists(select 1 from onflows_private.history_" + kind + "_pins "
                                "where athlete_alias=" + literal(self.a) + " and " + column + "="
                                + literal(self.fixture[role + "_" + kind]) + ");") == ["t"]

    def delete_run(self):
        return "delete from public.onflows_activity_derived_runs where run_key=" + literal(self.fixture["control_shadow"]) + ";"


def history_first(dsn, commit):
    case = RetentionCase(dsn, "history-first-" + str(commit).lower())
    try:
        writer, deleter = case.session("model-writer"), case.session("run-delete")
        writer.run("begin; " + case.model())
        before = case.rows()
        deleter.start("begin; " + case.delete_run() + " commit;")
        case.wait_blocked(deleter, writer)
        writer.run("commit;" if commit else "rollback;")
        if commit:
            deleter.expect_error("23503")
            assert case.run_exists() and case.run_pinned()
            after = case.rows()
            assert len(after) == len(before) + 1 and all(row in after for row in before)
        else:
            deleter.finish()
            assert not case.run_exists() and not case.run_pinned()
            assert case.rows() == before, "Rolled-back publication changed historical tuple identity"
    finally:
        case.close()


def delete_first(dsn, commit):
    case = RetentionCase(dsn, "delete-first-" + str(commit).lower())
    try:
        deleter, writer = case.session("run-delete"), case.session("model-writer")
        before = case.rows()
        deleter.run("begin; " + case.delete_run())
        writer.start("begin; " + case.model() + " commit;")
        writer.expect_error("PT409", hint=HINT)
        assert case.rows() == before, "Busy source inserted partial model history"
        deleter.run("commit;" if commit else "rollback;")
        retry = case.session("model-retry")
        retry.start("begin; " + case.model() + " commit;")
        if commit:
            retry.expect_error("PT409", hint=HINT)
            assert not case.run_exists() and case.rows() == before
        else:
            retry.finish()
            assert case.run_exists() and case.run_pinned()
            assert len(case.rows()) == len(before) + 1
    finally:
        case.close()


def existing_pin(dsn):
    case = RetentionCase(dsn, "existing-pin")
    try:
        case.control.run("begin; " + case.model(key="initial") + " commit;")
        before = case.rows()
        locker, writer = case.session("source-lock"), case.session("model-writer")
        locker.run("begin; select 1 from public.onflows_activity_derived_runs where run_key="
                   + literal(case.fixture["control_shadow"]) + " for update;")
        writer.run("begin; " + case.model(key="existing-copy") + " commit;")
        assert case.run_pinned() and len(case.rows()) == len(before) + 1
        locker.run("rollback;")
    finally:
        case.close()


def opposing_new_pins(dsn):
    case = RetentionCase(dsn, "opposing-new-pins")
    try:
        case.control.run("begin; " + case.model("parent", "existing-parent") + " commit;")
        before = case.rows()
        first, opposing, existing = case.session("first"), case.session("opposing"), case.session("existing")
        first.run("begin; " + case.model("control", "first-control"))
        opposing.start("begin; " + case.model("extra", "opposing-extra") + " commit;")
        opposing.expect_error("PT409", hint=HINT)
        existing.run("begin; " + case.model("parent", "existing-parent-copy") + " commit;")
        first.run(case.model("extra", "second-extra") + " commit;")
        after = case.rows()
        assert len(after) == len(before) + 3 and all(row in after for row in before)
        assert all(case.run_pinned(role) for role in ("parent", "control", "extra"))
    finally:
        case.close()


def parent_link_race(dsn, kind, pin_first):
    case = RetentionCase(dsn, kind + "-parent-link-" + str(pin_first).lower())
    try:
        f = case.fixture
        if kind == "input":
            # The input-only test has no immutable derived child to move too.
            case.control.run(case.delete_run())
        table = {"shadow": "derived_runs", "canonical": "canonical_runs", "input": "model_inputs"}[kind]
        key_column = "input_key" if kind == "input" else "run_key"
        assignments = "activity_ref=" + literal(f["extra_activity"])
        if kind == "shadow":
            assignments += ",input_key=" + literal(f["extra_input"])
        update = ("update public.onflows_activity_" + table + " set " + assignments + " where "
                  + key_column + "=" + literal(f["control_" + kind]) + ";")
        pin, link = case.session("pin"), case.session("parent-update")
        before = case.rows()
        if pin_first:
            pin.run("begin; " + case.model(kind=kind))
            link.start("begin; " + update + " commit;")
            # Native key UPDATE waits before the AFTER guard. Prove the real
            # edge, release it, and require complete retained parent closure.
            case.wait_blocked(link, pin)
            pin.run("commit;")
            link.finish()
        else:
            link.run("begin; " + update)
            pin.start("begin; " + case.model(kind=kind) + " commit;")
            pin.expect_error("PT409", hint=HINT)
            assert case.rows() == before, "Conflicted first pin changed historical rows"
            link.run("commit;")
            retry = case.session("pin-retry")
            retry.run("begin; " + case.model(kind=kind) + " commit;")
        assert case.run_pinned(kind=kind)
        assert case.control.run("select exists(select 1 from onflows_private.history_activity_pins "
                                "where athlete_alias=" + literal(case.a) + " and activity_ref="
                                + literal(f["extra_activity"]) + ");") == ["t"]
        if kind == "shadow":
            assert case.run_pinned("extra", "input"), "Changed shadow source input escaped closure"
        after = case.rows()
        assert len(after) == len(before) + 1 and all(row in after for row in before)
    finally:
        case.close()


def native_writer_race(dsn, writer_first, commit):
    case = RetentionCase(dsn, "native-writer-" + str(writer_first).lower() + "-" + str(commit).lower())
    try:
        keys = {name: sha256((case.a + ":native:" + name).encode()).hexdigest()
                for name in ("provider", "input", "input_hash", "shadow", "result")}
        activity = case.control.run("select public.resolve_onflows_activity_ref("
                                    + literal(case.a) + "," + literal(keys["provider"]) + ");")[0]
        publish = ("set local role service_role; select public.publish_onflows_activity_shadow("
                   + literal(keys["input"]) + "," + literal(case.a) + "," + literal(activity) + ","
                   + literal(keys["input_hash"]) + ",'synthetic-retention-v1','{}'::jsonb,"
                   + literal(keys["shadow"]) + "," + literal(keys["result"])
                   + ",'synthetic-retention-v1',null,null,null,null,null,'{}'::jsonb); reset role;")
        delete = "delete from public.onflows_activity_catalog where activity_ref=" + literal(activity) + ";"
        writer, deleter = case.session("native-writer"), case.session("catalog-delete")
        before = case.rows()
        if writer_first:
            writer.run("begin; " + publish + " set constraints all immediate;")
            deleter.start("begin; " + delete + " commit;")
            case.wait_blocked(deleter, writer)
            writer.run("commit;" if commit else "rollback;")
            if commit:
                deleter.expect_error("23503")
            else:
                deleter.finish()
            surviving = commit
        else:
            deleter.run("begin; " + delete)
            writer.start("begin; " + publish + " commit;")
            case.wait_blocked(writer, deleter)
            deleter.run("commit;" if commit else "rollback;")
            if commit:
                writer.expect_error("23503")
            else:
                writer.finish()
            surviving = not commit
        for table, column, key in (("model_inputs", "input_key", "input"),
                                    ("derived_runs", "run_key", "shadow"),
                                    ("run_summaries", "run_key", "shadow")):
            exists = case.control.run("select exists(select 1 from public.onflows_activity_" + table
                                      + " where " + column + "=" + literal(keys[key]) + ");") == ["t"]
            assert exists == surviving, "Native publication left a partial graph"
        assert case.rows() == before, "Native writer/deletion race changed historical tuples"
    finally:
        case.close()


def prune_publication_race(dsn, history_first, commit):
    case = RetentionCase(dsn, "prune-publication-" + str(history_first).lower() + "-" + str(commit).lower())
    try:
        case.control.run("select pg_temp.prepare_safe_retention_control("
                         + literal(json.dumps(case.fixture)) + "::jsonb);")
        original_history = case.rows()
        original_graph = case.control.json("select pg_temp.safe_retention_graph(" + literal(case.a) + ");")
        writer, prune = case.session("model-writer"), case.session("bounded-prune")
        call = "select * from public.prune_onflows_analysis_generations(" + literal(case.a) + ",1,interval '30 days',1);"
        if history_first:
            writer.run("begin; " + case.model(key="racing-history"))
            prune.start("begin; " + call + " commit;")
            prune.expect_error("55P03")
            assert case.control.run("select count(*) from onflows_private.analysis_cleanup_batches where athlete_alias="
                                    + literal(case.a) + ";") == ["0"]
            writer.run("commit;" if commit else "rollback;")
            before_cleanup = case.rows()
            retry = case.session("prune-retry")
            retry.run("begin; " + call + " commit;")
            assert case.run_exists() == commit and case.run_pinned() == commit
            assert case.rows() == before_cleanup, "Cleanup changed committed historical tuples"
        else:
            prune.run("begin; " + call)
            writer.start("begin; " + case.model(key="racing-history") + " commit;")
            writer.expect_error("PT409", hint=HINT)
            assert case.rows() == original_history
            prune.run("commit;" if commit else "rollback;")
            retry = case.session("history-retry")
            retry.start("begin; " + case.model(key="racing-history") + " commit;")
            if commit:
                retry.expect_error("PT409", hint=HINT)
                assert not case.run_exists() and case.rows() == original_history
                assert case.control.run("select count(*) from onflows_private.analysis_cleanup_batches "
                                        "where athlete_alias=" + literal(case.a) + ";") == ["1"]
            else:
                assert case.control.json("select pg_temp.safe_retention_graph(" + literal(case.a) + ");") == original_graph
                retry.finish()
                assert case.run_exists() and case.run_pinned()
                assert len(case.rows()) == len(original_history) + 1
                assert case.control.run("select count(*) from onflows_private.analysis_cleanup_batches "
                                        "where athlete_alias=" + literal(case.a) + ";") == ["0"]
    finally:
        case.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", type=local_dsn, default=LOCAL_DSN)
    args = parser.parse_args()
    tests = [("model INSERT first commits", lambda: history_first(args.dsn, True)),
             ("model INSERT first rolls back", lambda: history_first(args.dsn, False)),
             ("shadow DELETE first commits", lambda: delete_first(args.dsn, True)),
             ("shadow DELETE first rolls back", lambda: delete_first(args.dsn, False)),
             ("existing run pin avoids source row lock", lambda: existing_pin(args.dsn)),
             ("opposed multirow pin transactions refuse while existing copies proceed", lambda: opposing_new_pins(args.dsn))]
    tests += [(kind + " parent update / first pin: " + ("pin first" if pin_first else "link first"),
               lambda kind=kind, pin_first=pin_first: parent_link_race(args.dsn, kind, pin_first))
              for kind in ("shadow", "canonical", "input") for pin_first in (True, False)]
    tests += [("native worker/catalog delete: " + ("writer first" if writer_first else "delete first")
               + (" commits" if commit else " rolls back"),
               lambda writer_first=writer_first, commit=commit: native_writer_race(args.dsn, writer_first, commit))
              for writer_first in (True, False) for commit in (True, False)]
    tests += [("bounded prune/model publication: " + ("history first" if history_first else "prune first")
               + (" commits" if commit else " rolls back"),
               lambda history_first=history_first, commit=commit: prune_publication_race(args.dsn, history_first, commit))
              for history_first in (True, False) for commit in (True, False)]
    for name, test in tests:
        test()
        print("PASS " + name, flush=True)
    print(f"{len(tests)} real PostgreSQL retention concurrency scenarios passed", flush=True)


if __name__ == "__main__":
    main()
