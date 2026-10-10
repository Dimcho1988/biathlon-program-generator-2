import { describe, expect, it } from "vitest";
import type { RecoveryV2 } from "../lib/models";
import { dateAtOffset, recoveryHistorySegments, recoveryForecastPoints, forecastReadiness, stepRecoveryCursor } from "../lib/recovery-timeline";

function history(): RecoveryV2 {
  return {
    as_of: "2026-09-12", source_stale: false,
    current: [{ zone: "Z2", readiness_percent: 79 }, { zone: "Z5", readiness_percent: 80 }],
    daily: [
      { zone: "Z5", date: "2026-09-06", readiness_after_percent: 20 },
      { zone: "Z5", date: "2026-09-07", readiness_after_percent: 30 },
      { zone: "Z5", date: "2026-09-08", readiness_after_percent: 40 },
      { zone: "Z5", date: "2026-09-10", readiness_after_percent: 60 },
      { zone: "Z5", date: "2026-09-11", readiness_after_percent: 70 },
      { zone: "Z5", date: "2026-09-12", readiness_after_percent: 80 },
    ],
    forecast: [
      { zone: "Z2", days: 0, readiness_percent: 79 },
      { zone: "Z2", days: .5, readiness_percent: 90 },
      { zone: "Z2", days: 1, readiness_percent: 96 },
      { zone: "Z5", days: 0, readiness_percent: 80 },
      { zone: "Z5", days: 1, readiness_percent: 83 },
      { zone: "Z5", days: 3, readiness_percent: 87 },
      { zone: "Z5", days: 5.62, readiness_percent: 90 },
    ],
  } as RecoveryV2;
}

describe("shared recovery timeline", () => {
  it("keeps Z2's short deadline independent from Z5 and never stretches short forecasts", () => {
    const h = history(), z2 = recoveryForecastPoints(h, "Z2"), z5 = recoveryForecastPoints(h, "Z5");
    expect(forecastReadiness(z2, .5)).toBe(90);
    expect(forecastReadiness(z5, .5)).toBe(81.5);
    expect(z2.at(-1)?.day).toBe(1);
    expect(forecastReadiness(z2, 2)).toBeNull(); // old API: no invented continuation
    expect(z5.at(-1)).toEqual({ day: 2, readiness: 85 });
  });
  it("shows five previous calendar days, shares today's card value and breaks missing days", () => {
    const h = history();
    expect(recoveryHistorySegments(h, "Z5").map(segment => segment.map(p => p.day)))
      .toEqual([[-5, -4], [-2, -1, 0]]);
    expect(recoveryHistorySegments(h, "Z5").at(-1)?.at(-1)?.readiness).toBe(80);
    h.source_stale = true;
    expect(recoveryHistorySegments(h, "Z5").at(-1)?.at(-1)?.day).toBe(-1);
    expect(recoveryForecastPoints(h, "Z5")[0]).toEqual({ day: 0, readiness: 80 });
  });
  it("places date labels on fixed calendar days across daylight-saving changes", () => {
    expect(dateAtOffset("2026-10-25", -5)).toBe("2026-10-20");
    expect(dateAtOffset("2026-10-25", 2)).toBe("2026-10-27");
  });
  it("lets the keyboard cross today into daily history and hourly forecasts without getting stuck", () => {
    expect(stepRecoveryCursor(0, -1)).toBe(-1);
    expect(stepRecoveryCursor(-1, 1)).toBe(0);
    expect(stepRecoveryCursor(0, 1)).toBe(1 / 24);
    expect(stepRecoveryCursor(1 / 24, -1)).toBe(0);
    expect(stepRecoveryCursor(-5, -1)).toBe(-5);
    expect(stepRecoveryCursor(2, 1)).toBe(2);
  });
});
