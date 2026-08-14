import type { TrainingStatus } from "./training-status";

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
    { zone: "Z1", raw_time_min: 38.4, equivalent_time_min: 38.4, tref_min: 300, status_7_40: 0.87, recovery_readiness_percent: 92.3, recovery_days_to_full: 0.4 },
    { zone: "Z2", raw_time_min: 27.2, equivalent_time_min: 30.6, tref_min: 180, status_7_40: 1.04, recovery_readiness_percent: 84.7, recovery_days_to_full: 0.9 },
    { zone: "Z3", raw_time_min: 12.8, equivalent_time_min: 18.1, tref_min: 70, status_7_40: 0.76, recovery_readiness_percent: 77.6, recovery_days_to_full: 1.3 },
    { zone: "Z4", raw_time_min: 4.6, equivalent_time_min: 9.2, tref_min: 20, status_7_40: 0.58, recovery_readiness_percent: 69.8, recovery_days_to_full: 1.8 },
    { zone: "Z5", raw_time_min: 1.4, equivalent_time_min: 4.9, tref_min: 20, status_7_40: 0.32, recovery_readiness_percent: 81.2, recovery_days_to_full: 1.1 },
  ],
};
