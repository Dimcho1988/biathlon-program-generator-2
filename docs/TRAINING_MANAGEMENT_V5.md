# Training management v5 — executable planning controls

This change connects calendar edits and session preferences to both planning views. It does not change actual activity records, canonical equivalence, Tref, the speed-duration model, 7/40 mathematics or LOAD_ONLY Recovery. Wellness stays diagnostic.

## Calendar and revisions

`horizon_mode=AUTO_CALENDAR` resolves the end to the last main race plus the selected transition days, bounded by one year from the programme start. With no main race it uses the entered end. `MANUAL` preserves the entered end and reports a main race outside that horizon. The requested and effective ends are retained in the draft and live outlook. Moving a race changes periodization and taper windows, while stored draft snapshots remain immutable.

Successful profile and calendar writes invalidate the profile, weekly and outlook routes. The weekly page keys its state by the current input fingerprint. Opening an obsolete draft prepares a replacement; an obsolete active plan invokes the existing lifecycle refresh. Changed rules or profile revisions still require review. Paused and completed programmes are not restarted.

## Sessions and threshold work

The profile accepts 1–21 weekly sessions and automatic or explicit 0–3 daily maxima. These are scheduling ceilings, not training quotas. Explicit double-threshold days reserve two slots; other preferred training weekdays reserve their first slot before optional days. Session availability and time availability are separate: the entered daily minutes remain the total for the day.

Each calendar day has `sessions`; `session` retains the first session for compatibility with historical snapshots. Summary, reconciliation, change detection and the UI consume all sessions. Rest/skip actions explicitly apply to the entire day. Imported activity days retain their actual load; the daily model cannot infer a remaining afternoon allowance after a partial-day import.

A double threshold is selected as one atomic candidate: one total threshold work budget split between two sessions, each with its own warm-up, rests and cooldown. Both count as key sessions. Z3 never gains the metabolic interval multiplier. Z4 is eligible only with an individual interval profile explicitly marked `goal=THRESHOLD`; an aerobic-power profile cannot masquerade as threshold work. Age, experience, period, capacity, whole repetitions, exposure, 7/40 and readiness remain gates.

Recovery has calendar-day resolution. A day bundle uses day-start readiness; each session's nonlinear canonical load is calculated separately, then summed into one daily impulse. Subsequent days use the accumulated forecast. The UI explicitly does not claim an independent afternoon readiness prediction. Additional ordinary sessions receive maintenance dosing, and no session multiplies the weekly component budget.

## Metabolic interval implementation profiles

These are versioned onFlows coaching defaults, not validated scientific norms or automatically approved Norwegian source variants. They parameterize the existing END-VO2-TREF-01 parent card's long and short metabolic methods. They are independently identified as `onflows-metabolic-intervals-v2` and require calibration over time.

| Profile | Work and active Z1 rest | Repetitions | Total-work capacity ceiling | Reserve |
| --- | --- | --- | --- | --- |
| Z4 long | 180 s / 180 s | 3–6 | 1.2 × continuous capacity at the selected effort | 2 quality repetitions |
| Z5 short metabolic | 30 s / 30 s | 6–20 | 1.5 × separately supported Z5 continuous capacity | 2 quality repetitions |

Each row is a complete profile, not a universal multiplier for its zone. Maintenance uses its minimum complete variant. Work must fit the profile, whole repetitions and rests, individual exposure, available time, all canonical component deficits and forecast consequences. The Z1–Z3 role percentage is not applied again. No repetition may reach continuous exhaustion duration. An individual coach profile overrides the corresponding automatic profile and exposes its own capacity, work, rests, count, total ratio and reserve.

Z4 can use the supported speed-duration estimate or the explicit expert fallback. Automatic Z5 still needs a recent strict maximal test above the supported Z4 boundary; it does not reuse the Z4 Tref. Strength and short neuromuscular sprints do not inherit the interval exception.

## Regression coverage

- Moving a main race changes both the weekly periodization and the live outlook; manual horizons stay fixed.
- Synthetic generous-history cases generate 12, 16 and 21 real sessions with daily and component budgets enforced.
- Double threshold splits one work dose, counts both sessions, preserves separate canonical spill and is rejected when readiness is insufficient.
- Second-session edits appear in plan-versus-execution totals and lifecycle change reports.
- DOM interactions save from step two, retain a manual horizon and replace stale drafts without exposing obsolete programme menus.
