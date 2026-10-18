# Whole microcycles and reservation reconciliation

Coach approval: 18 October 2026. The seven-day display must not divide an
otherwise legal individual dose solely because it ends partway through an
anchored microcycle. Unused hypothetical future tasks must not retain load.

## Planning scope

- `days`, `start_date`, `end_date`, and the session/time summary remain the
  visible seven-day draft. Approval and current-day locks still use `days`.
- With supported component history, allocation goals include the complete
  anchored microcycles touched by that draft. Execution before the visible
  start consumes its own cycle's Q/E budget. No borrowing between cycles.
- The engine runs chronologically through the final cycle's end, at most six
  days beyond the display. `lookahead_days` are explicitly provisional and
  are not published as approved daily tasks. The interface displays them in
  a separate expandable forecast and labels coverage as full-cycle coverage.
- Only actual programme boundaries truncate the integrated daily goals.
  Phase, taper, wave, calendar and manual target changes remain date-specific.
- The existing seven-day slot pattern repeats into the tail. Actual and
  proposed sessions, key sessions and explicit time limits are checked over
  rolling seven-day windows. Strength also keeps its anchored-cycle quota,
  spacing and building/maintenance limits.
- A candidate must fit both remaining cycle E and daily rolling E headroom,
  in addition to direct Q, complete method structure, role, individual Tmax,
  causal Recovery, daily availability and calendar restrictions.

## Reconciliation

Bounded search first replays the selected schedule using its actual future
method identities, sports and roles, including the provisional tail. It does
not reserve a different imaginary method when the selected method cannot be
reserved, nor create a task on a selected rest day. The next full chronological
pass can use the released budget in whole method units; every later session
is recalculated from the new preceding load. No block is enlarged in place.

At most two reconciliation trials precede the existing three allocation
variants, within the existing total search time allowance. A trial is kept
only when it improves the coverage score. Search remains bounded and never
claims global optimality, guaranteed 100% coverage, or a proven physiological
limit. Reservation evidence now includes every protected future method and
its direct Q, including key methods previously omitted from the diagnostic.

## Regression evidence

Private replay of revision 643 reproduces the v47 session dates, method IDs,
work durations and component Q exactly. Private athlete inputs are kept out
of this repository. Automated regressions cover whole-cycle bounds, earlier
execution, separate cycle budgets, provisional versus visible totals, rolling
session/time limits, future-method reconciliation, and forecast parsing/UI.

This supersedes the display-boundary proration described by the earlier
allocation audit. Method coefficients, Recovery formula, Q equivalence,
spill coefficients and the long-term progression trajectory are unchanged.
