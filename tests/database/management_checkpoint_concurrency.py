#!/usr/bin/env python3
"""Local-only PostgreSQL publication/checkpoint races using synthetic fixtures.

Actual blocking edges, not sleeps, establish the interleavings. The shared
history harness restricts the DSN to local Supabase and closes all transactions
before deleting only its uniquely named synthetic fixture.
"""
from __future__ import annotations

import argparse
import json
import uuid

from history_generation_concurrency import Case, LOCAL_DSN, literal, local_dsn


def checkpoint(case):
    return case.control.json("select public.onflows_management_checkpoint(" + literal(case.a) + ");")


def state(case):
    return case.control.json("select to_jsonb(s) from public.onflows_management_plan_state s where athlete_alias="
                             + literal(case.a) + ";")


def publication(case, frozen, *, operation="ADAPT", revision=1, variant="full", training=None):
    args = (literal(case.a) + "," + str(revision) + "," + literal(case.fixture["actor"])
            + "::uuid,1," + literal(json.dumps(frozen)) + "::jsonb,")
    if variant == "check":
        return "select public.record_onflows_management_check(" + args + literal(str(uuid.uuid4())) + "::uuid);"
    payload = {"status": "ACTIVE", "mode": "AUTO", "approved_by": case.fixture["actor"],
               "approved_profile_revision": 1,
               "plan": {"activation_eligible": True,
                        "source": {"readiness_known": True, "generation_id": frozen["generation_id"]},
                        "input_snapshot": {"generation_id": frozen["generation_id"],
                                           "analysis_revision": frozen["analysis_revision"]}}}
    function = "save_onflows_management_plan_compact" if variant == "compact" else "save_onflows_management_plan"
    suffix = ",false"
    if training is not None:
        token, training_revision = training
        function = "save_onflows_management_plan_after_training"
        suffix = ",true," + literal(token) + "::uuid," + str(training_revision)
    return ("select public." + function + "(" + literal(case.a) + ",(" + literal(json.dumps(payload))
            + "::jsonb || jsonb_build_object('evaluated_on',(clock_timestamp() at time zone 'UTC')::date::text)),"
            + str(revision) + "," + literal(case.fixture["actor"]) + "::uuid," + literal(operation)
            + ",1," + literal(json.dumps(frozen)) + "::jsonb" + suffix + ");")


def as_service(sql):
    return "begin; set local role service_role; " + sql + " commit;"


def initialize(case):
    frozen = checkpoint(case)
    result = case.control.json(as_service(publication(case, frozen, operation="ACTIVATE", revision=0)))
    assert result.get("saved") is True and result["revision"] == 1, result
    return frozen


def rollback(case, target):
    return ("select to_jsonb(r) from public.rollback_onflows_analysis_generation(" + literal(case.a)
            + "," + literal(case.fixture[target]) + "::uuid) r;")


def late_monotonic_rollback(dsn, variant):
    case = Case(dsn, "checkpoint-aba-" + variant)
    try:
        frozen = initialize(case)
        original = case.rows()
        original_state = state(case)
        barrier, publisher, lifecycle = case.session("barrier"), case.session("publisher"), case.session("rollback")
        # Hold only the existing publication advisory lock. The two supported
        # rollback RPCs can commit independently while publication is queued.
        barrier.run("begin; select pg_advisory_xact_lock(hashtextextended('management:'||"
                    + literal(case.a) + ",0));")
        publisher.start(as_service(publication(case, frozen, variant=variant)))
        case.wait_blocked(publisher, barrier)
        first = lifecycle.json(as_service(rollback(case, "rollback")))
        second = lifecycle.json(as_service(rollback(case, "active")))
        assert first["outcome"] == second["outcome"] == "ROLLED_BACK"
        assert first["active_revision"] == frozen["analysis_revision"]+1
        assert second["active_revision"] == frozen["analysis_revision"]+2
        fresh = checkpoint(case)
        assert fresh["generation_id"] == frozen["generation_id"]
        assert {k: v for k, v in fresh.items() if k != "analysis_revision"} == {
            k: v for k, v in frozen.items() if k != "analysis_revision"}
        barrier.run("commit;")
        rows = publisher.finish()
        assert len(rows) == 1 and json.loads(rows[0]) == {"conflict": True, "reason": "INPUTS_CHANGED"}, rows
        assert case.rows() == original, "A rejected late rollback changed historical tuples"
        assert state(case) == original_state, "Rejected publication changed the pointer or next check"
        assert case.control.run("select count(*) from onflows_private.management_plan_checks where athlete_alias="
                                + literal(case.a) + ";") == ["0"], "Rejected check created an audit event"
    finally:
        case.close()


def mutation(case, kind):
    actor = literal(case.fixture["actor"]) + "::uuid"
    alias = literal(case.fixture["other_alias"] if kind == "other_model" else case.a)
    if kind == "settings":
        # The application settings/PATCH writer changes this existing row.
        return "update public.onflows_athlete_settings set hrmax_bpm=201 where athlete_alias=" + alias + ";"
    if kind in ("model", "other_model"):
        return ("select public.save_onflows_model_entry(" + alias
                + ",'SPEED_TEST','synthetic-checkpoint-model','{}',0," + actor + ");")
    if kind == "profile":
        return ("select public.save_onflows_management_entry(" + alias
                + ",'PROFILE','management-profile-v1','{\"synthetic_profile\":2}',1," + actor + ");")
    if kind == "response":
        return ("select public.save_onflows_response_entry(" + alias
                + ",'DAILY','synthetic-checkpoint-response',current_date,'{}',0," + actor + ");")
    assert kind == "rollback"
    return rollback(case, "rollback")


def publication_first(dsn, kind):
    case = Case(dsn, "checkpoint-publication-first-" + kind)
    try:
        frozen = initialize(case)
        original = case.rows()
        publisher, writer = case.session("publisher"), case.session("input-writer")
        result = publisher.json("begin; set local role service_role; " + publication(case, frozen))
        assert result.get("saved") is True and result["revision"] == 2, result
        # Successful RPC has acquired and retained the real publication locks.
        # Delaying COMMIT makes that existing boundary observable without adding
        # a production hook or changing any function body.
        writer.start(as_service(mutation(case, kind)))
        case.wait_blocked(writer, publisher)
        assert case.rows() == original, "Another session saw an uncommitted plan"
        publisher.run("commit;")
        changed = writer.finish()
        if kind != "settings":
            assert len(changed) == 1
            outcome = json.loads(changed[0])
            assert outcome.get("saved") is True or outcome.get("outcome") == "ROLLED_BACK", outcome
        after = case.rows()
        assert all(row in after for row in original), "Publication/input edit rewrote existing history"
        assert len(after) == len(original)+(2 if kind in ("profile", "response") else 1)
        assert case.control.run("select revision from public.onflows_management_plan_state where athlete_alias="
                                + literal(case.a) + ";") == ["2"]
        fresh = checkpoint(case)
        if kind in ("profile", "other_model"):
            # Profile has its own revision CAS; another athlete's model is not
            # part of this athlete's checkpoint even though the global SHARE
            # table lock currently blocks that model writer as well.
            assert fresh == frozen
        else:
            assert fresh != frozen
    finally:
        case.close()


def input_before_training_publication(dsn, kind):
    case = Case(dsn, "training-input-first-" + kind)
    try:
        frozen = initialize(case)
        original_state = state(case)
        token = str(uuid.uuid4())
        # Seed one synthetic background event, then claim its real worker lease.
        case.control.run(
            "insert into onflows_private.training_plan_updates(athlete_alias,generation_id) values("
            + literal(case.a) + "," + literal(frozen["generation_id"]) + "::uuid) "
            "on conflict(athlete_alias) do update set "
            "requested_revision=onflows_private.training_plan_updates.requested_revision+1,"
            "available_at=clock_timestamp(),attempts=0,lease_token=null,lease_until=null;")
        claim = case.control.json(as_service(
            "select public.onflows_training_plan_update('CLAIM'," + literal(case.a)
            + "," + literal(token) + "::uuid);"))
        assert claim and claim["actor_id"] == case.fixture["actor"], claim
        writer, publisher = case.session("input-writer"), case.session("publisher")
        if kind == "settings":
            source_lock = ("select 1 from public.onflows_athlete_settings where athlete_alias="
                           + literal(case.a) + " for update;")
        else:
            assert kind == "model"
            source_lock = "lock table public.onflows_model_entries in row exclusive mode;"
        writer.run("begin; " + source_lock + " set local role service_role;")
        publisher.start(as_service(publication(case, frozen, training=(token, claim["revision"]))))
        case.wait_blocked(publisher, writer)
        # The publisher must wait for this source before taking the event row.
        # The real input trigger can therefore enqueue while we retain its
        # source lock. Queue-before-source ordering deadlocks at this mutation.
        changed = writer.run(mutation(case, kind))
        if kind == "model":
            assert len(changed) == 1 and json.loads(changed[0]).get("saved") is True, changed
        writer.run("commit;")
        rows = publisher.finish()
        assert len(rows) == 1 and json.loads(rows[0]) == {"conflict": True}, rows
        assert state(case) == original_state, "Stale background publication changed the current plan"
        assert checkpoint(case) != frozen, "The concurrent input edit did not commit"
        # Releasing the old lease must preserve the newer input event for work.
        rejected = case.control.json(as_service(
            "select public.onflows_training_plan_update('REJECT'," + literal(case.a)
            + "," + literal(token) + "::uuid);"))
        assert rejected == {"state": "REJECTED"}, rejected
        next_claim = case.control.json(as_service(
            "select public.onflows_training_plan_update('CLAIM'," + literal(case.a)
            + "," + literal(str(uuid.uuid4())) + "::uuid);"))
        assert next_claim and next_claim["revision"] > claim["revision"], next_claim
    finally:
        case.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", type=local_dsn, default=LOCAL_DSN)
    args = parser.parse_args()
    tests = [("late monotonic rollback rejects " + variant,
              lambda variant=variant: late_monotonic_rollback(args.dsn, variant))
             for variant in ("full", "compact", "check")]
    tests += [("publication serializes " + kind,
               lambda kind=kind: publication_first(args.dsn, kind))
              for kind in ("settings", "model", "other_model", "profile", "response", "rollback")]
    tests += [("input trigger precedes training publication for " + kind,
               lambda kind=kind: input_before_training_publication(args.dsn, kind))
              for kind in ("settings", "model")]
    for name, test in tests:
        test()
        print("PASS " + name, flush=True)
    print(f"{len(tests)} real PostgreSQL checkpoint concurrency scenarios passed", flush=True)


if __name__ == "__main__":
    main()
