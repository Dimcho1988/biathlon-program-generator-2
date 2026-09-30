# Running grade correction and paired trainability

Running activities use `vflat_run_grade_table_v1`, independently of ski/roller
B65 dynamics. Selection uses the provider sport identifier, never the activity
name. Run, TrailRun, VirtualRun and explicit track, road, cross-country and
treadmill running identifiers are supported. Index histories remain specific to
the exact sport; selecting the running correction does not pool different sports.

The provisional expert table supplied on 2026-09-30 is retained exactly:

| Grade, % | Flat-speed multiplier |
| --- | --- |
| -32 | 1.60 |
| -26 | 1.40 |
| -24 | 1.20 |
| -20 | 1.10 |
| -18 | 1.00 |
| -14 | 0.90 |
| -10 | 0.87 |
| -4 | 0.90 |
| 0 | 1.00 |
| 4 | 1.12 |
| 10 | 1.50 |
| 20 | 2.30 |
| 30 | 3.40 |

Equivalent flat speed = observed speed × linearly interpolated multiplier.
The rounded polynomial shown with the supplied chart is not substituted for the
table. Outside [-32%, +30%], samples are unsupported. Existing speed/altitude
preprocessing and 21-second output smoothing remain bounded by recording blocks.
Unsupported grades, missing speeds, turns and low speeds remain excluded after
smoothing. Ski acceleration/deceleration, inertia and session terrain regression
do not apply to running.

TI still pairs raw HR at t+20 seconds with flat speed at t. The existing minimum
paired times, activity-duration gate, HR reliability screen and causal 40-day
outlier filter (20% after at least seven reference activities) remain unchanged.
Downhills below -3% remain excluded from TI and accepted speed-test samples.

Both the derived-run cache and profile comparison key include the selected model
versions. Existing running summaries corrected with B65 require recomputation
from observations. They are not relabelled, mixed into the new TI average or
converted into new measurements. Non-running fingerprints remain unchanged.
Frozen activity tests retain their source versions; manual flat tests retain
their measured values and have no grade-model dependency.

The speed page defaults to the sport with the most recorded time in the last
40 days (falling back to its 90-day activity/test window). An explicit sport
selection always wins. Manual tests show an editable sport field and keep their
identity/revision when corrected. Tests from other sports remain discoverable.

HR-duration inversion accepts an exact expert boundary within floating-point
rounding tolerance (relative 1e-10, absolute 1e-8 seconds). This does not relax the
expert duration range or move maximal-test anchors.

Deployment requires worker, API and web updates followed by a normal full sync
for athletes with old running summaries. Recheck the pinned generation's model
versions and TI admission counts after sync; do not rewrite historical inputs.
