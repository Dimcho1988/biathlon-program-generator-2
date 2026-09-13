import type { TrainabilityHistory, TrainabilityIndex } from "./trainability";

// Explicit local/test fixture only; never used in API data mode.
const template: TrainabilityIndex = {
  "schema_version": "trainability-index-v2",
  "model_version": "trainability_rank_hrmax_v2",
  "normalization": "percent_hrmax",
  "comparison_key": "fixture-current-v2",
  "source_versions": {
    "vflat": "vflat_b65_dynamic_v3_uphill150",
    "hrmod": "hrmod_mirror_area_shift_v8"
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
  "activity_duration_s": 2100,
  "minimum_activity_seconds": 420.0,
  "minimum_seconds_by_band": {
    "Z1": 420.0,
    "Z2": 420.0,
    "Z3": 420.0,
    "Z4": 420.0,
    "Z5": 300.0,
    "GENERAL": 420.0
  },
  "minimum_grade_pct": -3.0,
  "general_range_percent": [
    75,
    92
  ],
  "hr_seconds": 2100.0,
  "eligible_speed_seconds": 2100.0,
  "downhill_excluded_seconds": 0.0,
  "unavailable_speed_seconds": 0.0,
  "zones": [
    {
      "name": "Z1",
      "lower_bpm": 50.0,
      "upper_bpm": 137.0,
      "minimum_seconds": 420.0,
      "hr_seconds": 420.0,
      "hr_percent": 20.0,
      "speed_seconds": 420.0,
      "mean_hrmod_bpm": 125.0,
      "mean_hrmax_percent": 70.2247191011236,
      "mean_vflat_kmh": 12.0,
      "index": 5.852059925093633,
      "valid": true,
      "invalid_reason": null
    },
    {
      "name": "Z2",
      "lower_bpm": 137.0,
      "upper_bpm": 147.0,
      "minimum_seconds": 420.0,
      "hr_seconds": 420.0,
      "hr_percent": 20.0,
      "speed_seconds": 420.0,
      "mean_hrmod_bpm": 142.0,
      "mean_hrmax_percent": 79.7752808988764,
      "mean_vflat_kmh": 16.0,
      "index": 4.985955056179775,
      "valid": true,
      "invalid_reason": null
    },
    {
      "name": "Z3",
      "lower_bpm": 147.0,
      "upper_bpm": 158.0,
      "minimum_seconds": 420.0,
      "hr_seconds": 420.0,
      "hr_percent": 20.0,
      "speed_seconds": 420.0000000000002,
      "mean_hrmod_bpm": 153.0,
      "mean_hrmax_percent": 85.95505617977528,
      "mean_vflat_kmh": 19.0,
      "index": 4.523950325251331,
      "valid": true,
      "invalid_reason": null
    },
    {
      "name": "Z4",
      "lower_bpm": 158.0,
      "upper_bpm": 170.0,
      "minimum_seconds": 420.0,
      "hr_seconds": 420.0,
      "hr_percent": 20.0,
      "speed_seconds": 420.0,
      "mean_hrmod_bpm": 163.0,
      "mean_hrmax_percent": 91.57303370786516,
      "mean_vflat_kmh": 22.0,
      "index": 4.162410623084781,
      "valid": true,
      "invalid_reason": null
    },
    {
      "name": "Z5",
      "lower_bpm": 170.0,
      "upper_bpm": 178.0,
      "minimum_seconds": 300.0,
      "hr_seconds": 420.0,
      "hr_percent": 20.0,
      "speed_seconds": 420.0,
      "mean_hrmod_bpm": 174.0,
      "mean_hrmax_percent": 97.75280898876404,
      "mean_vflat_kmh": 28.0,
      "index": 3.491171749598716,
      "valid": true,
      "invalid_reason": null
    }
  ],
  "general": {
    "name": "GENERAL",
    "lower_bpm": 133.5,
    "upper_bpm": 163.76000000000002,
    "minimum_seconds": 420.0,
    "hr_seconds": 1260.0,
    "hr_percent": 60.0,
    "speed_seconds": 1260.0,
    "mean_hrmod_bpm": 152.66666666666666,
    "mean_hrmax_percent": 85.76779026217228,
    "mean_vflat_kmh": 19.0,
    "index": 4.514094224324857,
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
      if (band.valid) band.index = band.mean_hrmax_percent! / band.mean_vflat_kmh;
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
