# Trainability index: paired raw HR and Vflat

`trainability-index-v3` / `trainability_paired_raw_lag20_v3` replaces the rank-based
v2 index. Old summaries are marked `REFRESH_REQUIRED`, never mixed into v3.
The model version participates in the shadow configuration fingerprint. Refresh
staging's analysis generation after deploying API, worker and web together.

## Pairing and zones

- Use independent Vflat B65 intervals and original HR observations.
- For a speed interval's midpoint t, interpolate raw HR at t + 20 seconds.
  The lag is elapsed time, not a number of samples. No end extrapolation.
- Require continuous HR coverage through the lag window: no missing/invalid
  observation (outside 30–300 bpm) and no gap greater than 10 seconds. HRmax is
  normalization, not a sensor-validity cutoff.
- Exclude speed intervals with missing/invalid speed, missing actual grade,
  Vflat exclusion flags, dt > 10 s, or actual grade below −3%. Exactly −3% stays.
  An excluded interval removes the whole HR–speed pair.
- Assign each pair to the zone of its shifted raw HR. Lower boundaries are
  inclusive, upper exclusive except the top of Z5. There is no speed sorting.
- Time-weight both members by the eligible speed interval's duration.
- TI = (100 × mean paired HR / explicit HRmax) / mean paired Vflat in km/h.
  GENERAL independently uses 75–92% HRmax, inclusive, not mean zonal indices.
- Require activity duration ≥ 7 min; paired time ≥ 7 min in Z1–Z4 and GENERAL,
  ≥ 5 min in Z5. Compare unrounded durations. Null index means unavailable.

This changes TI zone allocation only. Canonical load, HRmod wave correction,
recovery and the total-volume load accounting retain their own definitions.

## Whole-activity admission

1. Raw-HR screen: absolute change ≥15 bpm within ≤5 s, merging events within
   15 s into one episode. At least three episodes excludes the entire workout
   from TI (`HR_SIGNAL_SUSPECT`). This is a configurable-in-code screening
   heuristic, not proof of sensor truth. A passed screen does not certify HR.
2. Index screen: compare each valid zonal and GENERAL index with previously
   accepted comparable same-sport workouts in the previous 40 calendar days.
   At least seven earlier valid observations of that band are required. More
   than ±20% from the reference excludes the whole workout (`INDEX_OUTLIER`),
   including its other bands. Exactly ±20% stays. Different configuration keys,
   future/equal-start activities and rejected activities do not form references.
3. Reference and predictor aggregation are time weighted. With ≥7 observations,
   use Huber location (1.5 × MAD scale); zero MAD uses the median. With fewer
   observations, predictor aggregation uses the weighted mean and historical
   admission explicitly says `INSUFFICIENT_HISTORY`.

Admission reads the complete pinned active generation, then applies UI date
filters. A lightweight paginated catalog captures the generation once, follows
its immutable activity-set pointer, and checks the expected row count. It avoids
loading the unrelated large HRmod documents used by the ordinary calendar. Thus narrowing the displayed dates does not alter admission. Decisions
can change when the available generation/history changes; this is not persisted
as a claim that the original sensor record is false. Source payloads remain
immutable. The activity-detail summary displays the raw candidate and links to
history for final participation; history and prediction share the same admission.

Both screens only affect TI/prediction. They do not remove workouts from total
training volume and do not disable HRmod solely due to an index outlier.

Sports remain separate; source labels Walk and NordicSki are not automatically
merged because not every walk is roller skiing. Comparable equipment and
conditions remain an expert assumption within a source sport.

## HR–speed prediction

See `RECOVERY_SPEED_MODELS.md`. One monotone HR–duration map is composed with the
personal Vflat–duration curve for both forward and inverse predictions. Outputs
identify index-based anchors versus expert midpoint fallbacks. No physiological
validation or precise effort prescription is implied by numerical reversibility.
