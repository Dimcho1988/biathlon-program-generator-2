# Multicomponent stress pilot — approved 2026-09-27

`response-monitoring-v2` replaces the observation dashboard's 50/30/20 score.
It does not change Recovery, plan dosing or the existing block-outcome learner.
The total, components and raw observations are visible on `/response`. An
explicit authenticated `/response?demo=1` view uses synthetic data with writes
disabled. Real mode never substitutes demonstration values for unavailable data.

## Nominal weights (100 points)

| Group | Indicators |
|---|---|
| Subjective 35 | Fatigue 8; sleep quality 4; sleep duration 4; psychological stress 6; soreness 5; training motivation 5; competition motivation 3 |
| Functional 25 | HR/speed TI and comparable pace execution jointly 12; comparable RPE 7; volume execution 2; control tests 4 |
| Autonomic 15 | lnRMSSD 8; resting HR 7 |
| Body mass 15 | Morning ratio and sustained deficit 12; post/pre ratio 3 |
| Biochemistry 10 | CK 2; urea 2; T/C ratio 2; hemoglobin 1; ferritin 1; transferrin saturation 1; CRP 1 |

All weights and score scales are **expert starting settings**, not validated
medical thresholds. Composition percentages and device/method notes provide
context, not additional correlated votes. Hormones are recorded individually;
only their same-sample molar ratio has an independent score allocation.

## Scoring and missingness

- Total = sum(valid indicator weight × score) / sum(valid indicator weights).
  Normalize once at indicator level. One available lab result never inherits
  the entire laboratory group's 10% allocation.
- Coverage = 100 × observed nominal weights. Show `PARTIAL` below 40% or fewer
  than two groups. A partial numeric result remains explicitly partial; no
  observations means null, never zero.
- Show nominal weight, effective share and numerical contribution separately.
- Original onFlows questionnaire anchors are 1 (least adverse) to 5 (most
  adverse), linearly mapped to 0–100. Partial questionnaires are valid. External
  subjective scales remain separate until an explicit scale mapping exists.
- Personal comparisons use clamp(50 + 15 × adverse deviation / spread, 0, 100).
  Spread = max(noise floor, 1.4826 × median absolute deviation). Daily baselines
  use 14 distinct earlier days in the previous 28 calendar days. The current
  day and future are excluded. High HRV remains an atypical-response indicator
  (absolute deviation), not evidence of improved readiness.
- Noise floors: resting HR 3 bpm; lnRMSSD .12; sleep .5 h; TI 3% of reference;
  morning mass max(.2 kg, .5% reference); laboratory 10% of reference (Hb 3%);
  tests use their recorded meaningful-change percentage. These are pilot
  sensitivity settings, not biological constants or diagnostic cutoffs.
- Morning workbook ratio is current/7-day mean, requiring current and four
  valid standardized days. Score sensitivity is 15 points per .5% ratio deficit.
  A 28-day personal reference additionally detects sustained lower mass; both
  interpretations share the same 12% vote, using the more adverse estimate.
- Post/pre ratio uses complete confirmed pairs only; sensitivity 15 points per
  1% net mass decline. Fluid and urine are context, not sweat/glycogen estimates.
- Comparable pace shortfall sensitivity is 15 points per 3%. Volume shortfall
  maps 30% to 100 points from reference 50. Both require explicit comparable
  execution and reason `FATIGUE` or `AS_PLANNED`; time, conditions and coaching
  changes supply no fatigue score. Pace and TI share 12%, never two allocations.
- Session feedback belongs to the **activity date**, not tomorrow's morning.
  RPE requires three previous comparable sessions; manual duration and timing
  remain explicit. Planned/executed observations can be saved without RPE.
- TI uses immutable summaries from the same captured analysis generation,
  existing model admission, valid general band, same sport and comparison key.
  No raw HR/speed recomputation or per-activity network fetch is introduced.
- Labs require three distinct earlier comparable sampling days within 365 days,
  the same laboratory/protocol/fasting/hour/specimen and exact values. T/C needs
  both positive hormones from the same specimen. Scores exist on the sample day
  only. Reference-range findings are shown independently and cannot be averaged
  away. Tests similarly require three prior matching protocol/version/unit/
  direction/conditions days. Repeated trials do not inflate baseline days.
- Three-day means and previous-day changes use **common available indicators**,
  with the same 40% / two-group requirement. Missing days never confirm recovery.
  The total line breaks on a change in indicator mix. Two warmup calculation days
  keep the visible trend independent of chart zoom.
- New observation blocks freeze numeric sleep/mass/TI references at creation,
  together with the existing subjective/HR/HRV baseline. Historical edits cannot
  rewrite those stored references. Existing blocks without v2 references can
  only reconstruct them from available prior observations and their old anchor.

## Scope and validation

The five-component score is observational. Persistent block return and the
existing adaptation controller continue using their established subjective
evidence; they are explicitly labeled on the page. Autonomous experimentation
and learned dose policies are the next separate phase. Future validation must
predict independent subsequent performance/recovery outcomes using held-out
athletes or later blocks, not optimize agreement with this hand-weighted score.

Weights and lab records use the existing revisioned store, additive kind
migration, owner/coach authorization, private recovery sharing and optimistic
conflicts. No real athlete records are inserted for demonstrations. Unit tests
cover no future leakage, fixed block references, missingness, correlation caps,
units, complete pairs, execution reasons and component arithmetic.

The synthetic TypeScript fixture is generated by the Python estimator:
`python -m scripts.generate_response_fixture`. Its schema and arithmetic are
checked by the frontend parser, avoiding a second independent scoring engine.

Primary grounding (does not validate our numerical weights):
- Glycogen/water recovery: https://pubmed.ncbi.nlm.nih.gov/25911631/
- Individual urea/CK variability: https://pubmed.ncbi.nlm.nih.gov/10647551/
- ECSS/ACSM consensus: https://pubmed.ncbi.nlm.nih.gov/23247672/
