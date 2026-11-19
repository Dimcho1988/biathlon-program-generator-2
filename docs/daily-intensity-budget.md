# Shared daily intensity budget

The planner checks one Z3–Z5 budget across all sports, sessions and parents of
combined methods. Previously, each maintenance parent could receive its own
60% dose, and maintenance work did not count as a key session. Several complete
maintenance methods could therefore accumulate on the same day despite passing
their individual method and weekly component checks.

This is a coaching policy, not a validated physiological maximum or an injury
prediction. Its limits are 0.60 full method doses on a maintenance day and 1.00
when the day's high-intensity work includes a developmental role.

## Accounting

- Each direct WORK block in Z3–Z5 spends its minutes divided by its parent's
  full developmental method dose. Source-library methods use their source
  standard; other methods use their existing individual capacity and method
  factors. Each parent of a combination retains its own denominator.
- Developmental denominators retain the existing Recovery adjustment and whole
  repetition allowance. The daily limit does not apply Recovery a second time.
- Only developmental **high-intensity** work raises the limit to 1.00. An easy
  developmental component cannot raise a maintenance quality budget.
- Imported execution without method linkage consumes direct Q divided by the
  sport/zone capacity times the developmental fraction. This is an explicitly
  conservative estimate, not a reconstruction of the performed method.
- Missing high-intensity dose evidence prevents additional high-intensity work.
  Existing actual work is never removed. Easy work remains subject to the usual
  Q/E, Recovery, duration and schedule checks.
- Warmup, recovery and Z1–Z2 do not spend this additional ledger. Their load and
  cross-zone effects remain in the existing canonical load checks.
- A coach-configured double-threshold day retains its existing independent
  session rules and reserves the whole quality budget. It cannot be followed by
  a third quality dose. A matched, imported first part continues through the
  existing double-threshold reconciliation and revalidation.

## Planning and existing plans

Candidates are reduced using complete method structures. If no complete dose
fits, that candidate is rejected with `DAILY_INTENSITY_BUDGET`. Subsequent dose
adjustments and speculative reservations use the same ledger. Nonbinding
single-session prescriptions are unchanged in the regression comparison.

Locked sessions with sufficient original nominal-dose evidence can be upgraded
without changing their blocks. Over-budget or unverifiable locked quality days
require review and cannot activate automatically.

Day diagnostics expose the used fraction, limit, imported fraction and any
explicit double-threshold exception. Parameter metadata labels the policy as a
coaching rule. The engine version change participates in the existing automatic
plan invalidation; it requires no schema migration or historic activity/model
recalculation.

Regression coverage includes the reported 3-session pattern (approximately 2.87
full doses), cross-sport accumulation, mixed-method roles, actual Q accounting,
double threshold, locked-plan revalidation and unchanged nonbinding days.
