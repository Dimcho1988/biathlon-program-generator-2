# Async sync and atomic analysis generations

This document is the deployment and operations contract for the onFlows flow:

`Sync request -> PostgreSQL queue -> worker -> staged generation -> atomic activation -> UI`

The queue intentionally uses the existing Supabase PostgreSQL database. A
second broker is not required for the first production version.

## Non-negotiable invariants

- Every job, generation, activity pointer and active-state row is scoped by
  `athlete_alias` in both application code and database constraints.
- HTTP handlers only enqueue work and read state. They do not call Intervals or
  execute the scientific pipeline.
- A generation is invisible while it is being built. The active pointer and
  legacy snapshot projection change in one database transaction only after the
  complete generation is ready.
- A stale worker cannot activate or fail a job after losing its lease. Lease
  tokens and compare-and-swap revisions fence every state change.
- FULL sync owns an immutable activity set. WELLNESS and RECOVERY generations
  reuse that exact set instead of copying every activity row.
- Dashboard, calendar and activity views resolve one active generation. They
  never assemble content from global “latest” rows belonging to different
  generations.
- Failed, interrupted or CAS-rejected generations never replace the last valid
  active generation.
- Recovery restore accepts only `load-history-v2` with the exact persisted
  daily Tref provenance. Older history triggers a FULL sync instead of guessing.

## Durable state

The migration `202608270001_sync_queue_generations.sql` adds four server-only
tables:

| Table | Purpose |
| --- | --- |
| `onflows_athlete_analysis_state` | Active generation, active revision and enqueue sequence for one athlete. |
| `onflows_sync_jobs` | Durable queue, idempotency, retry schedule, progress and worker lease. |
| `onflows_analysis_generations` | Immutable staged/active snapshot and exact provenance. |
| `onflows_analysis_generation_activities` | Activity catalog payload and immutable canonical/shadow pointers for the activity-set owner. |

All access from the application uses server-only RPCs and the Supabase service
role. Browser clients do not receive direct table privileges.

## Job lifecycle

1. `POST /api/v2/real/sync-jobs` validates the profile and enqueues FULL,
   WELLNESS or RECOVERY work with a profile-local `as_of` date.
2. Exact duplicate requests coalesce. If a same-kind job is already running,
   at most one trailing request is retained, so newer intent is not lost.
3. A worker claims one available job with `FOR UPDATE SKIP LOCKED`, receives a
   lease token and periodically renews it.
4. Provider data and all model outputs are captured without updating the active
   snapshot. Provider-wide authentication, throttling, network or storage
   failures fail the generation; only an explicitly missing individual
   activity can be excluded locally.
5. The worker stages the complete immutable generation, then atomically
   activates it only if the expected base revision is still current.
6. The UI polls `GET /api/v2/real/sync-status` only while work is pending and
   refreshes when the active generation changes.

Retryable failures use bounded attempts and durable `available_at` scheduling.
Intervals `Retry-After` values up to 24 hours are preserved in the queue rather
than keeping a process asleep. Terminal configuration, authorization and data
contract failures expose a stable error code but never sensitive details.

## Safe rollout order

Do not apply the migration directly to production as its first execution.

1. Create a current Supabase backup and record the active API/web deployment.
2. Apply all migrations to a disposable or staging Supabase project using the
   table-owning migration role, never the application `service_role`.
3. Preflight orphan legacy snapshots. Quarantine or repair any orphan, then
   validate `onflows_training_snapshots_athlete_fk`, which is initially added
   as `NOT VALID` to avoid an unsafe automatic deletion during rollout.
4. Run the migration assertions plus enqueue, claim, lease-expiry, retry,
   concurrent activation, rollback, retention and populated-profile deletion
   tests against that database. Also verify function owners, `extensions.digest`,
   RLS, and denial of the new tables/RPCs to `anon` and `authenticated`.
5. Deploy the API while the worker is still disabled. Confirm health, legacy
   revision-0 reads and read-only dashboard/activity contracts.
6. Configure exactly one worker instance. Copy the API values exactly for
   `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `ONFLOWS_TOKEN_ENCRYPTION_KEY`,
   `ONFLOWS_SNAPSHOT_SALT` and `ONFLOWS_ACTIVITY_ID_SECRET`; never regenerate
   the last three for the worker. Match all scientific version variables.
7. Start the worker and enqueue one controlled staging profile. Verify the job
   moves through QUEUED/RUNNING/SUCCEEDED, the revision increases once, and all
   dashboard/calendar/activity responses report that same generation.
8. Exercise a forced worker termination and a stale concurrent activation.
   Confirm the lease is recovered and the previous active generation remains
   readable until a complete successor activates.
9. Deploy the web application and repeat the canary through the browser.
10. Drain every pre-generation API instance. In a separate rehearsed hardening
    migration, remove legacy mutable snapshot write privileges and execution of
    the old mutable canonical publish RPC; keep only the generation path used by
    the new API and worker.
11. Only after the staging evidence is retained should the same ordered rollout
    be approved for production.

The first Render staging deployment uses the repository's
`render.staging.yaml` custom Blueprint path.  Phase 1 deliberately contains
only `onflows-api-staging`, pins the async feature branch and disables automatic
deploys.  After API health succeeds, the same Blueprint adds
`onflows-web-staging` for the OAuth and API-to-Supabase read-contract checks.
The paid worker is added only after those checks pass.  None of these resources
may be created from the production `render.yaml` or managed by a second
Blueprint.

The Render worker is a continuously running paid service. Creating or enabling
it is a billing and deployment action and requires explicit approval. The
repository Blueprint keeps it at one `starter` instance initially.

## Rollback and incident handling

- Stop/scale the worker first to prevent new activations. The API and UI keep
  serving the last active generation.
- Disable or gate new enqueue requests if the fault is in orchestration rather
  than provider availability.
- For a bad but successfully activated generation, use the server-only rollback
  operation to activate a previously activated generation. Never point to a
  READY, FAILED or CAS-rejected generation.
- Do not edit generation payloads or activity pointers in place. Correct the
  cause and create a new generation.
- A migration rollback must be rehearsed from backup in staging. Do not drop
  queue/generation tables while any deployed API or worker uses them.

Useful structured worker events include claim, stage, activation, retry,
terminal failure, lease loss and retention completion. Alerts should cover a
growing oldest-queued age, repeated lease expiry, provider throttling, terminal
error rate and no successful activation for an otherwise active profile.

## Retention

The worker invokes bounded housekeeping outside any job lease, at the first
safe opportunity after startup and no more than once every six hours per
athlete. It retains the active generation, activity-set owners referenced by
retained generations, and a small superseded history. Deletion is batched so it
does not become a long queue transaction. Housekeeping failure is non-fatal and
must not prevent job claims.

This opportunistic pass covers athletes that continue to sync. Before the
public thousand-user stage, schedule a bounded global sweep that invokes the
same server-only prune operation for inactive aliases; otherwise their old
terminal generations remain finite but do not age out automatically.

## Scaling boundary

One worker plus the database queue removes long provider/model work from the
request path and is the simplest safe starting topology. It does not by itself
guarantee throughput for thousands of frequently syncing athletes.

Before adding worker instances:

- measure queue age, job duration, Intervals calls per job and database growth;
- replace repeated 90-day imports with incremental provider synchronization
  where the provider contract permits it;
- add a distributed provider-rate budget, because the current process-wide
  pacer protects one worker process only;
- validate database connection limits and RPC query plans with realistic data;
- tune worker count from measured provider and database budgets, not CPU alone.

The scientific models, bounded causal 40-day per-zone Tref, canonical/shadow
separation and generation provenance must remain unchanged while scaling the
execution topology.
