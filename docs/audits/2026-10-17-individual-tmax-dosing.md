# Individual method dosing — 17 October 2026

The old library adapter derived the interval coefficient from the example's
maximum repetitions divided by the individual's Tmax. Multiplying that ratio
by Tmax cancelled individual capacity. Interval role selection also replaced
the selected developmental fraction with 100%.

The implemented contract is:

- Resolve continuous Tmax at the prescribed effort from the selected individual
  curve (or retain the explicitly identified coach fallback when no curve exists).
- Multiply by the independently editable method work-capacity coefficient.
- Multiply by the selected developmental fraction (default 0.65).
- For developmental work, multiply once by `0.5 + 0.5 * readiness`.
- For maintenance/support/activation, use 60% of the unreduced developmental
  dose without another readiness multiplier. Existing eligibility remains.
- Mixed library work components receive equal shares of their own capacities;
  a two-component pattern therefore uses half of each. Pattern proportions and
  whole efforts remain intact. Explicit paired methods keep their own budgets.
- Quantize work, then enforce the existing time, rolling load and long-term Q/E
  constraints. Recovery/rest time is not aerobic work. Strength dosing is unchanged.

Example: Tmax 5 minutes, coefficient 250%, developmental fraction 65%, readiness
80% → 7.3125 minutes before whole-repeat rounding. Maintenance → 4.875 minutes.
The original count is provenance, not a capacity ceiling. Explicit library
repetition overrides remain coach constraints. Automatic and personal native
interval profiles derive executable repeat counts from individual capacity.

Initial coach defaults: continuous/native aerobic profiles 100%; long generic
intervals 200%; short generic intervals (up to 120 seconds) 250%; compound source
patterns 250%. Existing explicit interval coefficients take precedence. These
are editable starting values, not scientifically validated physiological maxima.

New preference fields are `total_capacity_ratio`, `speed_tolerance_fraction`,
and `hr_tolerance_bpm`. Catalog previews and plans use the same resolver. Existing
explicit speed ranges remain intact, including the ski adaptation ranges.
For point prescriptions, provisional defaults are ±2% speed and ±3 bpm;
these are not represented as recovered historical agreements. Generated
ranges use available individual zone boundaries. HR remains a secondary
reference, especially during short efforts; central targets still drive Q/E.

The UI shows ranges and explicitly identifies a reduced dose. Existing
long-term targets can still keep Z5 doses small; this change does not raise
those targets or promise complete allocation. One frozen athlete replay took
about 7.4 seconds locally; this is not an end-to-end server latency measurement.

Tests cover coefficient/curve/role independence, 60–70% selections, 80% readiness,
source-count invariance, mixed component capacities, whole repeats, range
preservation/clipping and preference serialization. Existing expectation changes
reflect the new contract. The allocation fixture uses 390 instead of 400 because
its 400 case now reaches rolling caps with 2.38 Q remaining; the realization
assertion is unchanged. The multisport closure test now exercises the actually
balanced ski Z2 method instead of the smaller Z3 support session.

More repetitions increase payload size. Repeated instructions and lactate
references are retained in full at their first occurrence; later identical
instructions refer to that part. Locked sessions are not compacted again.
