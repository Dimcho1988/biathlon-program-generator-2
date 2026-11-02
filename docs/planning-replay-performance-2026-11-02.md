# Planning replay optimization, 2026-11-02

Repeated alternative schedules previously reconstructed the same historical
planning projection and discarded their exact Recovery calculations between
replays. Technical Tref references also rebuilt identical pandas Series for
many candidate doses. This change reuses those calculations within one job.

- A ReadSession memo retains the historical projection. Its key includes the
  athlete, date, complete non-scheduling profile, public settings, analysis and
  speed views. Only `_allocation_policy`, which does not enter reconstruction,
  is excluded. Each caller receives an independent deep copy.
- The existing bounded Recovery arithmetic caches can span the alternative
  schedules of one `build_draft` calculation. Dose, date and configuration keys
  and their existing size bounds are preserved. No cache spans jobs or users.
- Each generator has a 256-entry LRU for technical Tref. Keys retain the exact
  ordered dose vectors from the completed 40-day window and base parameters.
  Missing days remain distinct from explicit zeroes. Returned maps are copied.
- The reservation loop formats its placement date once per record rather than
  once for every historical row in its filters.

The authoritative generator, dose admission, causal Recovery checks, search
policies and 150-second search window are unchanged. The live input check and
fenced draft writer remain after calculation. No model version, schema,
service count, compute plan or activation behavior changes.

## Controlled comparison

Python 3.12.14 with the exact `requirements-api.txt` / `constraints-api.txt`
runtime. The baseline is `84636d0767c23925e698b02bb63464ab87c18744`; its API and
model source matches the then-live `65cd06f` release.

Three alternating before/after pairs each run the same three complete
generator calls in one captured read phase: initial schedule, quality-first
allocation and causal reservation refit. The clock and private audit inputs
are fixed at 2026-11-01 15:00 UTC. The offline adapter exercises persisted
context reuse without any network request or database write.

| Measurement | Before median | After median | Reduction |
| --- | ---: | ---: | ---: |
| Three generator calls, wall time | 19.551 s | 17.374 s | 11.14% |
| Three generator calls, CPU time | 19.550 s | 17.372 s | 11.14% |

All 18 generated JSON documents match their corresponding alternative exactly;
the captured source inputs are unchanged. The complete benchmark process
reached 298.36 MiB maximum RSS. This is one process containing both variants,
not an isolated before/after memory comparison or a cloud memory peak.

This benchmark excludes the outer wall-clock-limited search, HTTP acceptance,
queue delay and publication. Its percentage is not a measured reduction in
total live request latency or concurrent-user capacity. Raw timings and hashes
are in `benchmarks/planning-replay-2026-11-02.json`.

Reproduction with the private replay archive kept outside the repository:

```bash
python -m pip install -r requirements-api.txt
python scripts/benchmark_planning_replay_reuse.py \
  --fixtures /path/to/extracted/audit-replay \
  --baseline 84636d0767c23925e698b02bb63464ab87c18744 \
  --repeats 3 --output /tmp/planning-replay-comparison.json
```

## Regression verification

The selected backend regression suite passed 264 tests in 148.34 seconds.
The final replay-reuse suite passed all 10 tests in 4.21 seconds, including
the additional check that a changed, invalid ledger cannot reuse a valid
projection. These groups overlap and must not be added as independent totals.
Coverage includes complete plan equality, copy isolation, changed profile,
physiology, source, date and speed inputs; bounded Tref eviction; missing
history; dose arithmetic; causal reservations; background publication and
fresh final input checks. `git diff --check` passed.
