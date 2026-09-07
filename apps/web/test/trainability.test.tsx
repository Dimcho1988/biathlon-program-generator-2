import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { trainabilityFixture } from "../lib/trainability-fixture";
import { lineSegments, parseTrainabilityHistory } from "../lib/trainability";
import { TrainabilitySummary } from "../components/trainability-summary";
import { TrainabilityHistoryView } from "../components/trainability-history";

describe("trainability", () => {
  it("validates the stored contract and rejects fabricated values for short activities", () => {
    expect(parseTrainabilityHistory(trainabilityFixture).activities).toHaveLength(12);
    const malformed = structuredClone(trainabilityFixture);
    malformed.activities[5].index!.general.valid = true;
    malformed.activities[5].index!.general.index = 7;
    malformed.activities[5].index!.general.invalid_reason = null;
    expect(() => parseTrainabilityHistory(malformed)).toThrow();
  });
  it("breaks lines at invalid activities and configuration changes", () => {
    const rows = structuredClone(trainabilityFixture.activities);
    expect(lineSegments(rows, "GENERAL").map(s => s.length)).toEqual([5, 6]);
    rows[8].index!.comparison_key = "different-model";
    expect(lineSegments(rows, "GENERAL").map(s => s.length)).toEqual([5, 2, 1, 3]);
  });
  it("shows missing values and short-activity reasons rather than zeros", () => {
    const html = renderToStaticMarkup(<TrainabilitySummary index={trainabilityFixture.activities[5].index} />);
    expect(html).toContain("Активност под 7 мин");
    expect(html).toContain("—");
    expect(html).toContain("Скоростно време");
    expect(html).toContain("Общ · 75–92%");
  });
  it("separates sports and offers all six lines with accessible activity selection", () => {
    const html = renderToStaticMarkup(<TrainabilityHistoryView history={trainabilityFixture} />);
    expect(html).toContain("Вид спорт");
    for (const name of ["Z1", "Z2", "Z3", "Z4", "Z5", "Общ · 75–92%"])
      expect(html).toContain(name);
    expect(html).toContain('role="button"');
  });
});
