# Race-duration and planning diagnostics

The planner resolves approximate race duration from the saved discipline and the
speed–duration curve for `profile.sport` (the race sport, not `actual_sport`). One
unambiguous distance is required; relay and multiple-distance descriptions use
the manual fallback. Marathon and half-marathon names are supported.

Only a calibrated curve with individual, non-exploratory tests is eligible.
Log interpolation uses the curve's existing distance/time points, including its
existing volume correction, once. No extrapolation beyond the curve domain is
performed. This is approximate moving time; course, weather and shooting are not
predicted. This does not claim validation of race performance.

`race_duration_min` remains the user's manual fallback. Derived duration and
source evidence live in draft parameters and the current outlook. Generation
and outlook use the same resolver. Source-generation mismatches block a draft;
the read-only outlook falls back explicitly. Model recalculation never overwrites
the user's saved fallback. The profile offers a read-only preview; generation and
outlook resolve the duration automatically regardless of using the preview.

A symptom hold now displays the date of the last recorded DAILY report and links
to the morning assessment. Clearing the flag requires saving the edited or newer
report, then refreshing the plan. No age-based auto-clear was introduced. Recovery
and the observed-stress model remain separate. The response page exposes latest
report context even when the visible history window does not include that day.

Calendar validation now identifies blocks before the program start, after its
end, or overlapping another block/recovery period. It does not move or delete
blocks silently. A historical program start remains supported. The existing
stress-week and recovery-length limits are unchanged.

Engine and parameter versions advance to v8 so active plans detect the rule
change. No schema migration, source-history rewrite, or microcycle accent-rotation
policy change is included.
