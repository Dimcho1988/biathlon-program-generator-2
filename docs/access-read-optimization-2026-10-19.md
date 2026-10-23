# Owner authorization read optimization

## Implemented behavior

`currentAuthorizedAthlete` verifies the session claims and reads the selected
athlete through the authenticated Supabase client. When that row belongs to
the verified user and `is_owner === true`, organization memberships, coach
assignments and sharing grants cannot change the existing permission helpers'
answers. The owner path now supplies empty graph inputs to those same helpers.
The athlete/profile reads, profile-name fallback and request-local React cache
remain in place.

| Authenticated request | Table reads before | Table reads after |
| --- | ---: | ---: |
| Verified owner | 5 | 2 |
| Coach, shared viewer or self row without ownership | 5 | 5 |

This removes three Supabase requests for each owner authorization execution.
Claims/JWKS/session traffic is not included in those counts. The old graph
reads ran in parallel with the profile read, so fewer requests do not establish
a proportional latency improvement. Live staging timing remains unverified.

An unrelated graph-read failure no longer denies a verified owner. Missing
claims or an absent/unreadable athlete row still deny access. Other viewers
still need fresh graph reads and retain the existing fail-closed behavior.
The implementation does not add a cross-request permission cache.

## Verification

Eleven regression cases exercise the actual helper with a mocked authenticated
database client: owner request counts, foreign athlete selection, missing
ownership, sharing revocation, graph failures, profile fallback and missing
claims/athlete rows. The original implementation fails two of these cases;
the changed helper passes all eleven. Repeated direct calls in Vitest do not
prove a React server-request cache boundary; that cache is unchanged.

The complete web suite passes 589 tests, along with lint, type checking and the
default Next.js production build.
No live account, database or training-plan write was used for this verification.

## Other reviewed paths and limits

Management state polling already uses one serial timer, pauses network reads
while hidden, and aborts on effect cleanup. Sync polling also skips hidden tabs
and has bounded cadence and cancellation. Four concurrent state requests are
therefore insufficient evidence of a duplicate poll within one component;
multiple visible tabs remain a possible source.

The speed-load client already shares pending requests within a scoped request
key and supports independent cancellation. Load dynamics and speed-zone history
load their expensive reports on demand. The speed-zone history button lacks
unmount cancellation; its potential saving has not been measured and this
change does not modify it.

## Recovery

Revert the owner read-selection change to restore the previous five-read path.
There is no database migration or stored-state change to reverse. Training
prescriptions, approval requests and historical payloads are unchanged.
