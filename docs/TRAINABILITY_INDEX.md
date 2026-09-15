# Trainability index, staging v2

The index is an exploratory longitudinal descriptor, in percentage points of HRmax per (km/h).
Lower values mean less relative modulated heart rate per allocated flat-equivalent speed. It is not a
validated fitness or physiological threshold estimate.

## Calculation contract

- Consume the existing HRmod final and Vflat B65 outputs without changing them.
  The previously deployed uphill increment multiplier of 1.5 remains in Vflat.
- HR uses the model's own valid interval durations, retaining uncorrected waves.
  Speed exclusions never remove HR time. Gaps retain each source's time semantics.
- Speeds use active one-second intervals, existing Vflat validity, and the
  unclipped, spatially smoothed grade supplied to Vflat. Exclude grades strictly
  below -3%; retain exactly -3%. Do not add a second inertia correction.
- Rank eligible speeds descending, weighted by duration. For each HR band,
  let `p = HR seconds in band / all valid HR seconds`, and let `q` be the fraction
  of valid HR time above that band. Allocate the speed-duration quantile interval
  `[q, q+p]`. Split boundary intervals proportionally; never round sample counts.
- Index = `(100 * duration-weighted mean HRmod / explicit HRmax)` divided by
  duration-weighted mean allocated Vflat (km/h). Use percentage points (78, not
  0.78). For HRmod 154, HRmax 178 and Vflat 20 the index is 4.3258426966.
  Apply the same normalization to all zones and the independent general band.
  HR and speed are independently
  ranked distributions, not measurements necessarily taken at the same instant.
- Z1–Z5 use the existing profile boundaries: lower inclusive, upper exclusive,
  except the last upper bound inclusive. HR outside the zone range retains its
  rank share. Invalid bands also retain their reserved speed shares.
- The general index independently uses HRmod in `[0.75 HRmax, 0.92 HRmax]`, both
  inclusive, skipping the speed share corresponding to HR above 92%. It is not
  the arithmetic mean of the five zone indices.

## Validity and time

All indices are null for activities shorter than 420 seconds. Exactly 420 seconds
can qualify. Use the same provider-duration priority as the existing activity
model: moving time for endurance, recording/elapsed time for strength; fall back
to active stream duration when duration metadata is unavailable. This avoids
counting long pauses as training time. Missing duration fails closed.

Each band independently needs a minimum of BOTH unrounded HR seconds and
allocated eligible speed seconds, with a positive mean speed:

| Band | Minimum for each time |
| --- | --- |
| Z1, Z2, Z3, Z4, GENERAL | 420 seconds |
| Z5 | 300 seconds |

These are cumulative valid times within one activity, including separate bouts.
Exactly 420/300 seconds qualifies; 419.999/299.999 does not. A seven-minute
activity alone does not qualify the general band: seven minutes must fall in
75–92% HRmax, and its allocated speed share must also contain seven minutes.
Invalid bands retain their percentile allocation before validation, so their
fastest speeds are never reassigned to lower zones. Invalid indices are null,
never zero. The UI explains the band-specific reason and does not draw a point.

## Persistence and display

`trainability-index-v2` / `trainability_rank_hrmax_v2` results are stored inside the existing immutable shadow
JSON payload. The model version and activity duration join the cache fingerprint;
duration is omitted from the comparison key. Source model versions, HRmax and
zone bounds identify comparable configurations. No database migration is needed.

The protected `/api/v2/real/trainability` endpoint reads one active calendar
generation, then projects only index summaries for its pinned immutable run keys
and current athlete. It never selects the latest mutable result independently.
Periods cover at most 90 days. Missing or older-model indices request a full
refresh. The new API hides legacy summaries, and the new web parser also handles
v1 summaries from a still-old API or a pinned activity detail by showing a refresh
message. Never relabel or plot legacy bpm values on the normalized scale.

`/trainability` separates sports and configurations. It offers general and Z1–Z5
lines, activity details, a values table and date filtering. Invalid activities
break lines; source configuration changes are not connected. The activity's
HRmod/Vflat page displays the same stored summary.

Deploy API and web to staging from the same reviewed integration commit before
deploying the worker. Wait for the old worker to exit before submitting the full
refresh; Render can briefly keep the old worker polling after the new one is live.
Run the existing FULL_SYNC queue to recompute the history and atomically activate
the new generation. Keep the v2-aware web/API when rolling the worker back because
immutable v2 rows may already exist. No schema, RLS, or permission changes.
Production and canonical load/recovery calculations are unaffected.

HRmax normalization is not a validation of between-athlete fitness ranking.
Comparison still requires comparable sport, technique, equipment, conditions and
physiological zone definitions. With fixed HRmax, v2 is a constant rescaling of
v1 for bands qualifying under both minimum-time rules. HRmax/zone changes remain
separate comparison groups; do not present configuration changes as adaptation.
