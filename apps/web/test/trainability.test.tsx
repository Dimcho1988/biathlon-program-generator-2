import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { trainabilityFixture } from "../lib/trainability-fixture";
import { lineSegments, parseTrainabilityHistory, parseTrainabilityIndex } from "../lib/trainability";
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
  it("does not render legacy bpm indices on the normalized scale during rollout", () => {
    const mixed = structuredClone(trainabilityFixture) as unknown as { activities: { index: unknown }[] };
    mixed.activities[4].index = { schema_version: "trainability-index-v1", model_version: "trainability_rank_v1", general: { index: 8.5 } };
    const parsed = parseTrainabilityHistory(mixed);
    expect(parsed.activities[4].index).toBeNull();
    expect(parsed.activities[4].unavailable_reason).toBe("REFRESH_REQUIRED");
    expect(lineSegments(parsed.activities, "GENERAL").map(s => s.length)).toEqual([4, 6]);
    const html = renderToStaticMarkup(<TrainabilitySummary index={parsed.activities[4].index} />);
    expect(html).toContain("Обнови данните");
  });
  it.each(["Z1", "Z2", "Z3", "Z4", "Z5", "GENERAL"])("enforces both cumulative minima for %s", name => {
    for (const field of ["hr_seconds", "speed_seconds"] as const) {
      const index = structuredClone(trainabilityFixture.activities[0].index!);
      const band = name === "GENERAL" ? index.general : index.zones.find(b => b.name === name)!;
      band[field] = band.minimum_seconds;
      expect(parseTrainabilityIndex(index)).not.toBeNull();
      band[field] -= 0.001;
      expect(() => parseTrainabilityIndex(index)).toThrow();
    }
  });
  it("rejects an old ratio or a weakened minimum disguised as the new contract", () => {
    const wrongScale = structuredClone(trainabilityFixture.activities[0].index!);
    wrongScale.general.index = wrongScale.general.mean_hrmod_bpm! / wrongScale.general.mean_vflat_kmh!;
    expect(() => parseTrainabilityIndex(wrongScale)).toThrow();
    const wrongMinimum = structuredClone(trainabilityFixture.activities[0].index!);
    wrongMinimum.zones[4].minimum_seconds = 60;
    expect(() => parseTrainabilityIndex(wrongMinimum)).toThrow();
  });
  it("shows the specific five or seven minute reason and the percentage formula", () => {
    const index = structuredClone(trainabilityFixture.activities[0].index!);
    for (const band of [index.zones[0], index.zones[4]]) {
      band.valid = false; band.index = null;
      band.hr_seconds = band.minimum_seconds - 1;
      band.invalid_reason = "HR_TIME_BELOW_MINIMUM";
    }
    const html = renderToStaticMarkup(<TrainabilitySummary index={parseTrainabilityIndex(index)} />);
    expect(html).toContain("Под 7 мин HRmod");
    expect(html).toContain("Под 5 мин HRmod");
    expect(html).toContain("HRmod (%HRmax) / Vflat (km/h)");
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
