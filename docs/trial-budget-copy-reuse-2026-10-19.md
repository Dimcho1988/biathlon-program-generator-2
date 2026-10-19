# Trial budget copy reuse — 2026-10-19

Status: implemented locally, focused tests passed; full-suite and deployment
acceptance are tracked separately below. Base: `3bb8da6c1934d5af51177173f74bb7c2d31f8094`.

The planner copied complete method cards before each candidate and future
placement trial. A private offline replay recorded 2,051 such method-copy
calls in one standalone `generate_plan` computation. A cold cProfile run spent
2.504 s in 2,046 direct `deepcopy` calls from the generator and another 0.185 s
in 168 calls from its speculative reservation checker. These instrumented
numbers include unrelated evidence copies and are not wall-clock savings.

The change separates the dictionaries that trial budget calculations can
change, and reuses read-only source recipes, guidance, resolved curves and
method cards. Combined methods retain independent child method dictionaries;
speculative capacities retain independent combined child capacities and
readiness-policy dictionaries. Native dose preparation already replaces its
interval/dosing dictionaries instead of changing the existing ones. Published
method cards and frozen choice-check capacity evidence retain their full
copies.

No training formula, method availability, search iteration count, method order,
training history, persistence contract or cross-request cache changes. Future
code that changes nested source metadata during a trial must first extend the
copy boundary. The full-copy comparison tests intentionally protect that
boundary.

## Evidence

Twenty-three new focused tests compare the complete outputs with the preceding
full-copy behavior. They exercise building/maintenance prescriptions for
native intervals before/after budget preparation, continuous work, source
library recipes, strength, combined and nested combined methods, plus nine
complete generator scenarios, including actually selected native and combined
methods. Published native/combined session dictionaries and lists are mutated
to verify retained method sources stay independent. The tests also compare
input profiles, historical envelopes and complete plan JSON.

An initial balanced eight-run replay (previous/reused/reused/previous, repeated)
produced identical complete JSON in every sample: 849,153 bytes,
SHA-256 `ced58b299a0633b39a8645373ed248fbc032fa70446fd2a112b06454adc5b5bc`.

| Previous wall seconds | Reused wall seconds |
| ---: | ---: |
| 5.845 | 4.559 |
| 5.854 | 5.294 |
| 4.750 | 4.239 |
| 6.013 | 4.531 |
| **Median 5.850** | **Median 4.545** |

That is a 22.3% lower median standalone generator wall time in this replay.
Concurrent work and CPU scheduling affect local wall times. The portable
benchmark additionally reports process CPU time to make that limit visible.
A second balanced four-run check using the portable benchmark produced the same
JSON hash in all samples: wall medians 4.192 s → 3.478 s (17.0% lower),
process CPU medians 4.192 s → 3.477 s (17.0% lower). These are two local
measurements, not a promised production improvement.
A reservation-copy-only experiment had no reliable wall-time gain and was
insufficient by itself; the measured change includes the other four method
trial boundaries.

The replay uses private profile, speed-test and measured-load fixtures. It
omits historical TI, response and management observations, and does not execute
the outer improvement search. It is neither a complete live REFRESH replay
nor a measured reduction of the previously observed live request latency.
Private inputs and generated training JSON are kept outside git.

Reproduce offline with API dependencies installed:

```bash
PYTHONPATH=. python scripts/benchmark_trial_budget_reuse.py \
  --replay /private/path/audit-replay.json \
  --bundle /private/path/audit-bundle.json \
  --now 2026-10-19T09:00:00+00:00 --pairs 4
```

The benchmark rejects unexpected repository writes, compares the complete
serialized JSON, checks input immutability and emits aggregate timing/hash
metadata only.

## Validation and recovery

- Focused equivalence/isolation tests: 23 passed (26.61 s).
- Canonical suite: 786 passed (177.81 s).
- Full local API suite: interrupted for integration into the locked runtime;
  no failure visible through 68% progress. This is not a full API pass.
- Local environment: Python 3.12.14, numpy 2.3.5, pandas 2.2.3,
  pydantic 2.14.0, FastAPI 0.146.0, pytest 9.1.1.
- CI using locked runtime dependencies: pending integration.
- Live API/worker latency and proposal comparison: not yet checked.

Rollback is a code revert restoring the five full-copy trial boundaries and
removing the two helpers. No database or stored-history recovery is needed;
this change does not write or rewrite existing data. New proposal computation
continues to require the normal coach review flow.
