# Reusing the initial read phase during plan refresh

## Implemented scope

The lifecycle and the draft builder now share one initial `ReadSession` when
they use the same immutable analysis generation and positive revision. This
removes a second read of the profile, settings, full analysis, model entries and
response entries. The existing per-argument cache keys include the athlete;
the phase also has to belong to the same original repository and use the
planning-settings bundle configuration.

The reconciliation envelope must identify that same generation and revision.
If it differs, or the source is a mutable legacy snapshot, the builder retains
its previous fresh initial reads. This also preserves the previous behavior
when analysis activation and rollback occur between phases. No global cache,
time-to-live setting, new persisted data or separate input DTO is introduced.

Only the initial/calculation read phase is shared. The computation RPC still
uses the original live repository. The input comparison after calculation starts
a new read session; the SQL publication checkpoint and profile-revision check
remain unchanged. The action response still reads current state afresh. The
same boundary applies to manual refresh, day decisions, resume and actual
automatic adaptation. Existing worker early returns remain unchanged.

The builder continues to validate the original stored profile and use it in
the computation key and frozen input snapshot. The calendar-resolved profile
returned by the lifecycle is not substituted for it. Decisions and the locked
day remain explicit computation-key inputs. Standalone draft generation has
no supplied initial phase and retains its original behavior.

## Concurrent edits and compatibility

A profile edit after initial capture may now be rejected later, by the existing
SQL profile-revision comparison, rather than by the removed second profile
read. It cannot publish a stale profile but can spend time computing before the
conflict is reported. Fresh post-calculation reads still detect changes to
physiology, timezone, calendar, preferences, models, responses and analysis.

An initial read phase is private implementation state. Public HTTP request and
response schemas, database functions and immutable plan formats are unchanged.
There is no migration, dependency, engine-version, dosing or Recovery change.
The small unchanged-check events from PR #167 and their history reader retain
their existing contracts; no plan approval is part of verification.

## Evidence and measurement boundary

The preceding staging baseline is merge commit `837dc86` (PR #167). Two warm
manual refreshes took 5,289 and 4,808 ms, each with 28 store calls and 7,575,900
or 7,575,901 decoded store response bytes. The active full revision was 656,
still REVIEW_REQUIRED, with two unchanged-check events.

In the HTTP-adapter fixture the five duplicate responses total 58,753 bytes:
1,349 profile, 864 settings, 56,536 analysis, two model bytes and two response
bytes. Live baseline resource totals imply 489,980 bytes for the corresponding
five responses with current staging inputs. That live amount is derived from
repeated identical response sizes, not a new per-request wire measurement.

The expected comparable warm count is 23 calls. Actual staging counts, bytes and
latencies must be recorded after rollout; sparse observations cannot establish
a general speedup. Decoded HTTP response bytes are different from compressed
network traffic and database storage. The existing full current-state reads,
calculation and final input checks remain costs of the request.

Tests compare the complete HTTP result and computation key with initial-phase
reuse disabled, exercise fresh-read and profile-publication races, check raw
versus resolved profiles, and retain legacy/mismatched-analysis fallbacks.
Complete CI and live results are recorded in the associated pull request.

## Rollout and recovery

After the complete CI passes, deploy the tested commit to API and worker and
verify their actual live commits. The frontend source tree and database are
unchanged, so the already deployed compatible web needs no rebuild or migration.
Verify two real manual checks, unchanged revision/hash/review state, visible
audit history, fewer store calls and preserved long-term overview output.

Recovery is a revert of this initial-phase reuse and API/worker redeployment.
No database rollback or history restoration is necessary. Keep the existing
unchanged-check storage and reader from PR #167. Historical plan revisions,
training data, methodology and coach decisions are not rewritten.
