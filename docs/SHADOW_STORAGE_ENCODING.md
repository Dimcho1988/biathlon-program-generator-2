# Large shadow payload transport

## October 2026: smaller lossless representation

The v2 codec groups equal-shaped rows by column and compresses them using XZ/LZMA
(preset 3). Irregular rows use a row layout, preserving absent keys versus null.
No values are rounded, resampled or removed. The v1 gzip decoder remains supported.
The four eligible arrays are `timeseries`, `speed_test_series`, `segments_15s`
and `hrmod_waves`; scientific summaries and identifiers remain ordinary JSON.
Decoding verifies size, SHA-256, row count, column dimensions, stream termination,
and a 32 MiB LZMA dictionary budget. Scientific result hashes are not recomputed.

The model input table already stores only normalized model channels, not full
provider payloads, GPS or activity names. Keep these inputs for reproducibility;
an Intervals activity may later change, be deleted or become inaccessible.

### Bounded maintenance

Deploy the decoder-capable API **before** the writer/worker. Do not roll the API
back to a version lacking the v2 decoder after any v2 values have been stored.
The compaction RPC is service-role only, compares the previous representation
hash, and rejects changes to all non-series fields or scientific result hashes.
It does not provide general UPDATE permission on the immutable table.

On the existing worker, preview ten records without writing:

```sh
python -u -m apps.api.compact_shadow_storage --max-rows 10
```

After preview, apply in bounded batches, retaining each reported cursor:

```sh
python -u -m apps.api.compact_shadow_storage --apply --max-rows 100
python -u -m apps.api.compact_shadow_storage --apply --max-rows 100 --after LAST_VERIFIED_RUN_KEY
```

Each converted record is decoded and compared before writing, then read back
and compared again. Existing smaller representations are kept. A conflict or
integrity failure stops the run. Running a completed range again is safe.
No histories, inputs, plan revisions, model versions or activity samples are
deleted, and no Intervals requests are made. JSON byte savings are not physical
disk savings: measure `pg_database_size`/`pg_total_relation_size` before and after
Postgres reclaims the old tuples. `VACUUM FULL` needs a maintenance window and
temporary free disk space; use bounded lock/statement timeouts.

## Original gzip rollout

Staging refreshes on 2026-09-07 repeatedly failed while publishing a shadow
result of about 14 MB. Render recorded HTTP 520/521 and heartbeat/failure-record
errors; `pg_postmaster_start_time()` confirmed a database restart at that time.
The job eventually exhausted its three attempts. The old generation stayed
active, so no newly calculated index became visible.

`shadow_storage.py` losslessly gzip-compresses large `timeseries` arrays and
base64-encodes them inside the existing JSONB result payload. The envelope
contains an explicit codec version, original byte length, row count and SHA-256.
The decoder bounds decompression and verifies length, digest and row shape.
Small and legacy arrays remain supported.

The Supabase repository encodes only at publish time and decodes on all three
full shadow read paths: pinned activity view, pinned shadow read and legacy
shadow read. Logical JSON values, model versions, configuration fingerprints,
result hashes and immutable run keys do not change. SQL projections of
`trainability_index` and `zone_summary` remain directly available. Calculations,
HR/Vflat values, zone rules, 60-second and 7-minute gates are unchanged.

No schema, grants, RLS or scientific model migration is involved. New encoded
rows require the decoder-capable API. Deploy the API first, then the worker; keep
the decoder available if rolling the worker back. Use the normal full-sync queue
to resume after publication; already persisted matching results are reused.
