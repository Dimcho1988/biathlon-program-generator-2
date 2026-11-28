# Vflat and trainability correction — 2026-11-20

The session-density regression introduced in commit 0443b93 (PR #69,
2026-09-18) divided every NordicSki/Walk Vflat sample by a factor derived from
provider ascent per kilometre. Its original notes explicitly limited the
evidence to Valya's recordings and reported inconsistent improvement in other
athletes. At 20 m/km it removed 8.84%; at 28 m/km it removed 14.35%.

## Behavior

- Retire the session-wide density reduction. The terrain diagnostic has factor
  1 and reason `SESSION_TERRAIN_CORRECTION_DISABLED`; provider totals remain
  visible. Preserve the sample-grade curve, uphill coefficient 1.10, grade
  memory, acceleration/deceleration coefficients and HRmod. Downhill samples
  can still have Vflat below measured speed; there is no artificial lower clamp.
- Require finite, nonnegative measured raw speed for TI. Exclude near-stop
  runs <=1 km/h whose observations span >=3 s. Missing speed, boundary rows and
  recording gaps split runs. Keep short slowdowns and genuine slow climbing.
  Count confirmed-stop exclusions separately. TI still pairs raw HR at +20 s,
  excludes grade below -3%, and requires 7 min (5 min in Z5).
- Apply the causal +/-20% history screen to each band separately. Preserve the
  rejected value as `candidate_index`; its `index` is null and `valid` is false.
  `PARTIAL` activities contribute their remaining valid bands to later history,
  speed calibration/load, stress and learning. Signal failure excludes the
  whole activity. Reference windows remain 40 days with at least 7 earlier
  same-sport/configuration observations. No future data enter admission.
- Display raw/Vflat means over identical supported independent speed intervals,
  weighted by duration, including stops and descents. Do the same within each
  15-second diagnostic segment, splitting fractional boundary intervals.
  Sparse HR sampling cannot bias these speed means. The index has its own
  explicit coverage; it is not the whole-recording average.

## Identity and rollout

- Vflat: `vflat_b65_dynamic_v6_sample_terrain` /
  `vflat_b65_config_v8_no_density_scaling`.
- TI: `trainability_paired_raw_lag20_v5_measured_motion`, schema v3 with additive
  stop diagnostics and PARTIAL admission.
- These versions enter recording fingerprints and comparison keys. Old TI is
  shown as needing refresh. HR-only recalibration rejects old speed versions,
  so it cannot relabel old speed values as current.
- Frozen imported ski tests retain their original provenance. Existing model
  compatibility checks exclude old-transform anchors; manual flat tests remain
  compatible. Re-select imported test intervals from refreshed recordings if
  their Vflat calibration is needed. Do not relabel or silently scale old tests.
- Deploy API/web/worker from one staging merge, wait for the old worker to stop,
  then use the existing FULL_SYNC queue for the two audited profiles first.
  Verify an atomically activated generation with current versions, including
  downstream speed/load/recovery/planning views. No SQL schema migration.
  Production's separate branch is outside this change.

## Validation

Synthetic regressions cover density boundaries, uphill/downhill behavior,
missing raw speed, stop-duration boundaries and gaps, slow climbing, fractional
speed-time weighting, partial admission and causal references, downstream
per-band speed load, legacy-speed rejection during HR recalibration, and UI
candidate visibility. Existing HR recalibration tests compare the fast path
with a full recomputation for five sports and regular/sparse/gapped recordings.

An offline replay of four audited recordings reproduced the previous Vflat
curve after multiplying the new result by the old factor (maximum difference
<3e-9 km/h); the HR zone summaries were identical. Shared-interval means:

| Profile/date | Raw km/h | Previous Vflat | Revised Vflat |
|---|---:|---:|---:|
| Profile A, 17 Nov | 12.60 | 11.97 | 13.96 |
| Profile A, 19 Nov | 13.54 | 12.75 | 14.54 |
| Profile B, 17 Nov | 12.72 | 12.34 | 14.29 |
| Profile B, 19 Nov | 14.74 | 14.04 | 16.39 |

These four cases confirm the implemented change, not physiological validation
across routes/snow/technique. The 1 km/h threshold and existing dynamic model
remain empirical assumptions. Private raw recordings are not committed.
