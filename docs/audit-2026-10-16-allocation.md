# Allocation, strength exposure and pre-race readiness

Coach decision, 16 October 2026: include work and internal rests in the main
strength exposure, excluding warm-up/cool-down. Replace the pre-race 90%
Recovery veto with an advisory warning. Preserve the approved method-role
doses and the distinct direct-Q and canonical-E budgets.

## Implemented

- Remaining slots for shortage detection and dose balancing are scoped to
  the same long-term microcycle as the remaining target.
- Preparation reservations consider partially covered components in every
  microcycle, not only components with zero exposure or recovery weeks.
  Existing preferred training days retain their selection priority.
- Three bounded, complete engine replays compare optional Z1 finishes,
  dose balancing and separate versus combined methods. Search also runs
  when an accent is covered but a support component remains incomplete.
  All trials retain the same Q/E, Recovery, whole-unit, calendar and time gates.
- Race readiness is calculated for the selected schedule and surfaced as a
  warning below 90%, without vetoing or shrinking the prescription. A prior
  unmeasured race prevents pretending that a later race forecast is known.
- Strength exposure explicitly includes passive inter-set rests. The API
  and UI distinguish active exercise, pauses and total main strength time.
  Warm-up and cool-down are excluded from strength exposure.

## Strength shortfall diagnosis

The existing circuit already included transitions and inter-round recovery.
With nine 20-second exercises, one circuit contains 3 active minutes and
4 transition minutes. Three circuits authorize 9 active minutes. At the
approved maintenance fraction of 60%, the budget is 5.4 active minutes;
whole-circuit quantization permits one circuit, or 7 minutes of main exposure.
Two such sessions contribute 14 minutes, not 6. Counting pauses again would
inflate the load rather than solve the method-capacity mismatch.

This change does not invent extra circuits, reduce long-term targets, or
calibrate equivalence between maximum-strength and endurance methods.
Larger strength prescriptions require suitable method profiles within the
coach's session and dosing limits.

## Limits and validation

The long-term uniform-week projection remains an estimate. Actual composed
sessions use the canonical non-linear adjacent spill calculation, so the
projection is not a proof that every target has an executable prescription.
No arbitrary E tolerance or second target haircut is introduced. Unfilled
targets and structural strength bounds remain visible; bounded search never
claims global optimality or physiological impossibility.

Regression coverage includes cross-microcycle slot counting, passive strength
rests, the whole-circuit dose, identical prescriptions at 90% versus 70%
pre-race readiness, unknown intervening race loads, and filling support
components after accents are covered. Private replay inputs are not stored
in the repository.
