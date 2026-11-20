# Energy and nutrition: staging candidate

## Scope

`/energy` shows dated daily energy estimates, dietary carbohydrate/fat/protein targets (kcal and g), resting/non-training movement/training components, and collapsible five-minute activity details. Original recording intervals drive integration. The UI explicitly distinguishes accounted partial energy, full modelled daily expenditure and proposed intake. No food intake is logged and no energy deficit is inferred.

The existing assistant can read `kind=energy` with `date` or `start/end`, under recovery-data permissions. The menu button pre-fills a request; it does not send a message automatically. The assistant reads the same worker-owned report and the dated profile, checks allergies/preferences, and proposes food amounts with approximate nutritional sums. This is prompt-guided generation: a food-composition database and automatic verification of generated food nutrients are not implemented.

## Scientific model

Let h be measured HR / the dated reference maximum and z=(100h−80)/20. The non-protein carbohydrate fraction is a+bz+cz²+dz³, bounded to [0,1].

| Group | a | b | c | d |
|---|---|---|---|---|
| Male | 0.5966452238309221 | 0.3559872899390167 | 0.06930439199917234 | -0.02193690576911104 |
| Female | 0.3418807794179927 | 0.28642903908268263 | 0.24247127488145717 | 0.09579414397959929 |

The model uses 196 observations, 24 athletes, equal total weights per athlete and conditional CHO=100% for RER≥1. This is an exploratory within-sample model, not external validation. Apply only over 50–100% HRref. The empirical gross energy rate remains kg × (−0.09354289849 + 0.39849083619h − 0.04590881586 × female), kcal/min. That energy model was not independently revalidated in the cubic comparison.

Use observed raw HR, not HRmod, because the regression was fitted to measured HR. Existing unified interval timing, sensor validity, gaps and speed eligibility are reused. With absent/invalid HR, use the position within the same speed zone and map it to dated HR-zone boundaries, correcting the existing sport offset. This mapping is an explicit estimate, not an identity between %speed, %VO2max and %HRmax. Do not derive nutritional energy from Q or equivalent training minutes. Speed mapping follows the existing historical speed-model policy, including its declared retrospective-test window.

Exercise protein energy is a 3.28% group prior, taken out of total energy before allocating the remaining energy to CHO/fat; it is not added on top. Clauss & Jensen (2025) report 30 studies, 286 participants, endurance bouts ≥60 min. Applying the prior to shorter/interval exercise is an extrapolation. Outside-activity protein is an illustrative 0.81 mg/kg/min prior from the control conditions in that review, extrapolated across non-activity time; it is not measured whole-day oxidation. Gram values use food-energy equivalents 4/9/4 kcal/g, not direct tissue losses.

## Daily accounting and unknowns

Running, cycling and Nordic/roller skiing use this endurance estimate. Strength/weight-training recordings use the separate session estimate below; other sports remain explicitly unsupported. Activity energy is gross. Therefore only time outside recordings receives the non-activity resting estimate; do not add another full-day resting total. Local day duration accounts for daylight-saving changes. Missing HR/speed intervals, unsupported activity, out-of-domain HR, overlapping or cross-midnight recordings prevent a complete daily total/target. Cross-midnight records are flagged on affected days rather than split with invented intensities. Current days are partial, without full-day targets. No recorded activity is not proof of physical inactivity; full totals assume complete activity capture within the saved history range.

Daily cards split the accounted total into resting expenditure outside recordings, additional movement/TEF outside recordings, and gross activity energy. Their sum equals the accounted total; training-time resting energy is never added twice. Only one protein quantity is presented: dietary target, also reused by the assistant and daily target chart. Internal oxidation estimates remain for energy conservation, not as a competing protein recommendation. Chart controls switch between expenditure sources, accounted carbohydrate/fat oxidation in g/kcal (also available on partial days), and dietary targets in g/kcal. Partial days show only fallback protein, not invented complete calorie/CHO/fat goals. Missing values remain absent, not zero. Activity coverage and strength MET estimates remain distinct in collapsible details.

Strength uses the 2024 Adult Compendium code 02054 starting value of 3.5 MET (multiple exercises, 8–15 repetitions). Gross kcal = MET × 3.5 × kg / 200 × minutes, using the standard oxygen equivalent. This is a mean for the recorded session including rests, not an inference from HR or exercise sets. `strength_met` is dated and configurable (6 MET is the Compendium's vigorous weightlifting entry). An optional displayed increment subtracts the personal resting expenditure over the same interval; the daily sum uses gross energy plus outside-recording energy only. Five-minute bins uniformly distribute the estimate and are labelled accordingly. MET supplies no substrate split: `strength_cho_fraction` defaults to 0.5 of non-protein energy as an explicit adjustable assumption, and the 3.28% protein prior is an unvalidated extrapolation from endurance exercise. No additional EPOC estimate is added.

Non-activity energy = resting kcal/24 h × non-activity elapsed hours /24 × an explicit factor (default 1.2). The factor covers ordinary movement and thermic effect as a working assumption; steps and device kcal are not added again. Resting kcal can be entered, or estimated by Mifflin–St Jeor with weight, height, sex and approximate age from birth year. CHO outside activity defaults to 50% of non-protein energy, clearly labelled as a user-adjustable assumption. No Garmin total is inferred from an ambiguous Intervals field.

Dated nutrition profiles preserve sex, baseline weight, height, birth year, resting estimate, factor, non-activity CHO fraction and preferences. A weight observation at/before the day, within 30 days and no earlier than that profile, supersedes its baseline weight; future observations never change earlier days.

Dietary protein follows an explicit application heuristic for adults: let E = complete daily kcal / dated body mass. The coefficient is clamp(1.6 + 0.0075 × (E − 40), 1.6, 1.9) g/kg. The denominator is total daily expenditure, not exercise-only kcal or food intake. Training/rest labels and sex do not change this coefficient. Incomplete days use 1.8 g/kg, with calorie/CHO/fat targets absent. Missing weight or unconfirmed adult age produces no target. Birth year is approximate; the minimum possible age (year difference minus one) must be at least 18.

After assigning protein, remaining dietary calories follow the estimated non-protein CHO/fat ratio for the day, retaining its dynamics. These are working maintenance allocations, not a validated dose-response or replacement diet for injury/illness, energy deficiency or competition planning. The cited literature does not validate the 40–80 kcal/kg linear mapping. There is no automatic deficit.

## Architecture and release

API `GET /api/v2/athlete/energy` uses the existing prepared-page worker and revision fences; an HTTP page load never integrates recordings. Immutable per-recording ENERGY derivations are keyed by the existing calculation-source fingerprint, model version, source run key, dated HR settings, speed parameters, body mass and sex. Energy service/model and route contracts participate in that source identity, so numerical code changes cannot reuse stale results. Recordings stream rather than accumulate in memory. Nutrition profile writes use the existing append-only response store, optimistic revisions, actor checks and prepared-view invalidation. The web requires recovery sharing and owner or editing rights for writes.

Assistant overview reads omit chart timelines and historical profile versions; explicit section reads retain access to the same full saved report. Current-day outside energy covers only elapsed time at report preparation, not the remaining future hours. No new worker, queue or paid service is introduced.

Apply the additive `energy_nutrition_reviewed` migration before API/worker/web release. It extends three kind constraints and the existing server-only response/derivation functions; it does not alter recorded activity measurements. No historical data is overwritten and no eager whole-user backfill is requested. Existing body inputs are reused automatically. The already-issued Intervals athlete request now retains only validated sex, height (cm), birth year and dated body mass in the snapshot; no extra provider call, raw profile, identity, full birthday or credentials are persisted. Imported mass is not projected before its observation date. Dated diary/wellness observations override baseline mass. Manual dated values override imported values; missing fields stay nullable and are requested once. Sex, height and preferences are never guessed from a name. Old snapshots remain readable; provider fields become available on the next normal full synchronization. Changing the outside factor does not require duplicating missing personal fields. This update needs no additional database migration.

## References

- Clauss M, Jensen J (2025). Effect of Exercise Intensity, Duration, and Volume on Protein Oxidation During Endurance Exercise in Humans: A Systematic Review With Meta-Analysis. https://pubmed.ncbi.nlm.nih.gov/40135445/
- Witard OC, Hearris M, Morgan PT (2025). Protein Nutrition for Endurance Athletes: A Metabolic Focus on Promoting Recovery and Training Adaptation. https://pubmed.ncbi.nlm.nih.gov/40117058/
- Mifflin MD et al. (1990). A new predictive equation for resting energy expenditure in healthy individuals. https://pubmed.ncbi.nlm.nih.gov/2305711/
- 2024 Adult Compendium of Physical Activities, conditioning exercise, codes 02054 and 02050. https://pacompendium.com/conditioning-exercise/
