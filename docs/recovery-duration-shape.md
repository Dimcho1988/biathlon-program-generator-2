# Recovery v2.3: duration-dependent shape

The canonical recovery model used by dashboard and planner now derives each daily impulse shape from its original nominal duration D = duration_coefficient × E / baseline:

s = 1 + 4 × clamp((D − 2) / 5, 0, 1).

D ≤ 2 days gives s=1; 2–7 days interpolate linearly; D ≥ 7 gives s=5. The shape is frozen with the original impulse, never recalculated from remaining days. All zones, including strength, use the same rule. No parallel model or experiment is enabled.

The two exponential terms retain equal weights. Amplitude, baseline and E calculation are unchanged. The decay rate is calibrated to the same isolated absolute 90% readiness deadline for material impulses (amplitude ≥20). The existing half-life floor remains for small impulses. Aggregate recovery deadlines can differ when several impulses overlap. A higher shape increases early recovery and may leave a larger late tail. Seven days is a shape saturation point, not a recovery-duration cap.

The rule is an expert modelling choice, not an independently validated physiological law. Legacy shape settings remain validated and stored for schema compatibility but no longer determine curves; the v2.3 editor removes their input. The daily diagnostic shape_used exposes the actual coefficient. Duration, sensitivity and permanent baseline additions remain configurable.

API metadata and preparation source identity invalidate derived recovery dashboards. Existing recorded E and raw activities need no migration or reprocessing. Newly generated plans use the same canonical simulator; already saved workouts are not automatically rewritten.
