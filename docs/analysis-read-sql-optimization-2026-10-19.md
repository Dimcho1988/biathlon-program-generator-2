# Captured analysis reuse and overview SQL work

This narrow follow-up to PR #165 removes a second full analysis read inside the
speed calendar and repeated evaluation of the SQL presentation's selected plan.
No training rules, engine version, plan approvals, evidence format or history
change. The previous batch is live at `92070db` and has measured overview API
medians of 5,861 to 4,094 ms in three sequential samples per version. This is a
small API observation, not a browser-paint or controlled capacity benchmark.

## One captured analysis per read phase

The request-local `ReadSession` already caches `active_analysis(alias)`, but
`active_trainability_calendar` was bound to the raw repository. Its internal
`self.active_analysis(alias)` bypassed that cache. Actual HTTP-adapter traces
showed two POSTs to the same RPC with identical alias parameters and identical
complete response bytes. Live diagnostics measured 896,662 bytes for these two
responses. Sync-state reads were not the cause.

The ordinary calendar reader now delegates to a reader that can also accept an
already captured analysis. Its original pagination and validation body is
unchanged. `ReadSession` uses this optional capability only when its own cache
already contains the exact athlete's valid immutable generation, positive
integer revision and snapshot object. It passes a defensive copy. It does not
prime the cache with an extra request or retain data between phases.

Old snapshots, uncaptured phases and repositories without the capability keep
the original path. Generation metadata, athlete filters, count/pagination,
activity identity and date checks stay in place. If a newer generation activates
mid-request, the captured generation supplies a coherent view; the next phase
reads the newer generation. A missing or corrupt captured catalog fails without
switching silently to another generation.

Real HTTP-adapter tests compare the entire response: full overview 18 to 17
requests, compact overview 17 to 16. The two analysis responses become one and
their decoded byte count halves exactly. Tests also cover aliases, dates,
nested sessions, mutation isolation, legacy fallback and generation races.
117 focused tests passed; the three affected overview/capture suites then
passed 73 tests after an additional byte assertion. Those runs overlap. There
are 25 new cases. Independent review found no blocker. Full CI and live
acceptance are required before claiming deployed savings.

## Evaluate the selected SQL value once

The overview projection's `pinned` CTE was eligible for inlining. Reusing the
selected JSON expression throughout the output caused repeat work even though
only one revision is selected. The replacement adds `MATERIALIZED` to that CTE.
All output expressions, alias/pointer joins, required-field guards, ordering,
STABLE/SECURITY INVOKER settings and service-role-only grants stay identical.
The existing transactional database suite exercises the replaced function after
all migrations, including malformed fields and unchanged immutable evidence.

Read-only staging probes compared both JSONB equality and exact JSON text for
the current revision (10,559 bytes). Two executions of the preceding function
took 160.297 and 139.615 ms with 2,128 shared-buffer hits. The materialized SELECT
candidate took 75.983 and 74.636 ms with 901 hits, no physical reads and no
temporary reads/writes. These are separate SQL probes, not the API wall time;
the deployed replacement function must be measured again after rollout.

A more elaborate flattened-payload candidate reached 28.202 and 22.946 ms on
this record, but adds a whole JSON copy and another projection layer. One
variant spilled 393 temporary blocks; the slimmer variant avoided that spill
for the current record and 2,184 kB work_mem. That does not establish its memory
behavior near the 4 MiB envelope limit. This batch uses the simpler materialized
capture. It does not introduce that extra full-payload copy or raise work_mem.
Larger selected plans could still require separate memory/spill investigation.

## Rollout and recovery

Run full API, canonical, web and database CI. Apply the materialization-only
function replacement, then deploy the same tested commit to API and worker.
The current web contract is unchanged, so it needs no redeployment for this
follow-up. Verify one analysis read, 16 compact overview calls, exact UI values,
unchanged review state and history, plus the actual function execution time.
Do not create another full plan revision solely to measure a read optimization.

Recovery is API/worker redeployment of the preceding version. The additive
calendar reader is internal and optional; older code retains its old path.
The materialized read function can remain because its contract is identical.
If a SQL issue is found, restore the prior function body in a forward migration.
No plan data restoration, destructive down-migration or history deletion is
needed. Persisted-session deduplication and lightweight no-change audit events
remain separate proposals documented in PR #165 and are not implemented here.
