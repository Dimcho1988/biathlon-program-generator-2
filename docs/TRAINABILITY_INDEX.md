# Trainability index, staging v1

The index is an exploratory longitudinal descriptor, in bpm/(km/h). Lower values
mean less modulated heart rate per allocated flat-equivalent speed. It is not a
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
- Index = duration-weighted mean HRmod in the band divided by the
  duration-weighted mean allocated Vflat (km/h). HR and speed are independently
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

Each band independently needs at least 60 unrounded seconds of HR and at least
60 allocated seconds of eligible speed, with a positive mean speed. Exactly 60
seconds qualifies. Invalid indices are null, never zero. The UI explains each
reason and does not draw an index point for it.

## Persistence and display

`trainability_rank_v1` results are stored inside the existing immutable shadow
JSON payload. The model version and activity duration join the cache fingerprint;
duration is omitted from the comparison key. Source model versions, HRmax and
zone bounds identify comparable configurations. No database migration is needed.

The protected `/api/v2/real/trainability` endpoint reads one active calendar
generation, then projects only index summaries for its pinned immutable run keys
and current athlete. It never selects the latest mutable result independently.
Periods cover at most 90 days. Missing old indices request a full refresh.

`/trainability` separates sports and configurations. It offers general and Z1–Z5
lines, activity details, a values table and date filtering. Invalid activities
break lines; source configuration changes are not connected. The activity's
HRmod/Vflat page displays the same stored summary.

Deploy API, worker and web to staging from the same reviewed integration commit,
then run the existing full refresh to populate indices for stored activities.
Production and canonical load/recovery calculations are unaffected.
