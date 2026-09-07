import type { TrainabilityHistory, TrainabilityIndex } from "./trainability";

// Explicit local/test fixture only; never used in API data mode.
const template: TrainabilityIndex = {
  "schema_version": "trainability-index-v1",
  "model_version": "trainability_rank_v1",
  "comparison_key": "fixture-current",
  "source_versions": {
    "vflat": "vflat_b65_dynamic_v3_uphill150",
    "hrmod": "hrmod_mirror_area_shift_v7"
  },
  "hrmax_bpm": 178,
  "zone_bounds_bpm": [
    50.0,
    137.0,
    147.0,
    158.0,
    170.0,
    178.0
  ],
  "activity_duration_s": 900,
  "minimum_activity_seconds": 420.0,
  "minimum_seconds": 60.0,
  "minimum_grade_pct": -3.0,
  "general_range_percent": [
    75,
    92
  ],
  "hr_seconds": 900.0,
  "eligible_speed_seconds": 900.0,
  "downhill_excluded_seconds": 0.0,
  "unavailable_speed_seconds": 0.0,
  "zones": [
    {
      "name": "Z1",
      "lower_bpm": 50.0,
      "upper_bpm": 137.0,
      "hr_seconds": 180.0,
      "hr_percent": 20.0,
      "speed_seconds": 180.0,
      "mean_hrmod_bpm": 125.0,
      "mean_hrmax_percent": 70.2247191011236,
      "mean_vflat_kmh": 12.0,
      "index": 10.416666666666666,
      "valid": true,
      "invalid_reason": null
    },
    {
      "name": "Z2",
      "lower_bpm": 137.0,
      "upper_bpm": 147.0,
      "hr_seconds": 180.0,
      "hr_percent": 20.0,
      "speed_seconds": 180.0,
      "mean_hrmod_bpm": 142.0,
      "mean_hrmax_percent": 79.7752808988764,
      "mean_vflat_kmh": 16.0,
      "index": 8.875,
      "valid": true,
      "invalid_reason": null
    },
    {
      "name": "Z3",
      "lower_bpm": 147.0,
      "upper_bpm": 158.0,
      "hr_seconds": 180.0,
      "hr_percent": 20.0,
      "speed_seconds": 180.0000000000001,
      "mean_hrmod_bpm": 153.0,
      "mean_hrmax_percent": 85.95505617977528,
      "mean_vflat_kmh": 18.999999999999996,
      "index": 8.05263157894737,
      "valid": true,
      "invalid_reason": null
    },
    {
      "name": "Z4",
      "lower_bpm": 158.0,
      "upper_bpm": 170.0,
      "hr_seconds": 180.0,
      "hr_percent": 20.0,
      "speed_seconds": 180.0,
      "mean_hrmod_bpm": 163.0,
      "mean_hrmax_percent": 91.57303370786516,
      "mean_vflat_kmh": 22.0,
      "index": 7.409090909090909,
      "valid": true,
      "invalid_reason": null
    },
    {
      "name": "Z5",
      "lower_bpm": 170.0,
      "upper_bpm": 178.0,
      "hr_seconds": 180.0,
      "hr_percent": 20.0,
      "speed_seconds": 180.0,
      "mean_hrmod_bpm": 174.0,
      "mean_hrmax_percent": 97.75280898876404,
      "mean_vflat_kmh": 28.0,
      "index": 6.214285714285714,
      "valid": true,
      "invalid_reason": null
    }
  ],
  "general": {
    "name": "GENERAL",
    "lower_bpm": 133.5,
    "upper_bpm": 163.76000000000002,
    "hr_seconds": 540.0,
    "hr_percent": 60.0,
    "speed_seconds": 540.0,
    "mean_hrmod_bpm": 152.66666666666666,
    "mean_hrmax_percent": 85.76779026217228,
    "mean_vflat_kmh": 19.0,
    "index": 8.035087719298245,
    "valid": true,
    "invalid_reason": null
  }
};

export const trainabilityFixture: TrainabilityHistory = {
  schema_version: "trainability-history-v1", period_start: "2026-06-10", period_end: "2026-09-07",
  generation_id: "fixture-generation", revision: 1,
  activities: Array.from({ length: 12 }, (_, i) => {
    const index = structuredClone(template);
    const factor = 1 + i * 0.007;
    for (const band of [...index.zones, index.general]) {
      band.mean_vflat_kmh = band.mean_vflat_kmh! * factor;
      if (band.valid) band.index = band.mean_hrmod_bpm! / band.mean_vflat_kmh;
    }
    if (i === 5) {
      index.activity_duration_s = 419;
      for (const band of [...index.zones, index.general]) {
        band.valid = false; band.index = null; band.invalid_reason = "ACTIVITY_BELOW_7MIN";
      }
    }
    const date = new Date(Date.UTC(2026, 7, 1 + i * 3)).toISOString().slice(0, 10);
    return {
      activity_ref: `act_${String(i + 1).padStart(32, "0")}`, name: `Примерна тренировка ${i + 1}`,
      sport: i === 2 ? "Run" : "RollerSki", start_at_utc: `${date}T08:00:00Z`, local_date: date,
      index, unavailable_reason: null,
    };
  }),
};
