# Private, lossless history archival

Detailed model inputs and derived channels remain in PostgreSQL for 365 days
after the activity. Older large channels move to the private `onflows-history`
Storage bucket. Compact scientific summaries, scientific hashes, model versions,
activity identities, generation pointers and training plans remain in PostgreSQL.
No historical version or archive object is deleted by this feature.

An activity needs a known date, and every known catalog/generation date must be
older than the policy cutoff. The payload must also have been stored at least
seven days ago. Fields below 64 KiB stay inline. Missing dates and recent
activities remain inline. Existing lossless columnar compression is preserved
inside the archive; raw model samples are compressed with XZ without rounding.

The worker checks only when no sync job is claimed, at most once every 15 minutes,
and processes at most five records per pass by default. `--once` does not perform
archival. Failure defers maintenance and leaves sync processing available.
Unsupported oversized fields remain inline. The worker resumes its scan between
passes so a batch of oversized records cannot block later eligible history.

Each immutable object is addressed by its SHA-256 hash. Before changing the
database, the worker uploads, downloads, verifies compressed and JSON checksums,
and compares the full decoded scientific payload. The database then uses a
locked compare-and-swap to replace only eligible channels. It rejects changes to
summaries, identities or model metadata, missing objects, public buckets, stale
payloads and recent activities. A subsequent read verifies the stored result.
API readers fetch archived channels through server-only credentials and restore
their exact logical values before returning existing responses. No public URLs
or Storage credentials are sent to browsers.

## Rollout

The migration creates the bucket and a **disabled** policy. Deploy the API and
worker containing the archive reader before enabling the staging policy through
an administrator database session:

```sql
update public.onflows_history_archive_policy set enabled=true where id;
```

The application service role can read the policy but cannot modify it. Disable
it with the same statement and `enabled=false` to pause new archival; existing
archives continue to load. No subscription or hosting plan changes are required.

Use the existing server environment without printing or copying its credentials:

```sh
python -m apps.api.archive_history --max-rows 2
python -m apps.api.archive_history --apply --max-rows 2
python -m apps.api.archive_history --backup-kind shadow --key HEX_RUN_KEY
python -m apps.api.archive_history --restore-kind shadow --key HEX_RUN_KEY
```

The default command is a preview with no writes. Explicit backup verifies objects
without replacing the database payload and can be used on a recent record.
Restoration accepts `input` as well as `shadow` and does not require archival to
be enabled. Archive objects remain available after restoration. Output contains
only numerical totals, mode or error type; payloads and credentials are not logged.

For a deployment check on the existing worker, temporarily set
`ONFLOWS_HISTORY_ARCHIVE_VERIFY=shadow:HEX_RUN_KEY` (or `input:HEX_INPUT_KEY`).
At startup it backs up the selected inline channels, verifies the real repository
reader and confirms the database row is unchanged. Logs contain only a success
marker and numerical totals. Clear the variable after successful verification.

## Validation

Python tests cover exact floating-point and null/missing-field restoration,
already compacted channels, real repository readers, duplicate uploads,
checksums, path isolation, bounded decompression, failure before database writes,
compare-and-swap conflicts and the idle worker tick. Database CI rebuilds all
migrations and rolls back synthetic archive/restore fixtures, verifying role
boundaries, retention eligibility, summaries, immutable metadata and audit events.

This bounds detailed data kept in PostgreSQL over time, not total lifetime
Storage usage. Archives still consume Storage and download traffic. PostgreSQL
also retains summaries, plans and other metadata; freed pages can be reused but
archival does not automatically reduce provisioned disk or its invoice.
