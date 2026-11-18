# Date-effective HRmax recalculation

Changing HRmax or the HR zone calibration queues one athlete-scoped `FULL_SYNC` in the existing durable worker lane. Settings and queue insertion commit together. Saving unchanged settings does not queue a rebuild. Pending jobs coalesce through the existing queue.

The form and assistant settings contract accept `hrmax_apply_mode=LAST_90_DAYS` (default) or `FROM_DATE` with `hrmax_effective_from`. Dates are inclusive, evaluated in the athlete's timezone, and bounded to the last 180 days. A correction replaces subsequent calibration intervals; earlier intervals remain unchanged. There is no interpolation. The form displays the most recent effective date.

The database owns the HR calibration timeline and revision inside `model_preferences`. Each interval stores HRmax, zone bounds, source and percentages. The first interval captures the pre-change settings. Manual interior boundaries stay unchanged when only HRmax changes. Raw pulse, timestamps, distance and speed are not rewritten.

The raw recording pipeline, immutable HRmod/TI run identity, daily accounting, historical speed calibration and reconstructed load all resolve calibration by the activity's local date. A changed calibration revision blocks outdated prepared scientific reads and load reconstruction; activation rejects an analysis with an obsolete revision. Existing input revision and generation publication fences remain in place.

A rebuild includes up to 39 earlier days for the rolling history, extending the normal 90-day import only for the pending correction (maximum 219 input days). The next normal sync returns to its configured window. Captured lifetime daily inputs preserve the corrected older results. Missing provider recordings remain explicitly missing; a new calibration does not invent them.

A manually specified HRmax is no longer automatically replaced by an observed peak during synchronization. Automatic Tanaka/observed estimates retain peak-based refinement. This prevents synchronization from undoing a deliberate manual correction.

Validation includes Python integration tests using identical recorded pulse across the effective-date boundary, downstream history resolution and stale-revision rejection; PostgreSQL contracts for repeated corrections, queueing, rollback, isolation and publication fencing; frontend type checks, form contracts and production build. SQL contracts can be run with `node scripts/test_hrmax_history_sql.cjs /path/to/@electric-sql/pglite`.

Rollout: deploy the compatible API and worker, apply the migration, then deploy the form. The migration does not rewrite existing athlete histories. Changes made before this release cannot be assigned an effective date retrospectively without evidence of the prior calibration.
