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
    { zone: "Z1", raw_time_min: 50.9, equivalent_time_min: 21.84882499999999, tref_min: 300.0, status_7_40: 1.0414434311238976, recovery_readiness_percent: 98.22964541950293, recovery_days_to_full: 0.0 },
    { zone: "Z2", raw_time_min: 0.0, equivalent_time_min: 0.0, tref_min: 180.0, status_7_40: 1.020515408803807, recovery_readiness_percent: 97.84170251489827, recovery_days_to_full: 0.0 },
    { zone: "Z3", raw_time_min: 0.0, equivalent_time_min: 0.0, tref_min: 70.0, status_7_40: 0.94851655323998, recovery_readiness_percent: 90.49300125839497, recovery_days_to_full: 0.8674969381798795 },
    { zone: "Z4", raw_time_min: 0.0, equivalent_time_min: 0.0, tref_min: 20.0, status_7_40: 0.9629033091221942, recovery_readiness_percent: 53.87736142712738, recovery_days_to_full: 3.6660788874004906 },
    { zone: "Z5", raw_time_min: 0.0, equivalent_time_min: 0.0, tref_min: 20.0, status_7_40: 0.8922692813680896, recovery_readiness_percent: 71.07073249428917, recovery_days_to_full: 3.510831773387006 },
  ],
};
