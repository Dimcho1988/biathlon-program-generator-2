# Activity load evidence

Recovery, planning history, observed 7/40-day windows and training-return
history use `biathlon.load_evidence` to interpret each activity's provenance.

- Known non-strength sports can be reconstructed from pinned HR recordings,
  including sports without a supported speed model. Speed contributes only
  where the existing sport-specific model supports it.
- Reconstructed Q replaces old Q. It is never added to it or relabelled from
  an incompatible convention. The existing component projection verifies the
  original daily E ledger before recomputing E from the new Q.
- Recorded active time, rest and unknown intensity conserve recorded duration.
  Known unknown intervals use the existing separately labelled planning
  estimate, shared with Recovery. They do not become measured learning data.
- Provider failures, missing dates, invalid ledgers and unreconstructible Q
  remain unresolved. Diagnostics include the activity, date, sport and reason.
- Strength retains its separate exposure. Legacy metadata-only calibration
  flags are not mistaken for a recording ledger. Missing component Q does not
  erase valid observations for the other components.
- The planner and Recovery certify the current 40-day window. Dated older
  unresolved records do not block that window; undated records still do.
  Each observed progression window validates its own contributing records.

The recording version is v7, planning-history estimate v9 and planner v69 so
cached projections and drafts are rebuilt. No recovery thresholds, initial
component volume rules or physiological coefficients change.

Regression coverage includes mixed HR-only/speed-only sports, partial and
absent intensity, unsupported speed, incompatible Q, invalid ledgers,
provider gaps, strength, window boundaries and reconstruction idempotency.
