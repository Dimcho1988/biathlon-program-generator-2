# Planning feedback and constraint diagnosis (v5.1)

The reported near-empty week was reproduced from a read-only staging snapshot.
A manually entered Z1 target replaced the 7/40-derived weekly component goal.
Since canonical aerobic load cascades into Z1, this restricted higher-zone
methods as well as easy work. Readiness was high; it was not the blocking cause.

This release preserves explicit coach targets and all capacity, budget and
Recovery gates. It does not silently reset a saved target or activate a proposal.
The profile and weekly summary now show manual targets outside collapsed details,
with their units and an explicit action to restore automatic targets before saving.
Rejected methods report the blocking components, required minimum load and
remaining budget; affected rest days identify the manual target directly.

Successful profile PUTs in the investigated logs took 0.64–1.35 seconds end to end;
the database/API portion took 0.22–0.38 seconds. The subsequent page refresh took
about 3 seconds and remounted the form by profile revision, removing confirmation.
The editor now retains its current step and confirmation across its own save,
shows feedback beside the button, and remains keyed by athlete identity. Newer
external revisions reconcile only when the editor has no unsaved changes.
The configured planning page reads the calendar and management profile; obsolete
methodology and legacy profile requests are removed from its normal render.
Route invalidation and the background server refresh remain, so subsequent plan
navigation receives current inputs. Saving settings does not generate a plan.

The days editor displays the effective session limit from both weekly and daily
settings. A weekly maximum above the sum of daily allowances is still valid,
but its actual effect is visible. Extra session slots never multiply load budgets.

Validation includes a replay of the saved near-empty week and a local-only
counterfactual with automatic targets; DOM tests for save confirmation, external
revisions, athlete switching, explicit target reset and weekly warnings; API tests
for restrictive goals and preserved Recovery; and the existing calendar, lifecycle,
multi-session, interval-dose and canonical suites. Personal inputs and replay files
remain outside the repository. The cloud browser had no athlete session, so no
authenticated live UI save or personal programme approval was performed.
