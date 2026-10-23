# Migration filename alignment and existing-schema baseline

## Scope and proven facts

The metadata-only staging census compared repository commit
`3bb8da6c1934d5af51177173f74bb7c2d31f8094` with project
`xhaiawljiebclqpvyewt`. There are 37 repository migration files and 25 stored
migration records. All 25 names have a corresponding file; their relative
order is identical. Eighteen versions differ. The source-file manifest in
`migration-alignment-2026-10-19.json` records every original SHA-256, length,
path and proposed path. `migration-remote-before-2026-10-19.json` preserves
the exact observed versions, names, statement counts, lengths and digests.

The change renames only those 18 files to the recorded staging versions. All
37 SQL bodies and their relative order remain unchanged. No old SQL is edited
to make a historical record appear identical. In particular:

* 21 stored SQL strings match their source files byte for byte.
* `enforce_generation_read_contract` and
  `suspend_unsafe_generation_pruning` omit only the source's final newline.
* `activity_shadow_publish_timeout` omits explanatory leading comments and the
  final newline; its `ALTER FUNCTION` statement is byte-identical.
* `role_profile_security_followup` uses a direct final `REVOKE` in staging.
  The repository guards that statement with `DO/IF to_regprocedure(...)` for
  clean local rebuilds where the platform event-trigger function is absent.
  Its policies and preceding text match. This portability difference remains
  explicit and the existing source bytes are preserved.

These renames do not run migrations, repair remote metadata, change training
rules or modify any historical payload, pin, approval, generation or plan.
The application still has no permission to execute generation pruning.

## Twelve missing records: evidence required before repair

The first 12 source files, from `cloud_oauth_pilot` through
`accounts_sharing_organizations_v1`, lack migration records. They introduce
23 tables, 21 named functions and the `private` schema. This absence does not
prove their schema is missing. Conversely, a table/function name existing is
not sufficient evidence that its definition and access controls match.

Later migrations intentionally alter these objects. The valid reference is a
clean rebuild of the complete current source chain, not the first 12 scripts
in isolation. Database CI captures `migration_catalog_snapshot.sql` directly
after migration reset and before synthetic fixtures, then retains its JSON
artifact with the exact CI commit. The same unchanged SELECT is suitable for
a staging metadata read. It returns no application rows, sequence counters,
tokens, history payloads or stored SQL scripts.

The catalog records normalized object identities and server-side definition
digests for all application-owned relations/functions in `public`, `private`
and `onflows_private`, including later overrides. It covers columns, types,
nullability, defaults, generated/identity configuration, collation, ownership,
constraints and their validation/deferred state, indexes and readiness,
sequence structure/ownership, views, triggers, RLS flags/policies, explicit
ACLs and effective API-role privileges. The private-schema ACLs, application
role security attributes and `pgcrypto` namespace are included. PostgreSQL
major version and database locale must match; the tool does not suppress a
definition/security difference to obtain equality. Extension release numbers
and unrelated Supabase-managed objects are outside this application-schema
comparison.

Run `python3 scripts/audit_migration_alignment.py verify-files` to verify the
18 renames. Run the `compare` subcommand with the CI catalog JSON and staging
catalog JSON to list missing, extra or changed objects. Differences block
baseline preparation and require an explicit diagnosis. A matching catalog
proves current application schema equivalence within this stated scope. It
does not establish that every historical statement executed verbatim.

Two old data effects cannot be reconstructed from schema alone: detaching
orphaned OAuth aliases and seeding analysis-state rows for connections present
at that historical moment. The validated OAuth FK proves the current
referential invariant. It does not reconstruct which old rows were detached;
the original set of connections also cannot be inferred from current rows.
Any later baseline record acknowledges equivalent existing current schema,
with this historical-execution limitation stated in the audit trail.

## Guarded proposal and recovery

`scripts/audit_migration_alignment.py prepare-repair` accepts the successful
CI catalog, the staging catalog, a fresh result of
`migration_metadata_snapshot.sql`, the exact successful CI commit/run URL,
and an output path. It has no database or network client and never executes
SQL. It refuses unequal/incomplete catalog evidence, changed source files,
changed relative order, or any difference in the captured 25 original metadata
rows. Specifically reviewed new append-only migrations may be supplied with
`--additional-migration`; each added record must match that file's exact version,
name, length and full SQL SHA-256. Unspecified additions block preparation.
It then writes a review-only proposal and a separate recovery
file; existing output files are never overwritten.

The proposed transaction uses short lock/statement limits, a nonblocking lock
on the migration ledger, and recalculates the full catalog and exact metadata
guards inside the transaction. It inserts only the 12 absent version/name
pairs, with **NULL statements**. This records an existing-schema baseline
without inventing stored SQL execution history. It neither replays the early
DDL/DML nor changes any existing migration row. No concurrent deployment or
manual schema work should run during this short, controlled baseline action.
The expected ledger then contains the 37 baseline/source versions plus any
explicitly reviewed new migrations, in the unchanged source order.

The generated recovery transaction requires that exact complete after-state,
including the 12 NULL-statement rows, before removing only those new metadata
records. It refuses a changed ledger. It does not reverse application schema
or touch training data. Filename-only recovery is a normal git revert of the
alignment change; the manifest contains the exact 18 original paths and all
37 original hashes. Keep the proposal, recovery, successful CI link and
before/after read-only evidence together when any repair is applied.

At preparation time, fresh catalog equality and the metadata repair have
**not been completed**. The missing 12 records remain an explicit open item.
Do not run a blind database push or replay the early SQL against existing
training data to repair this bookkeeping discrepancy.
