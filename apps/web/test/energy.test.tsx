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
});
