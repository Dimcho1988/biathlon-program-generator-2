# onFlows cloud pilot configuration

The API is read-only. It requests athlete settings/activity lists, activity
detail, streams, and wellness through the existing `IntervalsClient`; it has no
provider write endpoint. Next.js calls FastAPI server-side only.

## Backend environment

All values are required unless a default is shown. Never prefix them with
`NEXT_PUBLIC_` and never commit their values.

* `ONFLOWS_SERVICE_TOKEN` — random service-to-service secret shared with Next.js.
* `INTERVALS_ACCESS_TOKEN` — read-only provider credential.
* `INTERVALS_ATHLETE_ID` — provider identity, backend only.
* `ONFLOWS_ATHLETE_ALIAS` — pseudonymous public alias.
* `ONFLOWS_HR_ZONE_BOUNDS` — six comma-separated integer HR boundaries.
* `ONFLOWS_ATHLETE_TIMEZONE` — IANA timezone.
* `ONFLOWS_INTRAZONE_VERSION` — `intra_zone_linear_v1`.
* `ONFLOWS_TREF_VERSION` — approved fixed-Tref parameter version.
* `ONFLOWS_RECOVERY_VERSION` — approved canonical recovery parameter version.
* `ONFLOWS_HISTORY_DAYS` — `41` through `90` (default `90`).
* `ONFLOWS_SNAPSHOT_SALT` — random private salt for provider-safe cache keys.

## Next.js server environment

* `ONFLOWS_DATA_MODE` — `fixture` or `api`.
* `ONFLOWS_API_RESOURCE` — `real` for the protected endpoint.
* `ONFLOWS_API_BASE_URL` — HTTPS FastAPI origin.
* `ONFLOWS_SERVICE_TOKEN` — same server-only service secret.

Refresh is explicit (`POST /api/v2/real/refresh`). The latest valid snapshot is
replaced only after complete analysis; retrieval/analysis failures retain it.
