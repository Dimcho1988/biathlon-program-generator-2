#!/usr/bin/env python3
"""Verify filename-only alignment; compare read-only schema evidence.

This program has no database or network client. A repair is only a reviewable
SQL file, generated after equal full catalog snapshots and exact metadata
guards. It never executes SQL. Baseline rows have NULL statements so metadata
does not falsely assert that the historical SQL scripts ran verbatim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/migration-alignment-2026-10-19.json"
BEFORE = ROOT / "docs/migration-remote-before-2026-10-19.json"
CATALOG_SQL = ROOT / "tests/database/migration_catalog_snapshot.sql"
METADATA_SQL = ROOT / "tests/database/migration_metadata_snapshot.sql"
HASH = re.compile(r"[0-9a-f]{64}\Z")


def read_json(path):
    with Path(path).open(encoding="utf-8") as source:
        return json.load(source)


def sql_literal(value):
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("SQL literals require NUL-free text")
    return "'" + value.replace("'", "''") + "'"


def json_literal(value):
    return sql_literal(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) + "::jsonb"


def verify_files(root=ROOT, manifest=None):
    manifest = manifest or read_json(MANIFEST)
    renames = {item["name"]: item for item in manifest["renames"]}
    originals = manifest["original_files"]
    files = sorted((root / "supabase/migrations").glob("*.sql"))
    expected = []
    for entry in originals:
        name = entry["name"]
        path = root / renames.get(name, {}).get("after", entry["path"])
        expected.append(path)
        raw = path.read_bytes()
        if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError(f"Original migration SQL changed: {name}")
    if files[:len(expected)] != expected:
        raise ValueError("Reviewed migration file set or relative order differs from the manifest")
    if len(renames) != 18 or len(expected) != 37:
        raise ValueError("Unexpected reviewed migration scope")
    return manifest


def catalog_rows(value):
    if value.get("format") != "onflows-catalog-v1":
        raise ValueError("Unknown catalog evidence format")
    if not isinstance(value.get("postgres_major"), int) or value["postgres_major"] < 15:
        raise ValueError("A comparable PostgreSQL major version is required")
    rows = value.get("objects")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Empty or invalid application catalog")
    by_key = {}
    for row in rows:
        if set(row) != {"kind", "identity", "definition_sha256"}:
            raise ValueError("Unexpected catalog object fields")
        if not isinstance(row["kind"], str) or not isinstance(row["identity"], str):
            raise ValueError("Invalid catalog object identity")
        if not isinstance(row["definition_sha256"], str) or not HASH.fullmatch(row["definition_sha256"]):
            raise ValueError("Missing catalog object digest")
        key = (row["kind"], row["identity"])
        if key in by_key:
            raise ValueError(f"Duplicate catalog identity: {key}")
        by_key[key] = row["definition_sha256"]
    return by_key


def compare_catalogs(expected, observed):
    left, right = catalog_rows(expected), catalog_rows(observed)
    differences = []
    if expected["postgres_major"] != observed["postgres_major"]:
        differences.append("PostgreSQL major version differs; do not normalize deparser output silently")
    if expected.get("database_locale") != observed.get("database_locale"):
        differences.append("Database locale differs; do not infer equivalent locale-dependent expressions")
    for key in sorted(left.keys() | right.keys()):
        if key not in left:
            differences.append("Unexpected object: " + ":".join(key))
        elif key not in right:
            differences.append("Missing object: " + ":".join(key))
        elif left[key] != right[key]:
            differences.append("Changed definition/ownership/security: " + ":".join(key))
    return differences


def require_baseline_coverage(catalog, manifest, root=ROOT):
    rows = catalog_rows(catalog)
    early_sql = "\n".join((root / entry["path"]).read_text()
                          for entry in manifest["missing_metadata"])
    tables = set(re.findall(r"(?im)^create\s+table\s+(?:if\s+not\s+exists\s+)?([\w.]+)", early_sql))
    functions = set(re.findall(r"(?im)^create\s+(?:or\s+replace\s+)?function\s+([\w.]+)", early_sql))
    if len(tables) != 23 or len(functions) != 21:
        raise ValueError("The reviewed baseline object inventory changed")
    present_tables = {identity for kind, identity in rows if kind == "relation"}
    present_functions = {identity.split("(", 1)[0] for kind, identity in rows if kind == "function"}
    if tables - present_tables or functions - present_functions:
        raise ValueError("Catalog does not cover all 23 baseline tables and 21 functions")
    for table in tables:
        for kind in ("column", "constraint", "index"):
            if not any(k == kind and identity.startswith(table + ".") for k, identity in rows) and not (
                    kind == "index" and any(k == kind and identity.startswith(table + "_") for k, identity in rows)):
                raise ValueError(f"Incomplete baseline table evidence: {kind}:{table}")
    # Presence is only a coverage guard; equality checks every emitted object,
    # including the actual constraints, columns, RLS, policies, ACLs and code.
    for kind, identity in [("schema", "private"), ("extension", "pgcrypto"),
                           *(("role", role) for role in ("anon", "authenticated", "service_role"))]:
        if (kind, identity) not in rows:
            raise ValueError(f"Missing baseline security/runtime evidence: {kind}:{identity}")


def sql_query(path):
    return Path(path).read_text().strip().removesuffix(";")


def do_block(body):
    tag = "$onflows_baseline_" + hashlib.sha256(body.encode()).hexdigest() + "$"
    if tag in body:
        raise ValueError("Conflicting generated SQL delimiter")
    return "do " + tag + "\n" + body + "\n" + tag + ";\n"


def reviewed_metadata_rows(metadata, before_rows, additional_migrations, root=ROOT):
    """Preserve all 25 old rows; accept only explicitly reviewed new scripts."""
    if metadata.get("format") != "onflows-migration-metadata-v1":
        raise ValueError("Unknown migration metadata format")
    rows = metadata.get("rows")
    if not isinstance(rows, list) or len({r["version"] for r in rows}) != len(rows):
        raise ValueError("Invalid or duplicate migration metadata")
    original_names = {r["name"] for r in before_rows}
    allowed = {}
    migration_dir = (root / "supabase/migrations").resolve()
    for value in additional_migrations:
        path = Path(value)
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        if path.parent != migration_dir or not re.fullmatch(r"[0-9]{12,14}_[a-z0-9_]+\.sql", path.name):
            raise ValueError("Additional metadata must refer to a reviewed repository migration file")
        version, name = path.stem.split("_", 1)
        if version <= max(r["version"] for r in before_rows):
            raise ValueError("Additional metadata must be an appended migration, not a historical baseline")
        if name in original_names or name in allowed:
            raise ValueError("Additional metadata must not replace or duplicate an original migration")
        raw = path.read_bytes()
        allowed[name] = dict(version=version, name=name, statement_count=1,
                             joined_bytes=len(raw), joined_sha256=hashlib.sha256(raw).hexdigest())
    if [r for r in rows if r["name"] not in allowed] != before_rows:
        raise ValueError("Original 25 migration metadata rows changed or unreviewed rows were added")
    if len(rows) != len(before_rows) + len(allowed):
        raise ValueError("Reviewed additional migration metadata is incomplete")
    for name, expected in allowed.items():
        matches = [r for r in rows if r["name"] == name]
        if matches != [expected]:
            raise ValueError("Additional migration version, name, statements or exact SQL hash differs: " + name)
    return rows


def prepare_repair(expected, observed, metadata, *, ci_commit, ci_run_url,
                   additional_migrations=(), root=ROOT):
    manifest = verify_files(root)
    differences = compare_catalogs(expected, observed)
    if differences:
        raise ValueError("Catalog proof failed:\n" + "\n".join(differences))
    require_baseline_coverage(expected, manifest, root)
    before = read_json(root / BEFORE.relative_to(ROOT))
    before_rows = reviewed_metadata_rows(metadata, before["rows"], additional_migrations, root)
    if not re.fullmatch(r"[0-9a-f]{40}", ci_commit):
        raise ValueError("Exact successful CI commit is required")
    if not re.fullmatch(r"https://github\.com/Dimcho1988/biathlon-program-generator-2/actions/runs/[0-9]+", ci_run_url):
        raise ValueError("Exact successful repository CI run URL is required")
    missing = manifest["missing_metadata"]
    if len(missing) != 12 or any(entry["version"] in {r["version"] for r in before_rows} for entry in missing):
        raise ValueError("The twelve baseline versions are not all absent")
    if len({entry["version"] for entry in missing}) != 12:
        raise ValueError("Duplicate baseline version")
    after_rows = sorted(before_rows + [dict(version=e["version"], name=e["name"], statement_count=None,
                         joined_bytes=None, joined_sha256=None) for e in missing], key=lambda r: r["version"])
    review = ("-- REVIEW ONLY: not executed by this program. Target project: " + manifest["project_ref"]
              + "\n-- CI commit: " + ci_commit + "\n-- CI run: " + ci_run_url
              + "\n-- Acknowledges equivalent existing schema; does NOT assert original SQL ran verbatim.\n")
    header = "begin;\nset local lock_timeout='2s';\nset local statement_timeout='30s';\nset local standard_conforming_strings=on;\nlock table supabase_migrations.schema_migrations in share row exclusive mode nowait;\n"
    # Always recalculate both guards inside the transaction; supplied files are
    # evidence for preparing a proposal, never a substitute for current state.
    check = ("if (" + sql_query(root / CATALOG_SQL.relative_to(ROOT)) + ") is distinct from "
             + json_literal(expected) + " then raise exception 'Application catalog changed; baseline repair refused'; end if;\n"
             + "if (" + sql_query(root / METADATA_SQL.relative_to(ROOT)) + ") is distinct from "
             + json_literal({"format": "onflows-migration-metadata-v1", "rows": before_rows})
             + " then raise exception 'Migration metadata changed; baseline repair refused'; end if;\n")
    values = ",\n".join("(" + sql_literal(e["version"]) + "," + sql_literal(e["name"]) + ",null::text[])" for e in missing)
    repair_body = ("declare changed integer;\nbegin\n" + check
              + "insert into supabase_migrations.schema_migrations(version,name,statements) values\n" + values
              + ";\nget diagnostics changed=row_count;\nif changed<>12 then raise exception 'Unexpected baseline row count'; end if;\n"
              + "end;")
    repair = review + header + do_block(repair_body) + "commit;\n"
    # Recovery is a separate, reviewable metadata-only transaction. Exact
    # 37-row after-state plus NULL statements prevent deleting unrelated rows.
    recovery_guard = ("if (" + sql_query(root / METADATA_SQL.relative_to(ROOT)) + ") is distinct from "
                      + json_literal({"format": "onflows-migration-metadata-v1", "rows": after_rows})
                      + " then raise exception 'Migration metadata changed; recovery refused'; end if;\n")
    ids = ",".join(sql_literal(e["version"]) for e in missing)
    recovery_body = ("declare changed integer;\nbegin\n" + recovery_guard
                + "delete from supabase_migrations.schema_migrations where version in (" + ids + ") and statements is null;\n"
                + "get diagnostics changed=row_count;\nif changed<>12 then raise exception 'Unexpected recovery row count'; end if;\n"
                + "end;")
    recovery = (review + "-- Recovery removes only the newly acknowledged baseline metadata.\n"
                + header + do_block(recovery_body) + "commit;\n")
    return repair, recovery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-files")
    validate = sub.add_parser("validate-catalog")
    validate.add_argument("catalog", type=Path)
    compare = sub.add_parser("compare")
    compare.add_argument("expected", type=Path)
    compare.add_argument("observed", type=Path)
    repair = sub.add_parser("prepare-repair")
    repair.add_argument("expected", type=Path)
    repair.add_argument("observed", type=Path)
    repair.add_argument("metadata", type=Path)
    repair.add_argument("--ci-commit", required=True)
    repair.add_argument("--ci-run-url", required=True)
    repair.add_argument("--additional-migration", action="append", type=Path, default=[],
                        help="Explicit reviewed new file, whose stored version and exact SQL hash must match")
    repair.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify-files":
            verify_files()
            print("18 filename changes: all 37 SQL hashes and relative order preserved")
        elif args.command == "validate-catalog":
            evidence = read_json(args.catalog)
            require_baseline_coverage(evidence, verify_files())
            print("Catalog identities are unique and baseline schema/security coverage is complete")
        elif args.command == "compare":
            differences = compare_catalogs(read_json(args.expected), read_json(args.observed))
            if differences:
                raise ValueError("\n".join(differences))
            print("Application catalog objects, ownership and security definitions match exactly")
        else:
            proposal, recovery = prepare_repair(read_json(args.expected), read_json(args.observed),
                read_json(args.metadata), ci_commit=args.ci_commit, ci_run_url=args.ci_run_url,
                additional_migrations=args.additional_migration)
            recovery_path = args.output.with_suffix(".recovery.sql")
            # Exclusive creation preserves any existing reviewed proposal.
            if args.output.exists() or recovery_path.exists():
                raise ValueError("Proposal or recovery output already exists")
            with args.output.open("x", encoding="utf-8") as dest:
                dest.write(proposal)
            with recovery_path.open("x", encoding="utf-8") as dest:
                dest.write(recovery)
            print(f"Prepared review-only SQL: {args.output}; recovery: {recovery_path}; nothing executed")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
