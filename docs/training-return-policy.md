# Linear return to training

Version: `linear-training-return-v1` / `training-management-v42-linear-return`.
These are explicit coaching parameters, not an experimentally validated
universal recovery timetable. No physiological measurement is inferred from Q.

## Policy

`training_return` is optional. `CONFIRMED_BREAK` requires the first inactive
date and first resumed training date. `CONTINUED` identifies missing records
without declaring rest. Existing profiles remain readable.

For each aerobic zone, let L be the lower expert reference and H the sustained
pre-break weekly direct equivalent time Q (excluding spill):

- Return target T = H for pauses up to 60 days, otherwise T = 0.90 H.
- Initial Q = min(T, max(0.90 L, 0.50 H)). A low or zero personal history is
  never raised beyond its return target.
- If H is unavailable, both initial Q and T use 0.90 L, labelled EXPERT_START.
- Nominal duration = 1.3 × min(pause days, 60).
- N = max(2, ceil(nominal duration / 7)) full observation weeks. Maximum N = 12.
- Stage k = initial Q + (T − initial Q) × k / (N − 1), k = 0…N−1.
- A completed, covered week advances each component by at most one stage when
  its executed Q reaches that stage (0.5 Q-minute ledger rounding tolerance).
  Unknown or unexecuted weeks accrue no progress. The final stage must also
  be executed before ordinary growth starts.
- Future stages are conditional forecasts. They are never saved as execution.

Historical evidence priority: explicitly declared Q; median of up to three
complete pre-break mesocycles; complete pre-break 40-day window; a retained
pre-break reference; expert start. A declared history is labelled separately
and does not manufacture activities. Recorded source Q is not rewritten.

The episode is stored with the existing preparation anchor in draft/active
plan revisions. Read-only outlooks do not persist it. Verified completed
weeks survive source retention, while corrections in available recorded
windows are replayed and can remove progress. Editing the declaration or
explicitly revising the reference creates a new episode.

## Integration and limits

The profile editor exposes dates and optional Q in hours/minutes/seconds.
The outlook shows historical Q, initial Q, return target and executed stages.
The same source policy serves the outlook and weekly generator.

A confirmed full pause supplies missing zero-load days only within its dates,
in a transient planning source. Recorded exercise conflicts block generation;
unknown days after resumption remain unknown. The measured ledger and learning
inputs are unchanged. A future resume date blocks current prescriptions.

The linear target does not also receive the automatic mesocycle wave or
calendar annual growth. Explicit coach cycles/component goals, time limits,
canonical E with spill, the existing 7/40 ceiling, Recovery, health holds,
sport-specific capacity, method budgets and taper remain operative. Thus the
feasible or executed dose can be below the desired curve and extend the return.

After return, ordinary growth starts from T (including the 90% target after a
long pause); there is no immediate jump back to 100% and no missed-time catch-up.
The annual policy decreases linearly from the configured low-volume rate
(default 30%) to 0% between these weekly Q references:

| Zone | Lower, hh:mm | Upper, hh:mm |
| --- | --- | --- |
| Z1 | 04:00 | 15:00 |
| Z2 | 01:00 | 05:00 |
| Z3 | 00:30 | 02:00 |
| Z4 | 00:10 | 00:50 |
| Z5 | 00:10 | 00:50 |

The old upper-rate/130% JSON fields remain accepted for compatibility and
normalize to 0%/100%. Observed personal history above the range is retained.
Strength and the existing individual-learning policy are unchanged.

Behavioral coverage is in `tests/api/test_training_return.py`; existing growth,
planning, health and allocation tests exercise the shared gates. Frontend
declaration validation and explanation coverage is in
`apps/web/test/load-progression.test.tsx`.
