#!/usr/bin/env python3
"""Real PostgreSQL format recovery/forward guards, on local Supabase only.

The clean migration chain already has canonical function bodies. Restore the
captured staging formatting so CI also exercises every live rewrite branch.
No application rows, sequence values or migration-ledger entries are changed.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_migration_alignment import catalog_rows, require_baseline_coverage, verify_files  # noqa: E402

LOCAL_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


def sql(statement, *, error=None):
    result = subprocess.run(["psql", LOCAL_DSN, "--no-psqlrc", "--tuples-only", "--no-align",
        "--set", "ON_ERROR_STOP=1"], input=statement, text=True, capture_output=True, timeout=75)
    if error is None:
        if result.returncode:
            raise AssertionError("Local PostgreSQL statement failed: " + result.stderr)
    elif result.returncode == 0 or error not in result.stderr:
        raise AssertionError("Expected local guard rejection: " + result.stderr)
    return result.stdout.strip()


def catalog():
    value = json.loads(sql((ROOT / "tests/database/migration_catalog_snapshot.sql").read_text()))
    require_baseline_coverage(value, verify_files())
    return value


def ledger():
    return json.loads(sql((ROOT / "tests/database/migration_metadata_snapshot.sql").read_text()))


def main():
    if len(sys.argv) != 1:
        raise SystemExit("No connection override: this acceptance runs only against local Supabase")
    migrations = list((ROOT / "supabase/migrations").glob("*_reconcile_existing_schema_baseline.sql"))
    assert len(migrations) == 1, "Exactly one reviewed reconciliation migration is required"
    forward = migrations[0].read_text()
    recovery = (ROOT / "docs/schema-reconciliation-staging-recovery-2026-10-19.sql").read_text()
    canonical = catalog()
    metadata_before = ledger()
    sql(recovery)
    restored = catalog()
    left, right = catalog_rows(canonical), catalog_rows(restored)
    changed = {key for key in left if left[key] != right.get(key)}
    expected_names = {entry["signature"].split("(", 1)[0] for entry in json.loads(
        (ROOT / "docs/schema-reconciliation-functions-2026-10-19.json").read_text())["functions"]}
    assert len(changed) == 18 and all(kind == "function" for kind, _ in changed), changed
    assert {identity.split("(", 1)[0] for _, identity in changed} == expected_names
    assert left.keys() == right.keys(), "Format recovery changed the catalog object set"
    print("PASS exact staging-format recovery changes only 18 function definitions", flush=True)

    # This function occurs after several earlier rewrites in the reviewed
    # VALUES order, proving a rejection rolls back already-executed DDL too.
    known_definition = json.loads(sql("select jsonb_build_object('definition',pg_get_functiondef("
        "'public.consume_onflows_login_ticket(text)'::regprocedure));"))["definition"]
    unknown_definition = known_definition.replace("AS $function$",
        "AS $function$\n-- local-only unknown-definition guard probe\n", 1)
    assert unknown_definition != known_definition
    sql(unknown_definition)
    unknown_catalog = catalog()
    sql(forward, error="Schema baseline function definition changed")
    assert catalog() == unknown_catalog, "Rejected forward migration left partial function rewrites"
    assert ledger() == metadata_before, "Rejected forward migration changed migration metadata"
    sql(known_definition)
    print("PASS unknown definition rejects and rolls back earlier format rewrites", flush=True)

    sql(forward)
    assert catalog() == canonical, "Forward reconciliation failed strict raw catalog equality"
    assert ledger() == metadata_before, "Roundtrip changed existing migration metadata"
    print("PASS all 18 live rewrite branches restore exact canonical catalog", flush=True)

    # Owner/permission guards run before the already-canonical fast return.
    sql("grant execute on function public.consume_onflows_login_ticket(text) to authenticated;")
    unexpected_acl = catalog()
    sql(forward, error="Schema baseline function ownership or permissions changed")
    assert catalog() == unexpected_acl, "Permission rejection changed the catalog"
    sql("revoke execute on function public.consume_onflows_login_ticket(text) from authenticated;")
    assert catalog() == canonical and ledger() == metadata_before
    print("PASS unexpected EXECUTE grant is refused without accepting canonical code alone", flush=True)
    print("4 real local PostgreSQL schema reconciliation cases passed", flush=True)


if __name__ == "__main__":
    main()
