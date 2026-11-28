# Prepared pages and rolling adaptive schedule

The late-day browser check also found a real source transition: the active
proposal's recordings and run keys were unchanged, but its wellness observations
had changed. Material changes to executed load or wellness now queue background
preparation for an active programme. Identical reimports do not queue or rebuild
it. Freshness may inherit a newer immutable generation only when the current
full snapshot digest is equal, its source day is equal, its revision increased,
and the complete input hash still matches after substituting only the saved
generation and revision. Rules, settings and observations remain part of that
hash. Final publication still verifies the live checkpoint. Daily preparation
and explicit refresh continue to calculate normally.

Opening a production model/page must not calculate its scientific result or
submit a programme refresh. The Supabase adapter reads a durable projection;
only the background worker calls the original scientific implementations. A
missing or superseded projection returns a marked preparation response, with
read-only polling in the interface. Authentication is checked on every read.

## Preparation and invalidation

`prepared_views.py` registers the existing route calculations and invokes them
inside a private worker context and one `ReadSession`. Results are fenced by
athlete, parameters, input revision, active generation, local date, model build
and a renewable claim. A result from inputs changed during calculation cannot
publish. All work uses the existing model memory lane.

The queue seeds twelve standard projections per active athlete, plus speed,
speed-history and speed-load variants for their supported recent sports.
New training, settings, tests and profile changes invalidate the relevant
results. Stress/response and plan edits invalidate planning context without
invalidating the paired speed/index model. Additional user-selected periods or
scenarios are bounded cached variants, prepared in the worker on first request.

Existing dashboard and learning projections remain separate. Supabase dashboard
reads now also fail closed on missing preparation, instead of computing in the
request. Recovery settings and saved activity records remain ordinary reads.

Small immutable `SPEED_Q` and `UNIFIED_Q` results persist per activity input key.
They survive process restarts; a new activity calculates only missing input
keys. Raw streams and unsupported/incomplete recomputations are not cached as
valid results. Expired technical entries are removed in bounded batches;
athlete evidence is not removed by this feature.

## Programme updates

New-training events share the existing coalescing revision queue. A worker
calendar tick adds an adaptive refresh once per athlete's local day, independently
of imports or page visits. Multiple changes arriving during a calculation keep
one newest pending revision. An explicit refresh enqueues a job and returns 202;
its actor and expected plan revision are checked again before publication.

Only approved AUTO programmes with an AUTO profile extend daily. Fixed, paused
and completed programmes preserve their lifecycle. No proposal is approved by a
page read. The v72 to v73 horizon extension may inherit AUTO approval only when
the exact prior approval hash matches after substituting that single engine
version; physiology, Recovery, profile, mode and other dosing changes retain
their existing approval requirement.

The adaptive schedule displays today plus six days, with the following seven
days as an option. The worker prepares the complete accounting cycles touched
by this horizon, so a short cycle tail cannot receive a complete cycle target.
The accounting report keeps the coach's original microcycle boundaries and is
available in a collapsed section. Fixed-week selection continues to select the
same accounting and schedule period. Missing future dates stay explicitly
missing; they are never invented as rest days.

A new recording or day can change Recovery and redistribute remaining doses,
so the future schedule is optimized as a coherent whole. Completed activity
derivations are reused. Saving only an isolated added day would not preserve
the coupling between component budgets, recovery and session placement.

## Verification

- API contracts cover prepared-only HTTP reads, authentication, pending/error
  responses, original worker calculations, claim ownership and manual queueing.
- PostgreSQL contracts cover local-day scheduling, request coalescing, fixed and
  paused protection, manual actor/revision checks, source/build/day fencing,
  private permissions and per-activity cache isolation.
- Interface contracts cover calendar windows, daylight saving changes, retained
  fixed-week controls, missing days, explicit refresh and bounded/cancelled
  pending-read polling.
- Run SQL contracts locally with
  `node scripts/test_prepared_views_sql.cjs /path/to/@electric-sql/pglite`.
  Synthetic fixtures roll back. The local harness deliberately stubs the
  unchanged authoritative plan writer; live staging verifies the full path.

Deploy the compatible schema and worker before the API/web prepared-only reads.
Existing adaptive programmes are queued once for the extended horizon at rollout.
Monitor `prepared_view kind=... state=prepared`, queue errors and current-day
prepared counts; an empty queue alone is not proof of successful preparation.
