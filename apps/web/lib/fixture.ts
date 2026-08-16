import type { TrainingStatus } from "./training-status";
import type { LoadHistory } from "./load-history";
import type { RecoveryHistory } from "./recovery-history";

export const trainingStatusFixture: TrainingStatus = {
  schema_version: "training-status-v1",
  as_of: "2026-06-20",
  athlete_id: "A",
  model: {
    algorithm_version: "streamlit-demo-0.6.0",
    effective_hr_version: "effective-hr-raw-pass-through-v1",
    effective_hr_source: "raw_hr",
    parameter_version: 1,
  },
  data_quality: {
    history_reliability: 1,
    latest_activity_quality_score: 0.96,
    warnings: [],
  },
  zones: [
    { zone: "Z1", raw_time_min: 50.9, equivalent_time_min: 21.84882499999999, tref_min: 300.0, status_7_40: 1.0414434311238976, recovery_readiness_percent: 98.22964541950293, recovery_days_to_full: 0.0 },
    { zone: "Z2", raw_time_min: 0.0, equivalent_time_min: 0.0, tref_min: 180.0, status_7_40: 1.020515408803807, recovery_readiness_percent: 97.84170251489827, recovery_days_to_full: 0.0 },
    { zone: "Z3", raw_time_min: 0.0, equivalent_time_min: 0.0, tref_min: 70.0, status_7_40: 0.94851655323998, recovery_readiness_percent: 90.49300125839497, recovery_days_to_full: 0.8674969381798795 },
    { zone: "Z4", raw_time_min: 0.0, equivalent_time_min: 0.0, tref_min: 20.0, status_7_40: 0.9629033091221942, recovery_readiness_percent: 53.87736142712738, recovery_days_to_full: 3.6660788874004906 },
    { zone: "Z5", raw_time_min: 0.0, equivalent_time_min: 0.0, tref_min: 20.0, status_7_40: 0.8922692813680896, recovery_readiness_percent: 71.07073249428917, recovery_days_to_full: 3.510831773387006 },
  ],
};

const fixtureDates = Array.from({ length: 40 }, (_, index) => {
  const value = new Date(Date.UTC(2026, 4, 12 + index));
  return value.toISOString().slice(0, 10);
});
const fixtureBase = [40, 20, 8, 4, 2];

export const loadHistoryFixture: LoadHistory = {
  schema_version: "load-history-v1",
  athlete_id: "A",
  period_start: fixtureDates[0],
  period_end: fixtureDates.at(-1)!,
  quality: {
    processed_activities: 24,
    limited_activities: 1,
    excluded_activities: 0,
    no_activity_days: 16,
    warnings: [],
  },
  zones: trainingStatusFixture.zones.map((zone, index) => ({
    zone: zone.zone,
    e7_daily: fixtureBase[index] * (0.86 + index * 0.025),
    e40_daily: fixtureBase[index],
    status_7_40: zone.status_7_40,
    tref_min: zone.tref_min,
    history_reliability: 1,
  })),
  daily: fixtureDates.flatMap((day, dayIndex) => trainingStatusFixture.zones.map((zone, zoneIndex) => {
    const wave = 0.82 + 0.18 * Math.sin((dayIndex + zoneIndex * 2) / 5);
    const recent = dayIndex > 32 ? 0.82 + zoneIndex * 0.035 : 1;
    return {
      date: day,
      zone: zone.zone,
      effective_load: dayIndex % 3 === 0 ? fixtureBase[zoneIndex] * wave : 0,
      e7_daily: fixtureBase[zoneIndex] * wave * recent,
      e40_daily: fixtureBase[zoneIndex],
      status_7_40: 0.9 + 0.12 * Math.sin((dayIndex + zoneIndex) / 7) * recent,
    };
  })),
  activities: [
    {
      activity_ref: "activity-002",
      date: "2026-06-20",
      sport: "NordicSki",
      duration_min: 51,
      quality_status: "valid",
      hr_coverage_percent: 96,
      zones: trainingStatusFixture.zones.map((zone) => ({
        zone: zone.zone,
        raw_time_min: zone.raw_time_min,
        equivalent_time_min: zone.equivalent_time_min,
        effective_load: zone.equivalent_time_min,
        mean_effective_hr_bpm: null,
        average_minute_value_percent: zone.raw_time_min > 0 ? 42.9 : null,
      })),
    },
    {
      activity_ref: "activity-001",
      date: "2026-06-18",
      sport: "Run",
      duration_min: 42,
      quality_status: "limited",
      hr_coverage_percent: 88.4,
      zones: trainingStatusFixture.zones.map((zone, index) => ({
        zone: zone.zone,
        raw_time_min: index < 2 ? 21 : 0,
        equivalent_time_min: index === 0 ? 16.8 : index === 1 ? 15.3 : 0,
        effective_load: index === 0 ? 16.8 : index === 1 ? 15.3 : 0,
        mean_effective_hr_bpm: index === 0 ? 121 : index === 1 ? 138 : null,
        average_minute_value_percent: index === 0 ? 80 : index === 1 ? 72.9 : null,
      })),
    },
  ],
};

export const recoveryHistoryFixture: RecoveryHistory = {
  schema_version: "recovery-history-v1",
  athlete_id: "A",
  period_start: fixtureDates[0],
  period_end: fixtureDates.at(-1)!,
  basis: "load-only",
  wellness_freshness: "unknown",
  wellness_coverage_percent: 0,
  model: {
    algorithm_version: "main-load-recovery-v3-equivalent-time-fixed-tref",
    parameter_version: "main-load-recovery-v1",
    parameter_fingerprint: "fixture-recovery-parameters-v1",
    practical_full_recovery_percent: 95,
  },
  settings: trainingStatusFixture.zones.map((zone, index) => ({
    zone: zone.zone,
    tref_min: zone.tref_min,
    sensitivity: [0.55, 0.70, 0.88, 1.00, 1.12][index],
    tau_days: [0.75, 1.00, 1.35, 1.65, 2.00][index],
    fatigue_cap: [130, 135, 145, 155, 165][index],
  })),
  current: trainingStatusFixture.zones.map((zone) => ({
    zone: zone.zone,
    readiness_percent: zone.recovery_readiness_percent,
    residual_fatigue: 100 - zone.recovery_readiness_percent,
    days_to_practical_recovery: zone.recovery_days_to_full,
  })),
  daily: fixtureDates.flatMap((day, dayIndex) => trainingStatusFixture.zones.map((zone, zoneIndex) => {
    const impulse = dayIndex % 3 === 0 ? 5 + zoneIndex * 5 : 0;
    const readinessAfter = Math.max(0, Math.min(100, 94 - 12 * Math.sin((dayIndex + zoneIndex * 2) / 5) - impulse));
    return {
      date: day,
      zone: zone.zone,
      readiness_before_percent: Math.min(100, readinessAfter + impulse),
      readiness_after_percent: readinessAfter,
      residual_fatigue_after: 100 - readinessAfter,
      impulse,
      effective_load: loadHistoryFixture.daily[dayIndex * 5 + zoneIndex].effective_load,
      tref_min: zone.tref_min,
    };
  })),
};
