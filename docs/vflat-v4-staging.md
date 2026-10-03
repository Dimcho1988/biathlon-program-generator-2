# Vflat V4 staging parameters

Approved for staging on 2026-09-13 after local comparisons on three TCX files.

- Uphill increment: `1 + 1.20 * (B65 - 1)`, equivalent to reducing V3's `1.50` increment by 20%. Flat and negative stationary multipliers stay unchanged.
- Grade memory: `effective = actual + 1.70 * reference * weight(age)`.
- Reference: mean of the finite negative grades in the preceding 15 elapsed seconds, evaluated when actual grade crosses from below -3% to at least -3%.
- Weight: `(exp(-age/18) - exp(-20/18)) / (1 - exp(-20/18))` before 20 seconds, exactly zero at and after 20 seconds. New descent re-entry cancels the window.
- Stop: raw speed at most 1 km/h for at least 3 elapsed seconds cancels the memory. This is four consecutive 1 Hz samples, without retrospective cancellation. Resuming the same descent cannot re-arm it; a new descent entered while moving is required.
- Stationary grade is clipped to [-3%, +15%] after applying memory. Actual grade continues to drive terrain events, climb memory and the separate descent-entry anchor.
- The legacy post-descent speed subtraction is disabled. Input speed median remains 11 points; final median remains 21 points. A median can retain abrupt transitions; no extra smoothing or rate limiter was introduced.
- Preparation, memory and filtering respect recording blocks. Height or speed across a recording gap must not create artificial grade or acceleration.

`VFlatB65Config` contains the tuning coefficients. Change the model/config version when changing defaults, so the existing configuration fingerprint invalidates old cached results. No database migration is needed: the added grade diagnostics use the existing JSON payload.

After deploying the staging API, worker and web from the same commit, run the normal **Обнови данните** flow. The worker recomputes the derived runs and atomically activates the new generation. Charts, 15-second summaries, speed-duration inputs and Trainability then use the same final Vflat. Canonical HR load and HRmod algorithms are unchanged.

Validation: independent reconstruction of the approved experimental V4 at weight 1.70 matched every stationary/dynamic/final component across all three TCX files (largest final numerical difference below 3e-14 km/h). Final valid-sample means: 18.0488311977, 20.1609546886 and 16.7367790402 km/h, on 4645, 1254 and 4773 valid points respectively. Private input files are not committed.

Regression tests cover the curve anchors, changing current grade, exact 20-second return, raw-speed stop confirmation and lock, re-entry, reference history, recording gaps, payload propagation and cache invalidation.
