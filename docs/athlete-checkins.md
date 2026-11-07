# Athlete check-ins

The response page puts the five anchored morning questions first: sleep quality,
fatigue, soreness, psychological stress and motivation to train. Competition
motivation, manual sleep duration and notes remain optional. Existing scores keep
their direction (1 is favourable, 5 is unfavourable) and scale version.

The session form separates perceived effort (RPE, 0–10) from feeling during the
activity (1 very good/strong, 5 very bad/weak). Compatible Intervals `icu_rpe` and
`feel` answers are retained in the catalog, separately from computed load.
An onFlows answer overrides only that field; a later import does not replace it.
Elapsed activity time supplies an editable whole-session duration. RPE × minutes
is descriptive session load; it is never added to Q. Imported answers with unknown
observation timing cannot create an RPE comparison baseline.

## Shared input contract

Both the form and a future chat use the existing authenticated daily/session write
endpoints and revision checks. `input_method` is `FORM` or `CHAT`; the chat is not
implemented by this change. A conversational client must ask the same anchored
questions, obtain explicit answers and confirm ambiguous free text before writing.
It must not infer scores, dates, diagnoses or recovery from missing answers.

Session `rpe_source` and `feel_source` are independently `ONFLOWS` or `INTERVALS`.
Imported values must still match the pinned activity when saved; otherwise the
server rejects the write. Native RPE requires an observation timing. Self-reports
remain athlete-only; coach observations cannot impersonate athlete answers.

## Evidence and missing observations

The learning view distinguishes no measurements, measurements awaiting a personal
baseline, a single evaluable indicator, partial and sufficient assessments. One
indicator remains identified but never becomes a total stress score or a learning
response. Two available indicators can supply a labelled partial assessment; a
sufficient assessment retains the existing 40% nominal coverage/two-group rule.
Changing the displayed indicator panel breaks the chart path. Recovery uses the
same frozen indicators and references throughout a block, with at least two
indicators. It is not inferred from a changing display panel.

A block also needs at least three observed loading-phase days; before/after
measurements alone cannot establish tolerance. A delayed response requires consecutive elevated observations after the policy
deadline. Unobserved follow-up is labelled insufficient, never delayed. Current
decisions require two consecutive observations comparable with the block. Today's
independent observations can be displayed while its unfinished training dose is
excluded from learning. Policy and preparation keys invalidate older derived
recommendations; original measurements and approved fixed programmes are retained.
