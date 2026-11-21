# Existing application schema: exact reconciliation proposal

## Evidence and scope

Database CI run `39318697899`, PR #174 head
`0a5feeb1e35c6783f95dd6d1f31411690e1c00d9`, rebuilt the complete chain and
captured its catalog before fixtures. Artifact `12219665872` has ZIP SHA-256
`a8fe81c7b81e01e71b7629da24fe8368a8cd108ee34d3b4e6b785a726a523463`.
The unchanged SELECT against staging and that artifact contain 780 unique
application identities each, PostgreSQL 17 and locale `en_US.UTF-8`. There
are no missing or extra objects. Strict comparison identifies 21 definition
differences; none is suppressed by the acceptance tool.

The earlier artifact from run `39318041630` is invalid evidence: its UNION
inferred PostgreSQL `name` for identities and truncated them at 63 bytes.
The corrected query explicitly casts identities to `text`; CI now rejects
duplicate/incomplete evidence before uploading it. Invalid artifacts are
retained separately for the diagnostic trace.

## Eighteen function definitions

Seventeen early public functions retain CRLF body line endings in staging
and LF in source. `enqueue_onflows_sync_job` additionally wraps statements
across different lines. Lexical inspection of every captured body finds no
multiline quoted string, quoted identifier or nested dollar literal. Token
sequences before/after are identical, including all quoted token bytes;
only whitespace outside those tokens changes. The function manifest records
every exact signature, before/target definition SHA-256, token digest and
line-ending count.

A separate diagnostic-only server calculation substitutes precisely those
18 target definitions. All 58 complete function catalog records then match
the clean CI reference, including owners, settings, SECURITY DEFINER,
leakproof attributes, explicit ACLs and effective API-role privileges. This
establishes the reason for the differences; it does not replace strict raw
catalog equality for deployment acceptance.

The additive migration accepts only the captured before hash or the exact
canonical target hash for each of these 18 identities. Unknown definitions,
owners or permissions abort the transaction. It uses CREATE OR REPLACE only
when a known before definition is present, checks the proposed target hash
before execution, then verifies the resulting raw definition. Already
canonical clean-rebuild definitions remain untouched. Function identities,
arguments, logic, settings, ownership and ACLs are preserved.

## Integrity and sequence access

Staging has a validated `onflows_training_snapshots_athlete_fk`. Its source
adds the same FK with `NOT VALID` and never validates it. The migration
checks the exact single-column target and cascade actions, then validates
the existing constraint. This checks rows without changing/deleting them;
it is already satisfied in staging.

Two public identity sequences differ because fresh local Supabase inherits
broader default grants. The target is the observed restricted staging state:

| Sequence | API-role target |
| --- | --- |
| `onflows_access_audit_log_id_seq` | service role: USAGE and SELECT; anon/authenticated/PUBLIC: none |
| `onflows_history_archive_events_event_id_seq` | service role, anon/authenticated/PUBLIC: none |

Both remain owned by `postgres`. Archive events are inserted by owner-run
functions; this does not require a direct service-role sequence grant. The
migration removes inherited grants explicitly, never reads/sets counters,
and preserves the pruning EXECUTE revocation.

## Validation, deployment and recovery

No reconciliation or metadata repair has been applied by this preparation.
The migration requires a fresh complete CI rebuild and existing PostgreSQL
contracts. After controlled application, recapture the unchanged catalog:
all raw identities and hashes must match the successful CI artifact. Capture
fresh migration metadata too; all original 25 rows must remain unchanged.
The new migration record is an explicitly reviewed addition, not permission
to accept arbitrary ledger drift. The missing 12 baseline acknowledgements
remain a separate guarded proposal until full equality is established.

The transaction has a two-second lock timeout and a 60-second statement
timeout. The snapshot table lock is nonblocking and compatible with normal
reads/writes. A busy DDL lock or unknown definition rolls the transaction
back. No training tables are written and no history payload, generation,
pin, approval or sequence value is removed/changed.

`schema-reconciliation-staging-recovery-2026-10-19.sql` is a review-only
recovery for the captured staging state. It restores the exact 18 original
definition hashes, including old CRLF and enqueue wrapping, only from the
known target definitions and original owner/ACL. It does not weaken FK
validation or restore broad fresh-environment grants: these already had the
target state in staging before the change. All 18 reverse transformations
were derived and checked byte for byte against the captured originals.
Apply recovery only as a controlled owner action with a new audit record;
never delete or rewrite an existing migration record to hide the reversal.
