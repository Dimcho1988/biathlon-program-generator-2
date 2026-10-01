# Cycling speed and a parallel speed-load ledger

The cycling model is `vflat_cycling_mechanical_v1` / `vflat_cycling_80kg_crr005_cda032_v1`.
RUN's grade table and Nordic B65 are unchanged. Method dosing keeps the approved 30% index / 70% maximal-test curve and both original curves.

## Flat-equivalent cycling speed

For speed v in m/s and grade G in percent, θ = atan(G/100):

    P = [m g (sin θ + Crr cos θ) + 0.5 ρ CdA v²] v
    P = m g Crr u + 0.5 ρ CdA u³

The nonnegative root u is converted to km/h. Reference assumptions: total rider/equipment mass 80 kg, Crr 0.005, CdA 0.32 m², air density 1.225 kg/m³, g 9.81 m/s², no wind. This is a steady mechanical estimate, not measured watts or a validated metabolic model. Wind, drafting, surface, equipment and rider position remain unobserved. MTB/gravel use the same reference, so their absolute estimates need particular care. Assisted cycling is not admitted to the speed ledger. Indoor/virtual cycling requires recorded speed and grade; missing grade is never assumed flat.

Existing block-local speed and altitude/distance preprocessing is retained. Valid samples require speed ≥5 km/h, grade from −3% through +30%, nonnegative driving power, and an active recording block. Recorded zero cadence excludes coasting; missing cadence remains unknown. Steeper descents and the following 20 seconds are excluded. A 21-second median is re-masked so invalid points never become valid through smoothing. Acceleration/braking energy is not inferred.

## Heart-rate reference

`sport-hr-reference-cycling-plus7-v1` is an explicit initial coaching assumption:

- Common-reference HR = measured cycling HR +7 bpm.
- Cycling zone bounds and target HR = reference bounds/target −7 bpm.
- TI uses the reference HRmax and the normalized HR with the existing +20-second pairing lag. Thus 143 bpm on a bike compares to 150 bpm on a run. The raw sensor screen runs before normalization.
- The transient cycling HRmax coordinate is reference HRmax −7; this does not assert a measured cycling HRmax or rewrite the athlete's profile.

Raw provider observations remain immutable. Only cycling input hashes add the optional cadence channel. Cycling model/configuration and HR policy invalidate previous derived outputs. Old imported cycling maximal tests must be recomputed/re-saved from observations; they are never silently relabelled. Manual measured flat tests remain valid. The ordinary full sync refreshes derived runs and the canonical cycling HR-zone history. Planning refuses a cycling history without the new policy stamp. No database migration or direct historical rewrite is required.

## Speed-based Q, E and 7/40

`GET /api/v2/athlete/models/speed-load` returns a separate, read-only estimate for the authenticated athlete. Optional `sport` selects one exact provider sport. No raw speeds or indices are averaged across sports.

For each activity, accepted same-sport paired indices from the preceding 40 calendar days define the map; the entire current day is excluded. This prevents circular self-calibration and use of future HR. Zone boundaries use `v = (100 HRref / HRmaxref) / TI`. Missing zonal indices may use the measured GENERAL index for that sport, visibly marked as an extrapolative estimate. If zonal boundaries conflict, the entire map uses the measured GENERAL index with `GENERAL_CONFLICT_FALLBACK`; measured zonal indices remain unchanged. Without a valid GENERAL index, nonmonotone boundaries remain unavailable. Missing prior indices and speeds outside the supported range remain unclassified. Linear interpolation within supported zones supplies a common-reference HR coordinate, not a fabricated HR measurement.

Measured Vflat samples supply time and Q using the existing 3-percentage-point/bpm coefficients (5 in Z5). The canonical causal Q→E cascade, base loads and `(B + E7)/(B + E40)` formula run on this separate speed history. STR is not inferred from speed. Results are not added to the HR ledger, recovery or planning budgets. Existing missing-HR planning estimates are a separate pre-existing feature.

The view displays the latest 40 days, computed with up to 90 days of speed observations for initialization. Pre-window effective-load state is unknown; short or incomplete history limits interpretation. Coverage uses recorded elapsed duration (at least the integrated sample duration), includes activities without calibration, and explicitly distinguishes excluded and out-of-map minutes. Ratios describe covered speed only. Unsupported sports are outside this ledger's scope. Current comparison indices may include today; those display values are never used retroactively to classify earlier sessions.

The UI shows all-sport or single-sport totals, the daily zone 7/40 curve, current same-HR comparison speeds and the 7-bpm cycling policy. Access follows the existing model permissions, requests carry the selected authorized athlete, and results disclose pinned generation/revision.

## Verification

Tests cover power conservation and flat identity, invalid/coasting samples, unchanged run/Nordic compatibility, raw-signal immutability, the HR offset through TI and blended dosing, equal relative-intensity method Q, causal calibration, missing data/old versions, independent speed accounting, coverage and API/UI authorization/generation boundaries.
