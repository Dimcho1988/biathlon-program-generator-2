# Live outlook and volume basis — management v3.1

## Problems corrected

The long-term page previously displayed `long_term` from a saved seven-day
draft. Saving mesocycle accents or a different wave left that snapshot unchanged.
The new read-only `GET /api/v2/athlete/management/outlook` derives the outlook from
the current saved profile, calendar and actual load snapshot. Its profile
revision is visible. It neither creates sessions nor changes approved plans.

The weekly generator used selected-means historical minutes both as the whole
training budget and as a cumulative ceiling per means. This could reduce an
athlete with substantial mixed training to only the previous skiing hours.

## Budget versus individual dose

For profiles with planning controls, the 28-day whole-training mean supplies
the time budget. Strength time is excluded when strength is disabled. A saved
coach volume goal can replace this reference. Profiles without planning
controls retain their same-means reference.

The cycle wave, period, taper, unavailable/race days and saved daily availability
then constrain the time budget. Outlook and weekly generation use the same
ceiling calculation; partial weeks are clipped. These are available minutes,
not predicted prescribed minutes or measured time in zones.

Each session still uses the actual means' own speed-duration/Tref capacity,
method dose, canonical component budgets and LOAD_ONLY Recovery. A separate,
versioned coaching guard bounds total session time to the longest session
observed for that means in 28 days, multiplied by `max(1, cycle volume factor)`.
This replaces the erroneous cumulative weekly ceiling; it is a reviewable
heuristic, not an independently validated injury-risk threshold. Means without
recent exposure only admit recovery methods with the low absolute work limit.
Warm-up, repetitions, rests and cooldown all count toward the session limit.
No speed or capacity observation is transferred between means.

The UI distinguishes whole-training history, selected-means history, available
time, cycle budget and prescribed sessions. Availability below historical volume
is called out. The optional history button populates unsaved daily availability
using actual weekday means; confirmed rest days count as zero, missing days do
not. The user must review and save those values.

## Verification

- Saving different accents/wave changes live targets without a draft or write.
- Frozen plans and source history remain unchanged; protected plan access applies.
- Whole-history budgeting preserves strength separation and daily availability.
- Long cycling history cannot grant the same session duration or hard-work
  permission in a means without exposure.
- Method capacity, canonical component budgets and Recovery gates remain active.

Long-term 7/40 values remain goals against the current observed C40/B50, not a
simulation of unknown future execution. A higher goal never guarantees that
Recovery will permit a corresponding dose. Existing approved schedules require
review when versioned management rules change.
