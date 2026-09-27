# Body mass and laboratory observations

`body-observations-v1` extends the response dashboard and the append-only response
store. It adds authenticated entry, corrections, personal references and dated
context. It does not establish a validated stress index from body mass or blood
tests. The existing 50/30/20 pilot score, block return assessment, Recovery model
and load progression controller retain their current meaning.

## Author's weight indices

The supplied workbook's intended ratios are:

- Morning ratio = mean morning mass over the last 7 calendar days **including the
  observation day**, divided by that day's morning mass.
- Session ratio = mean pre-exercise mass divided by mean post-exercise mass.

The attachment available for inspection contained dates and formulas but no
populated measurement cells. Some copied formulas also referenced other athletes'
columns or Excel date serials. Consequently no performance association, fitted
weights, physiological thresholds or validation statistics can be inferred from
that file. Personal data from the workbook is not included in this repository.

Implementation preserves the intended ratios, with these explicit data rules:

- One revisioned report per athlete-local day; four numbered sessions supported.
- Morning entries must be confirmed after toilet, before intake, using the same
  scale and comparable clothing. Generic Intervals weight is shown as an editable
  suggestion; it is not silently treated as a standardized morning measurement.
- At least 4 measured, standardized days within `[day-6, day]`, including `day`,
  are required for the morning ratio. This is an initial **coverage convention**,
  not a validated biological cutoff. The mean and count remain visible below it.
- No interpolation, zero filling, last-value carry-forward or substitution of
  seven observations across a longer period. A long gap naturally empties the
  reference window. Missing current weight always means no current ratio.
- Only complete, confirmed pre/post pairs from the same session enter the
  session ratio. An unmatched measurement can be saved and completed later.
- Each pair also reports `100 * (before-after)/before`. Negative values (mass
  gain) are preserved. Fluid/urine intake are stored as context, not added to the
  author's ratio or presented as a validated sweat-loss estimate.
- A morning ratio **above 1 means mass below the rolling mean**. Neither a rise
  nor a fall by itself proves fatigue, recovery, energy deficiency or fitness.

## Laboratory observations

Supported: urea (not BUN), CK, hemoglobin, **total** testosterone, cortisol,
ferritin, CRP and transferrin saturation. No universal laboratory reference ranges
are injected. Users transcribe the laboratory's applicable range in the same
units as the result. Comparisons with these limits are labeled literally rather
than diagnosed as disease or training maladaptation.

Each sample records a stable random identifier, local collection day/time,
laboratory, comparison protocol, fasting status, time since training, specimen
matrix, exact vs `<`/`>` result, units, reference limits and contextual note.
Corrections use optimistic revisions; retrying an identical save is idempotent.
Results can be entered by the owner or a coach with both recovery viewing and
planning editing access. Weight entry requires ownership. Existing recovery
sharing permissions govern reading these records, including biochemical data.

Supported unit conversions: urea mg/dL to mmol/L ×0.1665; hemoglobin g/dL to g/L
×10; total testosterone ng/dL to nmol/L ×0.03467 (ng/mL ×3.467); cortisol ug/dL to
nmol/L ×27.59; ferritin ng/mL equals ug/L; CRP mg/dL to mg/L ×10. Original values
and units are retained. CK uses U/L and saturation uses percent.

The T/C ratio is dimensionless, after conversion of both components to nmol/L.
It requires positive exact values from the **same sample and specimen matrix**.
Separate dates, missing hormones or censored results cannot form a ratio. No
universal T/C stress threshold or automatic training action is applied.

Personal comparisons require 3 distinct earlier sampling days in the preceding
365 days, confirmed comparable, matching laboratory, protocol, fasting status,
collection clock-hour and specimen matrix. Same-day repeat samples are collapsed
to a daily median and never counted as separate baseline days. The current day
and future observations are excluded. The reference is a median and the output
is an observed percentage change, not a diagnostic score. The minimum count and
lookback are engineering conventions for this pilot, not medical guidelines.

Non-exact results are excluded from personal references, but a result such as
`CK > 2000 U/L` still signals above-range when the entered upper limit is 180.
An absent result never becomes zero. Old samples remain dated historical records;
they are not forward-filled into daily stress. Same-day above/below-range flags
remain separately visible even when the existing numeric score is low.

## Integration and validation boundary

The response API exposes versioned `body_observations` on each day and
`lab_reports` with original inputs, normalization and comparison evidence. This
unites the observations in the stress/recovery view, but their numerical score
weight remains zero until prospective validation. To estimate that contribution,
collect actual standardized measurements alongside workload, performance, RPE,
symptoms and outcomes; assess held-out future blocks and incremental value beyond
existing correlated signals. Do not train and evaluate on the same observations.

Potential confounding includes fluid/glycogen balance and intake for body mass;
exercise type and sampling lag for CK; diet and hydration for urea; plasma volume
for hemoglobin; collection timing and context for hormones; inflammation when
interpreting ferritin. Abnormal findings need sports-medicine interpretation.

Apply the additive `body_observations` migration before deploying API writes.
It extends the kind constraint and existing invoker RPC, retains RLS, service-role
only table/RPC privileges, actor checks, advisory locks and optimistic revisions.
No provider import, activity streams or training-plan recalculation is required
when entering observations. Reads reuse stored daily aggregates; laboratory
reference lookup is grouped and bounded to a year's distinct prior days.

## Sources

- NATA, *Fluid Replacement for the Physically Active* (2017):
  https://pmc.ncbi.nlm.nih.gov/articles/PMC5634236/
- ECSS/ACSM, *Prevention, diagnosis, and treatment of the overtraining syndrome*
  (2013 consensus; no single universally accepted marker):
  https://pubmed.ncbi.nlm.nih.gov/23247672/
- AIS iron guidance: ferritin, hemoglobin and transferrin saturation as minimum
  profiling, with CRP as additional context and standardized sampling:
  https://www.ausport.gov.au/ais/nutrition/supplements/group_a/medical-supplements2/iron/how-and-when-do-i-use-it

Tests use synthetic data only. They verify calculations, gaps, specimen/unit
compatibility, dated historical context, revisions and access boundaries, not
clinical validity or live-deployment status.
