# Whole-method reservation and execution consistency

The planner previously protected a future **date** while permitting its reserved
method to change during an earlier dose trial. A Z4/Z5 combination could silently
be replaced with a cheaper Z3 minimum. Earlier easy work then spent the released
Z1 and the final high-zone methods no longer fitted.

Engine v67 uses one candidate catalogue for current selection, key-date
eligibility and future reservations. Proven slot shortages enable the same
combinations in all three paths. Background component roles are preserved in
both parts. A causal reservation protects the chosen method, sport and complete
minimum preparation, and cannot silently substitute a different quality. Each
day records `quality_reservations` for auditing these decisions.

Mandatory warmup, recovery and cooldown for a mixed core may use the same shared
preparation reserve as another method. Actual Z1 WORK cannot use that exception.
Optional easy finishes still use only the remaining core objective. The same
rule checks approved locked sessions. No reserve ceiling or physiological
limit is increased by this change.

The bounded repair search now also inspects future Z1 budget rejections when a
quality is absent altogether, rather than requiring an already selected session
in that quality. Repairs retain complete minima and replay the full engine.

## Execution roles

Planned and explicitly confirmed comparable execution use one key-role rule.
A reduced prescription freezes its actual role separately from the catalogue
template. Standalone confirmation can retain that role only when date, sport,
method, recipe version and planned complete duration identify one approved
session. Ambiguity never establishes a plan/activity match.

Only successful comparable execution within 5% of the planned duration and,
when reported, speed can use the confirmed role. An exceeded or unknown dose retains the
conservative five-minute high-zone exposure fallback. A confirmed easy part
cannot hide unlabelled hard intervals grouped into the same occasion. This
fallback is labelled in `recorded_session_policy.key_classification`; it is not
presented as a known developing method. Scientific load remains unchanged.

## Checks and acceptance

- `test_weekly_quality_contracts.py`: a future selected combination participates
  in preparation reservation before earlier easy work.
- `test_training_sessions.py`: the same confirmed dose retains its key
  classification across Z2–Z5 and all five roles; unknown/exceeded doses and
  mixed confirmed/unlabelled occasions remain conservative.
- `test_learning_methods.py`: resolved roles survive only one matching complete
  approved dose; changed duration, recipe or ambiguous matches do not qualify.
- `test_management_q_reporting.py`: an entirely rejected quality can trigger
  preparation repair without borrowing from other cycles or unknown history.
- Existing API suites check Recovery, whole repetitions, rolling Q/E, actual
  sport exposure, days/slots, locked work and HR/speed source interchangeability.

A green regression suite does not establish 100% coverage for every profile.
Check each anchored microcycle and component separately, retain the original
target and report remaining gaps. Exhausting bounded search is not an
infeasibility proof. No athlete-specific settings, approval or zone targets are
changed to manufacture a successful allocation.
