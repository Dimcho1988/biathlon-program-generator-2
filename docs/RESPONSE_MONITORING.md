# Response monitoring v1 — staging pilot

This is an observation module, independent of canonical load/recovery and the
training plan. `/response` is reachable from the dashboard menu. Selecting a day
reveals scores, fixed weights, contributions, source values, missing coverage,
personal reference and the previous day's sessions. Athlete self reports and
coach-entered context are separate, small, append-only records.

## Meaning of the graph

All scores are **conditional pilot points**, not percentages of physiological
stress, recovery, fitness or probability of illness. Weights and thresholds are
expert starting settings, not a clinically or prospectively validated model.

| Component | Weight | Mapping |
| --- | --- | --- |
| Morning self report | 50% | Mean of five equally weighted questions; `(answer−1)×25` |
| Perceived session response | 30% | `clamp(50+15×(RPE−median prior comparable RPE), 0, 100)` |
| Resting HR and RMSSD | 20% | Equal mean of the two available, referenced device scores |

The questionnaire asks sleep quality, fatigue, soreness, psychological stress and
motivation. Every answer uses explicit 1–5 anchors, oriented higher = more burden
(worse sleep and less motivation score higher). No default answers. All five are
required. Each question therefore contributes at most 10 points to the total.
Pain/symptoms and free text are separate contextual signals, not weighted points.

Total = `.5×subjective + .3×RPE + .2×physiology` **only if all three exist**.
Available coverage sums their fixed weights. Missing values are not zero, carried
forward, interpolated or redistributed. Graph lines break at missing days. A rest
day has no RPE response from that day's nonexistent session; consequently the
following morning may have no total. Individual component trends remain useful.

RPE relates to the **whole session** on 0–10. The athlete confirms total minutes
including within-session rests; `sRPE=RPE×minutes`. This descriptive quantity is
never added to HR-derived load. Imported `icu_rpe` from Intervals is shown with its
source. Unknown assessment timing/whole duration prevents imported-only values
from creating an RPE deviation or sRPE estimate. Manual confirmation overrides
import and survives sync. The API also accepts both existing activity-ref formats.

Comparable sessions: same sport, same reported timing (immediate, 10–60 minutes,
next day), duration within ±20% of the current session, L1 distance of normalized
canonical equivalent zone times ≤0.30. At least three strictly earlier dates in
the previous 60 calendar days are required; sessions on the same day and in the
future cannot enter the reference. Previous-day deviations are duration-weighted;
if any session lacks a valid deviation the daily RPE component is unavailable.
Terrain/equipment/weather are not matched in v1, limiting precision.

Personal references require ≥14 observed days in the previous 28 calendar days,
excluding today and the future. Center is median, spread is `max(floor,1.4826×MAD)`.
Floors: subjective 10 points, RHR 3 bpm, ln(RMSSD) 0.12. Device scores use
`clamp(50+15×signed_deviation,0,100)`. HRV is two-sided around the usual level:
`50+abs(score−50)`; high HRV is not interpreted as permission to increase load.
Both positive RHR and positive RMSSD with usable references are required. SDNN
does not substitute for RMSSD. Imported subjective scales remain raw and separate
because their anchors are not guaranteed. Intervals does not guarantee constant
device/protocol provenance; interpretation requires consistent measurement.

## Amplitude, persistence and planned recovery

The v1 daily status and block return summary explicitly assess **subjective
response**, not holistic physiological recovery. Other components stay visible
alongside them. This avoids pretending that mixed measurement scales form a
validated recovery endpoint.

A coach or athlete with plan-edit rights sets start, load-end, recovery-end and
phase prospectively. Blocks do not overlap. A started block cannot be extended or
redefined. Personal references are frozen from data available **at creation**;
later edits/imports do not move this reference. Insufficient initial history stays
uncalibrated for that block. The UI shows the actual freeze date.

Subjective deviation >1 reference spread is called elevated. In BUILD before
load-end it is labelled as a rise during a loading block, without an automatic
reduction. After load-end the phase is recovery. At recovery-end an elevated
observation signals review. A pain/symptoms report is independently visible.

Each block reports peak deviation, elevated observed days and coverage. Return
requires two consecutive observed calendar days after load-end within the usual
subjective range and without a pain/symptoms flag. Missing data or later elevation
resets confirmation; an early return cannot mask a relapse at the deadline.
Summaries cover the full block through the selected end date, independent of the
chart start/zoom. A missing final observation cannot prove recovery. Completed
blocks retain their endpoint summary; they do not silently extend their deadline.

## Inputs, permissions and persistence

* Morning forms are filled by the athlete in onFlows. Historical entries are
  marked by stored entry/revision time; absent observation time remains null.
* Imported wellness and activities are read from one pinned active generation.
  They are never written by this module or mixed across sync generations.
* Dates use the athlete timezone, not the browser/server UTC calendar. Editable
  observations are limited to the past 90 days; charts to 1–90 days.
* Owner or explicit recovery-sharing/authorized organizational access is required
  to view this module. Overview-only/plan-only grants do not expose wellness.
* Only owners write DAILY/SESSION; context and tests require plan-edit permission.
  The BFF resolves the alias and actual actor from verified auth, enforces origin
  and bounded bodies, and keeps service credentials server-side.
* Database records have RLS with no anon/authenticated table grants. A service-only
  RPC checks the actor and appends immutable revisions under an advisory lock.
  Expected revision prevents silent concurrent overwrite. No sync job is enqueued
  for a form save; manual records live outside canonical snapshot replacement.
* Optional tests record protocol/version, value/unit/direction and conditions,
  with explicit coach-attested comparability. They carry zero automatic weight;
  missing tests do not penalize the athlete. They do not schedule mandatory work.

## Boundary for the future controller

`mode=OBSERVATION_ONLY`, `automatic_action=NONE`, `automatic_increase=false`,
`changes_recovery=false`. No writes to Recovery/tau/sensitivity, Tref, 7/40,
HRmod, Vflat, trainability or the training plan. In particular, low subjective
stress **never implies inadequate stimulus**. Trainability remains a contextual
linked view. Any future dosage controller must be a separate, versioned decision
layer with its own validation, coach review and sufficient observation coverage.

## Rollout and verification

Apply the additive response-record/provider-RPE migration, deploy API/web and
worker to staging. Older generations remain readable without provider RPE; the
next ordinary successful sync imports that metadata. Do not fabricate reports in
an actual profile. Fixtures exist only under explicit `ONFLOWS_DATA_MODE=fixture`.
Verify schema/role permissions, RPC rollback tests, API and web regression suites,
production build, CI migration replay, and staging navigation. The first real
subjective trend requires athlete entries; references need the history above.
