import type { RecoveryV2 } from "./models";

/** Display server-calculated estimates only with an explicit, complete provenance. */
export function recoveryDisplayState(history: RecoveryV2 | null | undefined) {
  const quality = history?.data_quality;
  const estimated = quality?.readiness_available === true
    && quality.readiness_basis === "ESTIMATED_LOAD"
    && quality.estimated === true
    && quality.missing_days === 0
    && quality.estimation?.supported === true
    && quality.estimation.unresolved_activity_count === 0
    && quality.estimation.missing_calendar_days.length === 0;
  const unavailable = quality?.readiness_available === false
    || (quality?.complete === false && !estimated);
  return { estimated, unavailable };
}
