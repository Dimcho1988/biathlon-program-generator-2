# HRmod v10: low-zone compensation

Authorized staging change, 2026-11-19. Production result version:
`hrmod_low_zone_compensation_v10`. The mirror estimator and its configuration
remain `hrmod_mirror_area_shift_v9` / `hrmod_config_v9`; its wave detection,
upward amplitudes and terrain eligibility are unchanged.

The final stage keeps positive terrain-adjusted additions and replaces the old
post-peak removals with compensation from the original measured zones in order
Z1, Z2, Z3, Z4, Z5, over the whole recording. A receiver cannot also donate.
Within each zone removal is proportional to available capacity, capped at
30 bpm and at that athlete/sport's lower Z1 boundary. No universal HR floor is
applied across athletes. If all eligible zones lack capacity, additions are
scaled proportionally and the shortfall is explicit in diagnostics.

This is an experimental accounting policy, not a measured oxygen-debt model.
It preserves supported pulse area, not weighted training load or any mandated
zone duration. It can use lower-zone samples before or after an effort. The
unchanged wave table is explicitly labeled as the amplitude estimator; final
donor flags, zone times, segment means, diagnostics, hashes and selected load
use the compensated result. The raw and clean channels are not overwritten.

Area accounting uses held one-second intervals and trapezoidal smart-recording
intervals, matching the load ledger. Missing HR, timer pauses, unsupported gaps,
and an unsupported final observation cannot fund compensation. The new result
version invalidates shadow and trainability comparison fingerprints. Vflat and
raw provider inputs are reused, not recalculated or rewritten.

## Historical upgrade

Use the existing fenced FULL_SYNC worker with reason `HR_CALIBRATION_CHANGED`
and request field `hrmod_upgrade: hrmod_low_zone_compensation_v10`. This retains
the queue's existing provider-refresh coalescing safeguards. The upgrade reads
the pinned generation's complete stored period, retaining dated HR settings.
It reruns HR only, reuses speed, rebuilds chronological speed/capacity evidence,
direct load, component load, 7/40 and recovery, and publishes a new generation
atomically. Existing snapshots remain usable until activation. No settings
revision or raw input is changed. Missing/incompatible stored inputs trigger
the existing explicit full-refresh fallback, not a partly migrated snapshot.

Start with one profile and measure request, worker start, activation and model
preparation times separately. Queue further profiles sequentially after the
pilot succeeds; retain previous immutable generations for comparison. The
scope is each active stored history, not unimported lifetime provider history.

## Validation

Tests cover zone priority, receiver protection, bounds, insufficient capacity,
irregular recording/pauses/missing HR, conservation and exact equality of local
historical upgrades to a complete refresh, including load and recovery.
