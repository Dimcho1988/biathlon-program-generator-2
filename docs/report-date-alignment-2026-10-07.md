# Dashboard HR/speed analysis-date alignment

## Problem and cause

The dashboard could compare a persisted HR analysis ending on 2026-10-06
with a speed report ending on the current athlete-local date, 2026-10-07.
`LoadDynamics` omitted dates from its speed request. Forced speed refresh
repeated the same implicit-current-day request, so the comparison guard
correctly continued to block the two different rolling windows.

A full sync advances the persisted analysis date only after its asynchronous
job activates successfully. Speed refresh does not activate an HR snapshot.
The screenshot alone cannot identify whether that full job was still pending,
failed, coalesced, or whether the displayed page retained an earlier snapshot.

## Fix

- The dashboard requests speed with `as_of=history.period_end`.
- The API treats `as_of` as the reference date for the existing default
  40-day speed report, preserving its observed-history denominator, warmup,
  causal calibration and scientific formulas.
- This option is mutually exclusive with custom report dates. Future dates,
  invalid dates and dates that underflow the warmup are rejected.
- Snapshot dates participate in the client cache key and response validation.
  A changed HR period remounts the view and aborts the old request, even when
  generation and revision are unchanged.
- Existing generation/revision, account/athlete and access guards remain.
  Standalone speed requests and explicitly selected report periods keep their
  previous behavior.

Using a custom 40-day report period for this repair would pad short speed
histories with preceding zero days and change E7/E40 denominators. The new
analysis-date option deliberately follows the previous default calculation.

## Validation before rollout

All regression inputs are synthetic; real athlete data are not committed.

- The Oct7/Oct6 dashboard regression fails with the original component
  (`as_of` absent) and passes with the fix, including both chart sources.
- 445 web tests pass. ESLint, TypeScript and the production build pass.
- The full local API suite passes 1,131 tests. After adding the warmup
  underflow case, the affected speed-load file passes all 27 tests.
- API regressions compare entire reports with exact equality for short and
  long histories and for yesterday's default calculation. They also verify
  exclusion of later activities and athlete-local date boundaries.
- UI/client regressions cover forced refresh, reload/304, date-key isolation,
  stale request cancellation, same-generation period changes, unavailable
  data and absent HR history. Existing concurrent-request and access tests
  remain part of the full suites.
- Independent review found no blocker. Deploy API before web because older
  API versions ignore the new query option; the new client safely rejects
  an incorrectly dated result.

No database migration, historic-data change, model-version change, paid-plan
change or deletion is required. This repair does not establish application
capacity or change speed coverage.
