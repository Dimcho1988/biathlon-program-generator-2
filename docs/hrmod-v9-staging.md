# HRmod v9: sustained plateaus and accumulated falls

Staging follow-up to v8, authorized on 2026-09-13. Model `hrmod_mirror_area_shift_v9`, config `hrmod_config_v9`.

## Problem and detection change

V8 extended the receiver on any neutral/rising point within 2 bpm of the maximum and reset fall confirmation there. Rounded HR on a staircase descent therefore looked like a plateau. This could merge adjacent waves, shorten donor windows and spread less area over longer receivers. More detected waves did not establish correct boundaries.

V9 retains the fall candidate across neutral holds. A new maximum still resets it. A plateau extension requires at least 15 elapsed seconds and three points in the current recording block, a detected-HR range no wider than 1.5 bpm, and all window values within 1.5 bpm of the high. Both boundary slopes, the window's least-squares slope and its endpoint drift must be within the existing 0.05 bpm/s neutral tolerance. Checking both boundaries avoids treating a short rise/fall arch as a flat top. Once the accumulated fall is confirmed, split at the later of the maximum and the last qualifying plateau sample. Donor starts after the split.

The time window uses original timestamps. The two new positive finite controls are `plateau_min_duration_s` and `plateau_range_bpm`. These exploratory defaults describe morphology; they are not validated physiological thresholds. Sensitivity checks at 12 and 20 seconds preserve the key examples and give Z5 ranges of 76–90 seconds (04 Sep), 5 seconds (06 Sep), and 302–307 seconds (10 Sep), versus 90/5/310 seconds at the selected 15-second default.

## Scope

Keep the 320-second acceptance limit, v8 accumulated rises, 78% HRmax eligibility, the existing baseline and tail closure, +/-30 bpm caps, HRmax, exact-area allocator, terrain exclusion and 15-second summaries. No zone time target, baseline lowering or area multiplier is introduced. Canonical, Recovery, Tref and Vflat are unchanged. Versioned cache and trainability fingerprints invalidate both v7 and v8 results.

## Private-data comparison

Inputs are not committed. 04 Sep uses the immutable normalized staging input; 06/10 Sep use the uploaded TCX files. HRmax 180, bounds 80/137/148/160/170/180, technical floor 30. Original v7 final results were reproduced exactly for all three cases before comparison.

| Activity | v7 Z4 / Z5 | v8 Z4 / Z5 | v9 Z4 / Z5 |
| --- | --- | --- | --- |
| 04 Sep 09:21 | 0:57 / 1:41 | 1:56 / 0:15 | 1:06 / 1:30 |
| 06 Sep 16:00 | 0:32 / 0:16 | 0:28 / 0:05 | 0:50 / 0:05 |
| 10 Sep 17:08 | 3:53 / 4:20 | 4:44 / 3:54 | 3:13 / 5:10 |

04 Sep's wave 79:46–80:53 again splits at 80:22, moving 583 bpm*s rather than 335. The merged 06 Sep wave is separated; the first of those waves is below the unchanged 78% threshold and is correctly unmodified. The ten second-block waves remain separate. The final 10 Sep wave still splits at 52:07 and is corrected. Area errors are below 1e-9 bpm*s. These checks establish implementation behavior, not physiological validation of the model or a required amount of Z5.

Regression tests cover descending holds and accumulated four-bpm falls at 1/2/3-second sampling, ten repeats, sustained noisy plateaus, gaps/incomplete data, duration limits, parameter validation, area/bounds and prior-version cache invalidation. Deploy staging API/web/worker at the same merge, then refresh through the existing FULL_SYNC queue and verify active v9 results.
