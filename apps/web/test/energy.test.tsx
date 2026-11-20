import { describe, it, expect, vi } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { parseEnergy, type EnergyHistory } from "../lib/energy";
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: React.ComponentProps<"a">) => (
    <a {...props}>{children}</a>
  ),
}));
import { EnergyDashboard } from "../components/energy-dashboard";
const zero = {
  kcal: 0,
  carbohydrate_kcal: 0,
  fat_kcal: 0,
  protein_kcal: 0,
  carbohydrate_g: 0,
  fat_g: 0,
  protein_g: 0,
};
const data: EnergyHistory = {
  schema_version: "energy-constrained-cubic-v1",
  period_start: "2026-11-14",
  period_end: "2026-11-14",
  today: "2026-11-19",
  timezone: "Europe/Vienna",
  profile: null,
  profile_revision: 0,
  profiles: [],
  assumptions: ["Оценка"],
  references: [],
  days: [
    {
      day: "2026-11-14",
      activities: [],
      weight_kg: null,
      activity: zero,
      outside: null,
      total: null,
      accounted: zero,
      coverage_percent: null,
      status: "PARTIAL",
      reason: "Липсват данни",
      resting_basis: "MISSING_PROFILE",
      targets: null,
    },
  ],
};
describe("energy review", () => {
  it("does not display incomplete totals as zero or offer calorie targets", () => {
    const html = renderToStaticMarkup(
      <EnergyDashboard data={data} canEdit={false} />,
    );
    expect(html).toContain("Няма пълна дневна оценка");
    expect(html).not.toContain("Ориентир за прием");
    expect(html).toContain("disabled");
    expect(html).toContain("/assistant?energy_date=2026-11-14");
  });
  it("rejects incompatible report shapes", () => {
    expect(() => parseEnergy({ days: [] })).toThrow();
    expect(parseEnergy(data)).toBe(data);
  });
  it("shows daily partial calories and all macro grams without promoting them to a full target", () => {
    const accounted = { kcal: 2400, carbohydrate_kcal: 1200, fat_kcal: 900, protein_kcal: 300,
      carbohydrate_g: 300, fat_g: 100, protein_g: 75 };
    const day = { ...data.days[0], accounted, outside: accounted };
    const html = renderToStaticMarkup(<EnergyDashboard canEdit={false} data={{ ...data, days: [
      day, { ...day, day: "2026-11-15" },
    ] }} />);
    expect(html).toContain("Дневни суми по макронутриенти");
    expect(html).toContain("2026-11-14");
    expect(html).toContain("2026-11-15");
    expect(html).toContain("Частична сума");
    expect(html).toContain((2400).toLocaleString("bg-BG"));
    expect(html).toContain(`300 / ${(1200).toLocaleString("bg-BG")}`);
    expect(html).toContain("100 / 900");
    expect(html).toContain("75 / 300");
    expect(html).not.toContain("Ориентир за прием");
  });
  it("labels strength calories and substrate assumptions rather than zero coverage", () => {
    const activity = { ...zero, kcal: 260, carbohydrate_kcal: 124, fat_kcal: 124, protein_kcal: 12,
      carbohydrate_g: 31, fat_g: 124 / 9, protein_g: 3 };
    const day = { ...data.days[0], activity, accounted: activity, estimated_activity_seconds: 3600,
      activities: [{ ...activity, activity_ref: "strength", sport: "WeightTraining", duration_s: 3600,
        coverage_percent: 0, timeline: [], method: "STRENGTH_MET" as const, met: 3.5, above_rest_kcal: 190 }] };
    const html = renderToStaticMarkup(<EnergyDashboard canEdit={false} data={{ ...data, days: [day] }} />);
    expect(html).toContain("Силова тренировка");
    expect(html).toContain("приблизително по 3,5 MET");
    expect(html).toContain("260");
    expect(html).toContain("Над личния разход в покой за това време: 190 kcal");
    expect(html).toContain("Разпределението е допускане");
    expect(html).toContain("Въглехидрати: 31 g / 124 kcal");
    expect(html).not.toContain("покритие 0%");
  });
});
