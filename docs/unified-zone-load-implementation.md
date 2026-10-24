# Unified zone profile and load, 2026-10-24

Authorized scope: implement the agreed research proposal, not just a comparison.
Repository base e8bbb2f, branch codex/unified-zone-load-2026-10-24.

Accepted inputs and decisions:

- Default HR edges 45/72/79/89/94/100 percent of reference HRmax.
- Tanaka 208 - 0.7 * age only when no individual maximum exists.
- Highest stable 15-second raw measured HR mean can increase the working
  maximum; window range <=10 bpm and increase >=1 bpm. Never use HRmod,
  clamped/estimated HR, or automatically lower a maximum from a lower peak.
- Existing cycling +7 bpm common-reference policy applies once. Raw records
  are unchanged. Initial speed profiles use that same reference frame;
  do not stack a second blanket 7 bpm subtraction or a duplicate sport prior.
- Measured paired TI has precedence. Tests without pulse establish V(t), not
  a measured TI. Without TI, use V50=V(3000s), V6=V(360s) initial anchors.
- Initial reference HR anchors 86% -> V50, 98% -> V6; lower edges 72% ->
  0.78 V50 and 79% -> 0.87 V50. Cycling uses mechanical P(v) before inversion.
- New common lower accounting pair: 45% HRmax -> 20% V6. This is an explicit
  coaching/accounting convention, not a validated physiological equality.
  Remove fixed 5 km/h LOAD exclusions; calibration validity stays separate.
- Z5 high speed is V(10s), high HR is HRmax. Within-zone accounting mapping
  is not an invertible physiological relationship for supramaximal efforts.
- Q uses one speed-style kernel with coefficients 1/1/1/1.2/1.5 for all
  Vflat sports: running, Nordic/rollerski and cycling. Coefficients applied once.
- Manual global leading metric HR or speed; fallback only for missing data,
  no new sensor-quality classifier. One contribution per interval.
- Observed and estimated records/provenance remain distinct. No estimated
  pulses or descent completions admitted to TI calibration.
- Q/E, 7/40, Recovery, method dose and planning must share the same profile
  and kernel. Keep strength and unsupported-sport behavior explicit.

Implementation work:

1. Pure common zone profile and predictor; preserve TI boundaries, invert
   selected V(t) for continuous Tmax without clipping/repositioning edges.
2. Generalize speed kernel and provide identical HR-coordinate evaluation.
3. Persist leading metric, age and HRmax origin/observations; settings UI.
4. Relative rest classification at scoring time, low-speed transport retained.
5. Shared actual ledger projection and planning inputs, old snapshots refreshed
   from immutable samples without sending estimated records to calibration.
6. Automatic raw-HR peak discovery and consistent re-normalization.
7. Unit/integration/UI checks, retrospective real-data comparison, staging
   deployment and live verification. Do not claim completion of pending steps.

Research notes: fixed 7 bpm is a coaching prior, not a universal measured
running/cycling difference. 20% V6 and 45% HRmax are model thresholds.
Primary evidence reviewed includes Cerezuela-Espejo et al. 2018,
Pallares et al. 2016, Vobejda et al. 2013, Schantz et al. 2022,
and Capostagno & Bosch 2010. Exact source links belong in the final research
and validation notes; do not label engineering coefficients as published fits.

Implementation and validation checkpoint:

- Common profile, ZonalPredictor, speed/HR kernel and relative-rest ledger are
  wired into recorded Q/E, 7/40, Recovery, method dosing and planning.
- Both public speed-zone views and 30/70 dosing use the same active profile.
  The selected curve supplies capacity, without moving accepted paired TI edges.
- The explicit C1 10.8 -> 10 second tail preserves the original curve exactly
  on its original domain. V10 remains labelled an estimate unless measured.
- Settings UI/API support age, Tanaka origin and the global leading indicator.
  Both manual and automatic HR schemes share the approved 45%/HRmax endpoints;
  manual interior thresholds are preserved.
- A sustained raw peak can raise the working maximum. Provider responses are
  replayed from a private temporary directory for the second calculation pass.
  The two passes do not download an activity or stream twice.
- Reconstruction reads pinned immutable samples in bounded batches and uses
  only prior-date calibration. Each activity retains its own capacity context.
- Unknown dual-missing time stays unknown. It is never filled with assumed
  expert zone proportions or promoted to observed zero load.
- Historical accounting Q is excluded from speed-curve calibration; recorded
  duration can position expert priors. Raw paired observations and real tests
  remain the only individual speed/HR evidence.
- Speed-only prescriptions without a target HR use their known speed. HRmax
  is not substituted for an absent interval pulse.
- 32 new invariant and integration checks pass, including a real application
  refresh with no HR that produces Q, 7/40 and Recovery from speed.
- 644 web checks, TypeScript validation and a clean production build pass.
  The full Python regression suite passes (3,662 checks). An additional 54
  targeted checks pass after the final boundary/validation refinements.
- Read-only aggregate checks confirm pinned speed data for skiing, cycling
  and running. This is coverage evidence, not validation of the new per-sample
  numerical results. A detailed retrospective comparison remains pending.
- Migration 20261024211104 adds private JSONB preferences, a service-only
  conditional HRmax RPC and matching planning/checkpoint projections. It is
  applied in staging; existing configured profiles have the approved endpoints.
  RPC role restrictions and a missing-profile conditional update are verified.
  Synthetic transactional SQL acceptance tests pass in Database CI, including
  a clean rebuild and the existing concurrency and immutable-history checks.
- Pure mathematical checks run with canonical dependencies; refresh/planning
  integration checks live in the API test partition with its own dependencies.

Q definition (zone-local reference units): let l/u be the speed bounds,
C=(1,1,1,1.2,1.5), R=C*u/l, f=(v-l)/(u-l), and W=1+f*(R-1).
For Z1-Z4, k=W/R; for Z5, k=W. Q is the integral of k in minutes.
HR uses its own within-zone fraction f and the SAME speed-boundary ratio R.
HR-only cold starts explicitly use their HR-boundary ratio until absolute
speed evidence exists. One interval contributes once. Q from distinct zone
references is not a measured whole-body energy quantity.

Research references and interpretation:

- Tanaka et al. (2001), DOI 10.1016/S0735-1097(00)01054-8: age prediction of
  HRmax is a population estimate, not an individually established maximum.
- Cerezuela-Espejo et al. (2018), DOI 10.3389/fphys.2018.01320: threshold/HR
  observations support using individual rather than universal fixed mappings.
- Vobejda et al. (2013), PubMed 24247182: Vmax45 is close to MLSS in the
  studied sample; this does not validate extrapolated V(50 min) from one test.
- Schantz et al. (2022), PubMed 35220936: HR/oxygen-uptake relationships are
  modality-dependent. Capostagno & Bosch (2010), PubMed 20190351, also shows
  why a universal 7-bpm sport difference cannot be asserted.
- The existing cycling +7 common-reference policy is an explicit coaching
  starting assumption, applied once. Observed paired TI has precedence.
- 45% HRmax <-> 20% V6 is an approved lower accounting pair, not a published
  physiological equality or a claim that lower intensities have no adaptation.
- V50/V6 HR anchors and within-zone coefficients remain explicit model priors.
  Z5 HR/speed interpolation is bookkeeping, not a physiological sprint-HR fit.

Remaining release steps: Python CI after test partition correction, PR integration, staging API/web/worker
deploy and live verification.
Production remains separate.

The existing staging API, web and worker have automatic deployment disabled.
Production deployment is separate from this implementation.
