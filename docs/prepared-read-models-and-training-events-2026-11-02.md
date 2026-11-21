# Prepared dashboards and training-triggered programs

Status: deployed and verified in staging. Both migrations are applied
(`20261102172430` and `20261102174120`). Worker and API run the same source
`c554f030caa98691d9eb2ef1582251c8ae796c26`, with Recovery v2 enabled in both.
All six active athletes have current prepared dashboards and the real
service-role RPC returned a ready, source-matched Recovery v2 envelope for each.

## Resulting behavior

The existing worker prepares a complete dashboard envelope after an activated
analysis, settings, Recovery/test input, or management-profile change. Its next
local-day boundary also schedules a new Recovery projection. One private row
per athlete contains the job state and the latest derived envelope, capped at
8 MiB. Multiple changes coalesce by increasing the requested revision.

The main dashboard first reads this exact current envelope. A hit skips source
snapshot reads, activity reconstruction, component projection and Recovery
simulation. Permissions are still checked before reading. The existing period
filter, completed-work and volume summaries retain their response contract.
A miss uses the authoritative calculation and conditionally stores it; reads
waiting for the memory lane recheck the cache before expanding a second copy.
Cache transport failures retain the existing calculation/error behavior.

SQL invalidation, requested/prepared revisions, local calendar day, expiry,
source generation/revision/as-of/activation and the release/Recovery feature
identity guard reuse. Inputs changed during computation cannot be stored as
current. Daily scheduling uses the athlete's next local midnight rather than
a fixed 24-hour interval. A new release can warm views before that daily timer.

Program preparation is **not** scheduled by the dashboard timer. Activating an
analysis with an additional available training record schedules one program
task. An identical activity set, wellness-only generation, settings/test edit,
or a new day does not create a program task. A provider-missing record becoming
available counts as new training; duration may come from elapsed, moving or
recording time. No artificial training events are created during migration.

Existing opt-in provider imports remain separate from program calculation.
The worker checks one due active/review program at a time and retains the SQL
connection, grant and six-hour import cooldown checks. A periodic import with
no new actual training does not regenerate a program.

An existing program uses the unchanged lifecycle and dose calculation. A fixed
program keeps its approved plan and receives a separate proposal; only the
existing explicit approval action replaces it. The new SQL writer independently
checks this condition, including logical equality across the existing compact
primary-session storage format. A configured athlete without an active program
gets a reviewable draft through the existing durable draft queue. Profiles,
access, pause/completion, fresh inputs and normal publication checks still apply.

A new day alone does not invalidate a saved prescription or require computing
it again to review it. Every other input/rule identity must still match and
an expired proposal cannot be activated. Expired programs are not actionable;
opening one does not generate a new one. The original forecast and prescription
stay immutable when an already prepared proposal is explicitly approved.

The existing single worker consumer and reentrant model-memory lane bound
heavy work. Manual draft jobs have priority. Other preparation checks occur at
most once per 15 seconds when the manual consumer is idle. Both preparation
queues use token leases, 20-second heartbeats and three attempts per input
revision. The automatic program writer checks the lease and current training
event in the same transaction as the authoritative publication.

## Retention

After a successful dashboard preparation, cleanup runs at most once per local
day. It deletes at most 100 unapproved drafts older than 30 days per athlete.
It preserves the newest draft, latest complete learning memory, latest
progression anchor, origins referenced by any saved plan revision, and receipts
or pending work referenced by the current draft job. Approved program revisions
and all actual training remain unchanged. Existing primary-session compaction
preserves frozen evidence while removing duplicate JSON aliases.

Expired derived history and READY computation cache rows older than two days
can be removed. Running calculations are retained. Historical generation pins
remain conservative; this cleanup never releases a provenance pin or deletes
provider recordings. The dashboard and event queues retain only one current
row per athlete rather than accumulating an event log.

## Verification and limits

The original selected backend regression suite passed all 534 tests in 62.74
seconds, including expiry and concurrent cold reads. The expanded 612-case
run passed 611 cases in 164.95 seconds; its remaining poll fixture used a
placeholder fingerprint instead of the actual frozen-input hash. After fixing
that fixture, all 66 cases in the affected prepared-model, poll-state and
Blueprint groups passed in 11.59 seconds. No runtime change was needed for
that test correction. `git diff --check` passed.

The migration and SQL contracts were executed on local PostgreSQL 18.6 through
test-only PGlite 0.5.14. Contract fixtures roll back completely. Checks cover
private RPC/table access, dirty settings and day handling, build invalidation,
cache and program lease fencing, source removal/recreation without reusing an
old revision, new versus identical/wellness/missing-activity
imports, event coalescing, fixed-dose protection and protected history cleanup.

The local runner loads core tables and the existing access/source readers from
repository migrations. External account tables are minimal test dependencies;
the unchanged authoritative plan writer is intentionally unreachable in local
guard tests. The same guard contract also passed against the staging Supabase
database. `tests/database/prepared_program_publication.sql` additionally passed
against its real authoritative writer: an owned training lease published a
separate proposal, explicit approval selected it, the original prescription
remained immutable, and a completed lease could not publish again. Assertions
are enabled and both cloud test transactions roll back their fixtures.

The initial cleanup exceeded the eight-second request limit for one athlete.
The additive draft-origin expression index eliminated the repeated full plan
scan. Retrying under `service_role` with the same eight-second statement limit
succeeded and removed four obsolete unapproved drafts. Approved program and
proposal hashes remained unchanged. The measured 1.701 ms index lookup is a
database lookup measurement, not dashboard or whole-cleanup latency.

Initial rollout also exposed a missing worker Recovery v2 flag. The worker
Blueprint and service setting now explicitly match the existing API flag.
The final runtime uses the same commit in both services and its build/Recovery
cache identity matches. Service plans and counts are intact.

No latency improvement percentage or new user-capacity limit is established
by these functional tests. Authenticated dashboard HTTP latency still requires
a real signed-in request; database cache readiness alone is not that benchmark.

## Verified staging rollout

| Service | Deployment | Live at (UTC, 2026-11-02) |
| --- | --- | --- |
| Worker | `dep-dbkd1a08v5cc73b2r540` | 18:05:16 |
| API | `dep-dbkd24m1egvs739qpa6g` | 18:07:21 |

At 18:10:31 UTC, all six rows matched their requested revision and the current
runtime identity, contained Recovery v2, had completed today's cleanup, and
had no preparation error. Their final preparations completed between 18:07:11
and 18:08:32 UTC; the largest envelope was 1,287,013 bytes. The real service-role
READ contract additionally checked the source generation, revision, activation
timestamp and both athlete identifiers, with assertions enabled. This proves
database prepared hits; it does not measure signed-in dashboard HTTP latency.

The two existing programs retained revisions 776 and 40 and the exact same
approved-plan and proposal hashes. There were zero training events and zero
synthetic fixture users remaining. New-versus-unchanged training scheduling
and the real fixed-program publication/approval path passed the rolled-back
cloud contracts. The live migration did not manufacture training events.

Minute-resolution samples during the final release recorded current-worker
memory of approximately 187–194 MiB and current-API memory of 170–174 MiB,
within their unchanged 512 MiB limits. Each service remains at 0.5 CPU and one
configured instance. These short samples are not continuous peak measurements
or a concurrency/load test. The final services had no warning/error log entries
in the checked 18:07:30–18:09:41 UTC interval; `/health` returned HTTP 200.

Render briefly ran the preceding worker while replacing it. Different build
keys caused temporary cache replacement during that overlap. Its shutdown was
observed at 18:07:16 UTC; the six envelopes then converged on the final identity.
The deployed tree exactly matched the locally tested tree
`bec8002c35a5597ae17a584bafdadd65734b753c`. Later documentation-only commits do
not require deploying another runtime or rewarming all six views.

Service plans, instance counts, deployment settings and training formulas do
not change. The additive schema can remain if application code is rolled back.
The preceding worker restores its earlier periodic program checks; account for
that behavioral difference during rollback. Never drop pending queues as a
rollback step.

Local SQL reproduction uses a separately installed test-only package:

```sh
node scripts/test_prepared_sql_local.cjs /path/to/node_modules/@electric-sql/pglite
```
