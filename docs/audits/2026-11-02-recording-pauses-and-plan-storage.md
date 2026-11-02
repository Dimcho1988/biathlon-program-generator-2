# Recording pauses, complete plan storage and inline workout details

Confirmed provider timer stops were counted as unknown sensor intensity over
elapsed time. The result could label otherwise well-recorded sessions incomplete
and impute expert training load during those recording pauses.

The v8 common recording ledger separates only explicitly matched timer-stop
intervals without an available HR or speed observation. Probable pauses,
unexplained gaps and malformed stop boundaries remain unknown. Available speed
observations are preserved even when an HR interval carries a stop annotation.
No HR, Q or physiological rest is inferred for a timer pause.

The conservation identities are:

- `active_seconds + rest_seconds + unknown_seconds = recorded_seconds`;
- `recorded_seconds + paused_seconds = elapsed_seconds`.

Both new fields are validated when present; older conserved ledgers remain
readable. The model version invalidates derived load caches. The separate
40-day coverage check and supported estimation policy continue to protect
against unknown training and missing provider/calendar records. Dashboard
warnings are rebuilt from current common-indicator coverage rather than a stale
HR-only activity count.

Plan storage uses `primary-session-reference-v1`: an exactly equal legacy
`day.session` becomes a local reference to the complete `day.sessions[0]`.
Visible and lookahead sessions, frozen instructions, doses, numeric types,
source fingerprints and publication checkpoints remain intact. Unequal aliases
are retained. Reads restore independent JSON aliases for existing consumers.
Draft, background publication, active plan, pending proposal and history readers
use the same lossless boundary. SQL overview projections already prefer the
complete `sessions` array. The 2 MiB draft and 4 MiB active-revision limits remain.
A deterministic draft capacity rejection is a permanent computation failure,
not a reason to run the same expensive calculation three times.

The weekly interface places the selected day's details immediately below its
summary and before the next day. The summary also closes the expanded details.

Verification includes the complete 733-test web suite, lint, TypeScript and a
production build; 1017 canonical tests; storage/publication, coverage and model
regressions. A private 50-recording replay exercises both leading indicators
(100 comparisons), preserving measured zone times and Q. A captured failed plan
retains all 24 visible/lookahead sessions and roundtrips with identical serialized
JSON values while shrinking from 2,310,237 to 1,739,076 bytes (24.72%) before
persistence metadata. Two stored copies occupy 3,478,176 bytes before the active
revision envelope. Private fixtures are excluded from the repository.
