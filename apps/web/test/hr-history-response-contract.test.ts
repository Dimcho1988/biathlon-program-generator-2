import { describe, expect, it } from "vitest";
import { loadHistoryFixture, trainingStatusFixture } from "../lib/fixture";
import { parseLoadHistory } from "../lib/load-history";
import { parseDashboardView } from "../lib/dashboard-view";

const version2 = {
  ...loadHistoryFixture,
  schema_version: "load-history-v2",
  tref_bounds_profile_version: "aerobic-tref-v1",
  daily: loadHistoryFixture.daily.map(row => ({ ...row, tref_used_min: 120 })),
  strength: loadHistoryFixture.strength ? { ...loadHistoryFixture.strength,
    daily: loadHistoryFixture.strength.daily.map(row => ({ ...row, tref_used_min: 90 })),
  } : null,
};

describe.each([loadHistoryFixture, version2])("HR calibration in $schema_version", history => {
  it.each([
    { hr_settings_revision: null, hr_calibration_history: [] },
    { hr_settings_revision: "calibration-revision", hr_calibration_history: [
      { from: "0001-01-01", hrmax_bpm: 200, zone_bounds_bpm: [100, 120, 140, 160, 180, 200],
        hr_zone_source: "MANUAL", hr_zone_percentages: null },
      { from: "2026-11-01", hrmax_bpm: 193, zone_bounds_bpm: [100, 120, 140, 160, 180, 193],
        hr_zone_source: "MANUAL", hr_zone_percentages: null },
    ] },
  ])("accepts API metadata through the complete dashboard ($hr_settings_revision)", metadata => {
    const load_history = { ...history, ...metadata };
    const response = { schema_version: "dashboard-view-v1", generation_id: "active-generation", revision: 1,
      analysis_as_of: history.period_end, activated_at: "2026-11-18T15:00:00Z",
      training_status: trainingStatusFixture, load_history,
      completed_work: null, recovery_history: null, volume_history: null };
    const parsed = parseDashboardView(JSON.parse(JSON.stringify(response)));
    expect(parsed.load_history).toMatchObject(load_history);
    expect(parsed.training_status).toEqual(trainingStatusFixture);
  });

  it("retains compatibility with older snapshots without calibration metadata", () => {
    expect(parseLoadHistory(history)).toMatchObject(history);
  });

  it.each([
    { hr_settings_revision: 7 }, { hr_settings_revision: "" }, { hr_settings_revision: "  " },
    { hr_calibration_history: null }, { hr_calibration_history: {} },
    { hr_calibration_history: [null] }, { hr_calibration_history: [193] },
    { hr_calibration_history: [[]] },
  ])("rejects malformed calibration metadata: %j", metadata => {
    expect(() => parseLoadHistory({ ...history, ...metadata })).toThrow();
  });

  it("still rejects unknown root fields alongside valid calibration metadata", () => {
    expect(() => parseLoadHistory({ ...history, hr_settings_revision: null,
      hr_calibration_history: [], unexpected: true })).toThrow(/структура/);
  });
});
