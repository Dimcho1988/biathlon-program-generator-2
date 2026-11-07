import { expect, it } from "vitest";
import { combinedIndexTrend } from "../lib/trainability";
import { trainabilityFixture } from "../lib/trainability-fixture";

function activity(day: number, general: number, zone: number) {
  const row = structuredClone(trainabilityFixture.activities[0]);
  row.activity_ref = `row-${day}`;
  row.local_date = `2026-09-${String(day).padStart(2, "0")}`;
  row.start_at_utc = `${row.local_date}T10:00:00Z`;
  row.index!.general = { ...row.index!.general, valid: true, index: general };
  row.index!.zones[0] = { ...row.index!.zones[0], valid: true, index: zone };
  return row;
}
it("creates one smooth mean from all enabled points with equal weight at equal distance", () => {
  const rows = [activity(1, 4, 8), activity(15, 6, 10)];
  const trend = combinedIndexTrend(rows, ["GENERAL", "Z1"], 14);
  const middle = trend[128];
  expect(middle.value).toBeCloseTo(7);
  expect(middle.count).toBe(4);
  expect(trend.every(p => p.value! >= 6 && p.value! <= 8)).toBe(true);
  expect(Math.max(...trend.slice(1).map((p, i) => Math.abs(p.value! - trend[i].value!)))).toBeLessThan(.01);
  const general = combinedIndexTrend(rows, ["GENERAL"], 14);
  expect(general[128].value).toBeCloseTo(5);
  expect(combinedIndexTrend([...rows].reverse(), ["GENERAL", "Z1"], 14)).toEqual(trend);
  expect(combinedIndexTrend(rows, [], 14)).toEqual([]);
});
it("excludes invalid, rejected and incompatible observations and leaves long gaps", () => {
  const rows = [activity(1, 4, 8), activity(29, 4, 8)];
  const invalid = activity(5, 100, 100); invalid.index!.general.valid = false;
  const rejected = activity(6, 100, 100); rejected.index!.signal_quality.status = "EXCLUDED";
  const otherSport = activity(7, 100, 100); otherSport.sport = "Other";
  const otherSettings = activity(8, 100, 100); otherSettings.index!.comparison_key = "different";
  const trend = combinedIndexTrend([...rows, invalid, rejected, otherSport, otherSettings], ["GENERAL"], 7);
  expect(trend.filter(p => p.value !== null).every(p => p.value === 4 && p.count === 2)).toBe(true);
  expect(trend.some(p => p.value === null)).toBe(true);
  expect(combinedIndexTrend([rows[0]], ["GENERAL", "Z1"], 14)).toEqual([]);
});
