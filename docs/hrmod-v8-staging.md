# HRmod v8: accumulated rises, plateau endpoints and 320-second waves

Approved for staging on 2026-09-13. Model `hrmod_mirror_area_shift_v8`, config `hrmod_config_v8`.

## Detection

A rise still requires a fitted slope of at least 0.15 bpm/s to initiate, an accumulated rise of at least 5 bpm, and at least 3 elapsed seconds. Anchor it at the nearby local trough, including when sparse or rounded HR prevents a positive fitted slope at the trough itself. Do not cross an earlier wave or recording block. Neutral holds and reversals below 3 bpm retain the candidate; a new low, reversal of at least 3 bpm, or the 320-second candidate horizon releases it.

A falling slope of at most -0.10 bpm/s starts fall confirmation. Require at least 3 elapsed seconds and a fall of at least 3 bpm from the highest detected HR. A neutral/rising sample within 2 bpm of that high extends the top plateau and resets the fall candidate. On confirmation, split at the later of the highest sample and the last such plateau sample. The full rise and top plateau are receiver; only samples after that split are donor. `peak_elapsed_s` continues to expose the receiver/donor split for compatibility; with a plateau it need not be the time of the absolute maximum.

The same accumulated-rise rule closes a preceding tail at the intervening trough, even if HR has not returned to the preceding baseline. Existing return-to-baseline, sustained lower-trough, recording-gap and incomplete-edge rules remain. Complete waves longer than 320 seconds are excluded; 320 seconds is inclusive. The separate 600-second detection safety timeout remains.

## Unchanged calculation and scope

Keep 78% HRmax eligibility, 5-second detection smoothing, 20-second median baseline, exact original sample durations, alpha=1, mirror area allocation, +/-30 bpm caps and HRmax. Keep the separate terrain donor exclusion and the 15-second diagnostic summaries. No physiological assumption is added to force a target number of minutes in Z4 or Z5. Abrupt receiver-to-donor transitions and saturation at HRmax remain possible with the existing allocator.

Canonical load, Recovery, Tref, Vflat and database schema are unchanged. Trainability is recalculated from the changed HRmod through its existing model comparison key.

The adapter's `SOURCE_COMMIT` is the original imported core provenance, not the deployment revision. The v8 model/config IDs identify this revision and change the derived configuration fingerprint.

## Validation and rollout

Regression cases cover ten staircase repeats sampled at 1/2/3-second intervals; noisy lower plateaus; 208-second acceptance and >320-second rejection; no correction from small HR noise or incomplete plateaus. Existing tests cover gaps, irregular durations, conservative exclusions, area balance and HR bounds. Cache tests verify v7 cannot be reused with the v8 fingerprint.

Private TCX inputs from 06.09 and 10.09 reproduce the old staging results before modification. The new detector resolves ten individual waves in the second block of 06.09 and automatically finds the final plateau endpoint of 10.09 near 52:07. Input files are not committed. Detection success is distinct from the amount of time crossing a zone boundary.

Deploy the staging API and web, then the staging worker, from the same merge. Wait for the previous worker to stop before queuing FULL_SYNC. Use the existing job queue and atomic generation activation. Confirm health reports v8 and refreshed stored payloads carry v8; the UI derives its version label from the payload. Production services use the separate `production` branch and are outside this rollout.
