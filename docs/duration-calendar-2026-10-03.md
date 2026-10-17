# Consistent duration display and newest-first activity calendar

All duration outputs share hours:minutes:seconds, including completed activity metrics, interval and zone times, calendar cards/week totals/sleep, direct and equivalent volume Q, training summaries, curve/test durations, model diagnostics, and volume chart ticks. The formatter rounds once to seconds, carries across minutes/hours, supports totals beyond 24 hours, and distinguishes unknown/invalid values from zero. Manual maximal tests retain subsecond precision in `h:mm:ss,fff`; existing minute:second inputs remain accepted.

The activity calendar starts with the latest week. Mobile days run newest first and omit padding days outside the selected period. Desktop columns retain Monday–Sunday through CSS ordering. Sessions within a day run latest first regardless of API input ordering, without mutating the source snapshot. Period navigation and scientific aggregation are unchanged.

Effective load E remains numerical, rates such as pace remain min:sec/km, and numeric configuration inputs retain their stated minute/hour/second units. Dates, day-based recovery horizons and model parameters keep their existing semantics. No stored data, backend/scientific algorithms, authentication or database schema changed.

Validation: 427 web tests passed, including duration carry/invalid/zero/long totals, fractional test parsing, chronological calendar grouping and same-day sorting. TypeScript, lint and production build passed. Existing assertions were updated to the requested display format.

Target: the established integration branch and Render web staging service only.
