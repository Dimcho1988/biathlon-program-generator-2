# onFlows cloud pilot configuration

The API is read-only. Each athlete authorizes the existing onFlows OAuth app
directly in Intervals.icu. The authorization code and provider access token
remain server-side; Next.js receives neither.

## Persistent data boundary

Apply `supabase/migrations/202608150001_cloud_oauth_pilot.sql` once in the
Supabase SQL editor. The migration creates three RLS-enabled, server-only
tables:

* one-time OAuth state hashes;
* encrypted Intervals access tokens and the minimum connection metadata;
* aggregate athlete snapshots containing `training-status-v1` and
  `load-history-v1` read models.

Raw activities, streams, GPS, provider payloads and athlete names are never
stored by this cloud layer. Supabase `anon` and `authenticated` roles receive no
table access. Only the FastAPI service uses the Supabase secret key.

Intervals access tokens do not use refresh tokens. A new OAuth grant replaces
the previous token; disconnect/revoke requires a new authorization.

## Backend environment

Never prefix these values with `NEXT_PUBLIC_` and never commit their values.

* `ONFLOWS_SERVICE_TOKEN` — generated in the shared Render environment group.
* `INTERVALS_CLIENT_ID` — existing onFlows OAuth client ID.
* `INTERVALS_CLIENT_SECRET` — existing onFlows OAuth client secret.
* `INTERVALS_REDIRECT_URI` — exact FastAPI callback HTTPS URL.
* `OAUTH_STATE_SECRET` — independent generated state-signing secret.
* `SUPABASE_URL` — Supabase project origin (for example,
  `https://project-ref.supabase.co`), without `/rest/v1`; the server client
  appends the REST path.
* `SUPABASE_SECRET_KEY` — server-only Supabase secret key; never a publishable
  or anon key.
* `ONFLOWS_TOKEN_ENCRYPTION_KEY` — generated 256-bit Render value used for
  AES-256-GCM token encryption.
* `ONFLOWS_ATHLETE_ALIAS` — pseudonymous public pilot alias.
* `ONFLOWS_HR_ZONE_BOUNDS` — six comma-separated integer HR boundaries.
* `ONFLOWS_ATHLETE_TIMEZONE` — IANA timezone.
* `ONFLOWS_INTRAZONE_VERSION` — `intra_zone_linear_v1`.
* `ONFLOWS_TREF_VERSION` — approved fixed-Tref parameter version.
* `ONFLOWS_RECOVERY_VERSION` — approved canonical recovery parameter version.
* `ONFLOWS_HISTORY_DAYS` — `41` through `90` (default `90`).
* `ONFLOWS_SNAPSHOT_SALT` — generated private salt for provider-safe cache keys.
* `ONFLOWS_WEB_BASE_URL` — exact Next.js HTTPS origin.

There is no manually entered `INTERVALS_ACCESS_TOKEN` or
`INTERVALS_ATHLETE_ID`. Both arrive through the verified OAuth grant.

## Next.js server environment

* `ONFLOWS_DATA_MODE` — `fixture` or `api`.
* `ONFLOWS_API_RESOURCE` — `real` for the protected endpoint.
* `ONFLOWS_API_BASE_URL` — HTTPS FastAPI origin.
* `ONFLOWS_SERVICE_TOKEN` — inherited from the same shared Render environment
  group as FastAPI.

The browser starts OAuth through a same-origin Next.js route. Next.js calls the
protected FastAPI authorization endpoint server-side and validates that the
returned destination is exactly `https://intervals.icu/oauth/authorize`.

Refresh remains explicit (`POST /api/v2/real/refresh`). A complete analysis
atomically replaces the persisted aggregate snapshot. Retrieval or analysis
failures retain the last valid snapshot. Fixture mode remains available only
when `ONFLOWS_DATA_MODE=fixture` is set explicitly.

The current status remains available at
`GET /api/v2/real/training-status`. The precomputed 90-day zonal series and
privacy-minimized activity aggregates are available at
`GET /api/v2/real/load-history`. Neither endpoint returns raw streams or
provider identifiers. Existing `training-status-v1` rows remain readable
during rollout; one successful refresh upgrades the stored envelope.

The same atomic snapshot can include `recovery-history-v1`, exposed through
`GET /api/v2/real/recovery-history`. It contains the canonical precomputed
load-readiness history and read-only recovery parameters. Wellness freshness
and coverage are explicit, but wellness does not alter readiness until an
approved integrated-recovery model is available.
