# Technical optimization rollout, 20 October 2026

PR #176 continues the existing onFlows optimization; training methodology and
coach approval are outside this change.

## Database evidence

Four reviewed pending migrations were applied with exact source hashes and
atomic migration-ledger records. Five repository filenames now match the actual
recorded versions, including the earlier historical-reference bootstrap.
All 918 application catalog identities matched the successful clean Database CI
artifact. The guarded baseline acknowledgement then added twelve missing ledger
entries with NULL statements; no historical SQL was replayed or existing ledger
entry rewritten. The ledger contained 42 entries after this reconciliation.

The complete retained technical census inspected 669 shadow results, decoded
1,741 checksummed LZMA envelopes, and scanned 2,764 canonical results and 470
model inputs. No unresolved opaque payloads or additional nested references were
found. Final history delta through plan revision 662 and 40 management entries
added only already-protected roots. Four existing aliases received owner
coverage evidence. One pre-existing missing legacy run remains explicitly
recorded; this rollout does not invent or claim recovery of that source.

## Recovery acceptance and maintenance timeout

A rolled-back preview selected ten orphan shadow analyses and their ten compact
summaries, with no generation, plan, model, input, catalog or pointer changes.
The exact same 20-row scope was then committed with 2,306,945 logical recovery
bytes and a fourteen-day recovery window. A separate committed restore returned
all twenty records; their complete JSONB rows matched the original preimages.

The measured cleanup SQL execution was 18,464.713 ms. EXPLAIN isolated about
15.85 seconds in orphan candidate selection, principally conservative archive
marker checks over old payloads. The existing 8-second PostgREST inherited limit
and 15-second HTTP default cannot accommodate that live workload. The follow-up
migration scopes a 30-second SQL timeout to the service-only fixed maintenance
RPC; the client allows 35 seconds, including a network margin. Ordinary requests
retain their original timeouts. Ambiguous POST failures are still never retried.
The fixed policy, historical pins, archive exclusions, candidate and recovery
budgets, CAS restore checks, and all role grants remain unchanged.

This does not promise immediate physical disk shrinkage: recovery temporarily
retains preimages, and PostgreSQL reuses space after its normal vacuum lifecycle.
Deployment identifiers, final cleanup totals, history fingerprints and before/
after request measurements are recorded in PR #176 after execution.
