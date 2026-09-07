# Large shadow payload transport

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
