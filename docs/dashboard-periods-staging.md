# Dashboard periods and direct volume — staging

The load cards and detailed current-state cards show direct equivalent volume summed over the last seven calendar dates and the weekly equivalent of the last forty calendar dates. Both windows end at the loaded history's period_end, inclusive, and display their actual date ranges. They use activity equivalent_time_min from all sports; strength minutes stay separate. No E, cascade or Tref is substituted for volume. For short history, the weekly denominator is the available calendar span and the estimate is labeled preliminary. Missing daily zone groups suppress the affected volume rather than becoming rest days. Limited/excluded input coverage is disclosed.

Tref stays unchanged in the scientific model and API. Its existing values and expert bounds move into collapsed technical details. E7/E40 and 7/40 remain the existing modeled outputs, separately labeled. Strength activity counts explicitly cover the entire loaded period. The overview's duration card says calendar days and marks incomplete history/durations.

The details view separates the latest recorded training day (date, all modeled sessions that day, actual/equivalent zone totals and separate strength duration) from current recovery and rolling volume. Using a day avoids claiming within-day ordering that the aggregate contract does not supply. Recovery shows its calculation date, source date, daily resolution and the no-new-load assumption; stale sources get an explicit note. An unavailable activity date is never presented as today.

All duration fields in the completed-work report use hours:minutes:seconds, rounded once to the nearest second with unbounded hours. E remains numeric. Date filtering and report aggregates are unchanged.

Validation: regressions cover calendar boundaries, rest days, short/missing history, strength exclusion, same-day session aggregation, UI separation, and duration carry/values above 24 hours. Web suite, ESLint, TypeScript and production build; live staging inspection after deployment. API, worker, database and model calculations require no deployment or data rebuild.
