# Vflat terrain-density correction

Approved for staging on 2026-09-18 after offline comparisons on Valya's
NordicSki and Walk (roller-ski) activities. The fixed coefficients are
experimental and have not shown consistent improvement for other athletes.

- `d = total_elevation_gain / (distance / 1000)` uses provider totals.
- `p = max(0, 0.8834239445414596 * d - 7.974288128873758)`.
- Every final Vflat sample is divided by `1 + p / 100` once.
- Apply only to NordicSki/Walk within 6.177543912583202–29.354843998838497 m/km.
- Leave predominantly uphill sessions unchanged: at least 80% of moving time
  above +1% grade and less than 10% below -1%. Time weights respect recording
  gaps; confirmed stops at <=1 km/h lasting >=3 seconds are omitted.
- Missing/invalid provider totals, unsupported sports, missing terrain samples
  and routes outside the tested range leave Vflat unchanged. No altitude-based
  ascent estimate or extrapolation is substituted.

The V4 stationary curve, grade memory, smoothing, HRmod, index pairing, sample
exclusions and minimum durations are unchanged. Corrected Vflat reaches the
time series, segments, speed-duration inputs and all valid zone/general indices.
Pre-correction Vflat and the factor/reason are retained in diagnostics.

Model/config version V5 invalidates previous cached calculations. The per-session
cache fingerprint includes provider sport/distance/ascent, so edits to these
totals trigger recomputation. These route-specific values do not split the
model comparison key. Deploy staging API/web/worker from the same commit and
use the existing FULL_SYNC queue to atomically activate recomputed history.
Production uses its separate branch. No database migration is required.
