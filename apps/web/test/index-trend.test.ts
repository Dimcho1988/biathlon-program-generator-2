import { expect, it } from "vitest";
import { indexTrend } from "../lib/trainability";
import { trainabilityFixture } from "../lib/trainability-fixture";

it("uses a trailing calendar window without future, invalid or incompatible observations", () => {
  const rows = [1, 3, 4, 5, 6, 7, 8, 20].map((day, i) => {
    const row = structuredClone(trainabilityFixture.activities[0]);
    row.activity_ref = `row-${i}`;
    row.local_date = `2026-09-${String(day).padStart(2, "0")}`;
    row.start_at_utc = `${row.local_date}T10:00:00Z`;
    row.index!.general.index = i === 0 ? 4 : i === 1 ? 6 : 100;
    return row;
  });
  rows[2].index!.general.valid = false;
  rows[3].sport = "Ride";
  rows[4].index!.comparison_key = "different";
  rows[5].index!.signal_quality.status = "EXCLUDED";
  rows[6].index!.general.index = 8;
  const trend = indexTrend(rows, "GENERAL", 7);
  expect(trend[0].value).toBeNull();
  expect(trend[1].value).toBe(5);
  expect(trend[2].value).toBeNull();
  expect(trend[5].value).toBeNull();
  expect(trend[6].value).toBe(7); // Sep 1 is outside the seven-day calendar window.
  expect(trend[6].count).toBe(2);
  expect(trend[7].value).toBeNull(); // Long gap does not carry an old average forward.
  expect(indexTrend([...rows].reverse(), "GENERAL", 7)).toEqual(trend);
});
