# Coherent planning inputs per read phase

Status: implementation and focused verification; database CI and deployed
acceptance remain separate requirements. No live database writes or coach
approval were performed for this change.

## Measured reason and scope

The deployed PR #174 READY REFRESH at 20:31:07 UTC on 19 October took 4,092 ms:
20 store HTTP calls, 3,522 ms store HTTP time and 4,500,371 decoded response
bytes. The ready-computation claim took 369 ms and result copying 18 ms.
Those are observations for one warm request, not SQL-only costs or an overall
performance guarantee. A normal matching state poll uses six reads and about
42 KB; its observed elapsed time is about 0.7 s.

The actual HTTP regression trace has three intentional fresh phases:

| Phase | Existing serial inputs | New read |
| --- | --- | --- |
| Initial context/calculation | Profile, settings, full analysis, models, responses: 5 | One FULL bundle |
| After calculation | Settings, full analysis, models, responses: 4 | One new FULL bundle |
| Final current response after publication | Profile, settings, full analysis, models, responses: 5 | One new FULL bundle |

Initial plan pointer, expected publication checkpoint, computation claim,
publication, final fresh pointer and compact revision history remain separate.
The warm HTTP fixture therefore changes 20→9 calls; OWNED adds COMPLETE and
changes 21→10. There is no reuse across the three phase boundaries. The full
analysis remains present in every FULL phase; this change mainly removes
round trips, not the large immutable analysis/plan payload transfers.

## Read contract

The additive GET RPC is `read_onflows_planning_inputs`, with alias, FULL/HINTED
mode and saved analysis hints. It is STABLE, SECURITY INVOKER, has an empty
search path and grants EXECUTE only to the service role. It reads the same
whitelisted profile/settings/model/response fields and existing
`active_onflows_analysis` projection inside one statement snapshot. It acquires
no new publication locks and exposes no credentials or headers. API token,
alias and actor authorization boundaries remain in their existing routes and
write RPCs.

The first database CI attempt exposed a test-baseline error: the September
accounts migration grants ALL on existing public tables to the service role;
the later read-contract migration restricts the generation table, not the
analysis-state table. This bundle does not change either table ACL. The contract
now verifies the existing state privilege mask is unchanged by the read and
retains the explicit ban on direct immutable-generation UPDATE. It does not
silently impose or claim a new SELECT-only state grant.

The private `planning-inputs-v1` object contains the requested alias, settings
and profile rows, latest model/response entries, separate raw-history overflow
flags and the analysis row/mode. The API validates its envelope and alias, then
retains existing field parsers lazily. Real RPC/DTO failures fail closed; they
do not fall back to several independent queries.

The existing `ModelStore.entries` and `ResponseStore.entries` contracts already
return latest identities. New hooks serve exactly these semantic results;
ordinary raw GET/history paths are untouched. Models use the largest revision
in kind/key order. Responses use the largest revision's value, retaining the
identity's first-occurrence position from the preceding global history sort:
MAX(recorded_at) across all revisions, then kind/key. Ordering by the chosen
revision's timestamp would be incorrect when recorded timestamps are unusual.

Both original readers reject exactly 10,000 raw revisions as well as larger
histories. Bounded raw counts preserve that failure through per-section flags;
the matching section raises its original bounded-history error only when
consumed. Latest-only counts cannot hide exhaustion. This does not remove
historical revisions or change their retention.

## Polling, legacy behavior and freshness

HINTED mode is used for state polling. The snapshot is omitted only when the
current immutable generation, positive pointer revision and as-of date exactly
match a valid saved hint. The same Python validation and saved digest remain
authoritative; SQL does not replace the Python canonical fingerprint with its
own hash. Hint UUID/date strings are compared without casts or normalization.
Malformed or unmatched hints and legacy analysis receive the full analysis
from the same RPC snapshot. A missing/unavailable analysis follows the original
reader, including its refusal to use legacy data behind a nonnull unavailable
generation pointer.

Matching polling changes six calls to two: pointer plus HINTED bundle. A
changed analysis also remains two calls, with FULL analysis in that same
bundle. There is no 448-KB matching-poll snapshot regression. The reader checks
that an omitted snapshot really matches its requested validated hint; it never
silently fills a corrupt metadata-only response with a fresh second analysis.

Nested page/context ReadSessions share only their explicitly captured phase.
A separate raw-repository session after calculation/publication captures fresh
data. Model/profile/response raw paths are not intercepted, and no global or
cross-request cache is added. Custom providers without the capability keep the
old path. The direct repository constructor has an explicit capability opt-in;
the production `from_environment` factory enables it. Deploy the additive SQL
before the application starts requesting it; a missing real RPC is a storage
failure, not an automatic legacy fallback.

The save/check core still locks and compares the live publication checkpoint,
including the monotonic analysis revision from the preceding fix. An input
change during computation conflicts, a profile change is rejected by its SQL
CAS, and the final current response may legitimately observe a change after
publication. The fresh final pointer/record cannot be replaced with the API's
candidate, because another action may have published meanwhile.

## Verification and acceptance

The HTTP suite compares complete canonical response JSON, computation key,
request counts and fingerprints for READY/OWNED paths, and exercises HINTED
matching/full fallback, all input classes changing during calculation, fresh
post-publication changes, malformed/foreign/error responses without fallback,
history overflow, ordering anomalies, raw-history availability, nested data
aliasing, athlete/phase separation and the production factory capability.
Metrics use only the fixed `GET:planning_inputs` label.

`tests/database/planning_inputs_bundle.sql` runs against migrated local
Supabase with synthetic fixtures and BEGIN/ROLLBACK. It checks service-only
ACL/security/schema/field whitelists, tenant separation, latest ordering,
9,999/10,000 raw-row boundaries, hints and active/legacy/missing semantics.
It is wired into database CI; local PostgreSQL/Docker are unavailable in this
worktree, so actual SQL execution must be recorded from CI before acceptance.

Remaining acceptance: exact full CI commit, deployment/migration identities,
repeated READY/OWNED/poll measurements, unchanged complete payloads and
historical prefix evidence, plus fresh authorization/UI checks. SQL aggregation
and serialization can consume part of the avoided network time; no deployed
latency improvement is claimed before those measurements.

## Recovery

Revert the application use of the capability to restore the preceding
independent reads. The additive read RPC may remain unused; removing it later
is optional and must preserve unrelated ACLs/functions. No history, approval,
generation, pin or payload rewrite is needed. Keep the publication freshness
fix and disabled pruning independent of this performance rollback.
