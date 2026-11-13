# Staging cache and assistant read optimization — 13 November 2026

Scope: `codex/staging-cache-optimization-2026-11-13`, based on
`f30073005073ae1c565ad42256394c1bd4b2ac1b` from
`codex/build-end-to-end-cloud-integration`. Only onFlows staging is a deployment
target. Scientific formulae, Luna/provider configuration, financial caps and
authorization rules are unchanged.

## Observed baseline

All three staging services were live on the baseline commit. The worker logged
231 successful publications between 11:11:13 and 11:19:15 UTC after that deploy:
18 dashboard, 18 learning and 195 other page preparations. The database later
contained 151 current rows (6 dashboards, 6 learning views and 139 page variants).
Publication counts include repeated preparation of some rows; they are not 231
distinct athletes or views. Deploy SHA was an input to all three model keys.

Two controlled read-only questions were submitted through the signed-in staging
interface. Application logs, aligned to conversation completion times, showed:

| Question | API elapsed | Store requests | Store time | Store response bytes |
| --- | ---: | ---: | ---: | ---: |
| Actual Z1 hours, 1–31 October 2026 | 9,078 ms | 14 | 2,525 ms | 943,776 |
| Weekly program and profile settings | 14,971 ms | 20 | 4,383 ms | 2,118,923 |

The first answer was 39.47 hours with the existing data-quality qualification.
The second read 2,095,642 bytes for the plan pointer and fetched settings three
times. These are individual live observations, not a stable latency percentile.

## Changes

- Prepared dashboard, learning and page families identify their calculation
  dependencies, checked-in method catalogs, numerical/validation runtime and
  Recovery-v2 flag. Git commit identity is no longer an input. Source digests
  are shared and warmed once before API readiness; comments and docstrings do
  not invalidate calculations. Input revisions, local dates and publication
  fences remain authoritative in PostgreSQL.
- One private SQL comparison identifies meaningful input changes across all
  three families. Timestamp-only writes and sync queue sequence changes do not
  invalidate scientific results. Active generation/revision changes still do.
- Saved management pages carry their actual preparation timestamp, evaluation
  date and update reason, including when current. Scientific predictions remain
  unavailable until their input/day/model checks pass. The management page reads
  live sync progress separately from its prepared scientific payload.
- Automatic whole-page retries back off from 5 to 20 seconds: seven refreshes
  in a two-minute pending interval instead of 23. Manual retry remains available.
- A shared atomic plan-store schedule projection reads the current revision,
  visible sessions, candidate sessions and provenance without unrelated model
  snapshots. Weekly summaries no longer build detailed repetition blocks only
  to discard them. One conversational read phase reuses settings; editable
  inputs, access grants and command preconditions remain live.
- Nine unchanged presentation functions move out of the conversation
  orchestrator into `assistant_presentation.py`. This separates rendering from
  tool execution without changing prompts, budgets or supported operations.

## Reproducible comparison

`scripts/benchmark_assistant_reads.py` runs the same synthetic three-read
conversation against either checkout. It does not contact a database or LLM;
its transport emulates the selected PostgREST fields and JSON decoding. Both
runs used 20 measured repetitions after one warm-up.

| Measurement | Baseline | Updated |
| --- | ---: | ---: |
| Repository/transport calls | 17 | 14 |
| Settings reads | 4 | 1 |
| Live access checks | 9 | 9 |
| Transferred JSON bytes | 2,035,284 | 235,621 |
| Median Python CPU time | 134.602 ms | 23.182 ms |
| Median local elapsed time | 147.027 ms | 22.771 ms |

The exact result supplied to the scripted model is identical, SHA-256
`1c9fbc6285f986e76d555ad5fadaf583303629778e89b487074b1c0da753db1f`.
Bytes fall 88.4%; CPU time falls 82.8% in this fixture. Local elapsed time varies
with host contention and is not an end-to-end Luna latency measurement.

Example reproduction, using the same script for both revisions:

```sh
.venv/bin/python scripts/benchmark_assistant_reads.py --repo ../onflows-baseline --repeats 20
.venv/bin/python scripts/benchmark_assistant_reads.py --repo . --repeats 20
```

## Boundaries and maintenance

A local PostgreSQL/PGlite comparison used five independent synthetic writes per
scenario and rolled back the fixtures. Counts below are dirty events, not
completed scientific calculations:

| Five writes | Dashboard before → after | Learning before → after | Pages before → after |
| --- | ---: | ---: | ---: |
| Sync request sequence only | 0 → 0 | 5 → 0 | 5 → 0 |
| Identical settings | 0 → 0 | 0 → 0 | 5 → 0 |
| Settings timestamp only | 0 → 0 | 0 → 0 | 5 → 0 |
| Actual HRmax changes | 5 → 5 | 5 → 5 | 5 → 5 |

The dashboard already had a specialized activation trigger. Real source
changes continue to request recalculation in every domain. During a management
page refresh, the live sync panel defers whole-page reloads to the bounded
prepared-view retry schedule, avoiding an immediate reload loop.

The first transition from SHA-based to dependency-based identities requires a
refresh. Later unrelated chat/frontend/documentation releases retain the same
identities. Page families remain conservatively grouped: a shared calculation
or adapter change can refresh more than one page. Source-contract boundaries
and scientific non-Python assets must be reviewed when adding new readers;
tests cover prepared callback dependencies and current JSON catalogs.

Saved programs are explicitly dated references during refresh; they do not
authorize stale numerical predictions or bypass current command validation.
Live tests of conversation reads do not alter athlete training inputs. Settings,
data publication, write commands, revocation and stale-worker races are checked
with isolated regression fixtures. The live management page uses one extra
small sync-status read to keep progress truthful without recomputing science.
