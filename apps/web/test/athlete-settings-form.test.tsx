// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { AthleteSettingsForm } from "../components/athlete-settings-form";

const percentages = [50, 60, 70, 80, 90, 100]; // Synthetic test scheme, no product default.

describe("athlete HR zone setup", () => {
  it("prefills a full calendar year and resolves today in the athlete timezone", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.setSystemTime(new Date("2024-02-28T23:30:00Z"));
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      await act(async () => root.render(<AthleteSettingsForm editing initialTimezone="Europe/Sofia" />));
      const select = container.querySelector<HTMLSelectElement>('[name="hrmax_apply_mode"]')!;
      expect(select.options).toHaveLength(3);
      expect(select.value).toBe("LAST_40_DAYS");
      await act(async () => { select.value = "FROM_DATE"; select.dispatchEvent(new Event("change", { bubbles: true })); });
      const input = container.querySelector<HTMLInputElement>('input[type="date"]')!;
      expect([input.min, input.value, input.max]).toEqual(["2023-02-28", "2023-02-28", "2024-02-29"]);
      await act(async () => { select.value = "FROM_TODAY"; select.dispatchEvent(new Event("change", { bubbles: true })); });
      expect(container.querySelector('input[type="date"]')).toBeNull();
    } finally {
      await act(async () => root.unmount());
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });

  it("offers explicit retrospective scope and displays the saved effective date", () => {
    const html = renderToStaticMarkup(<AthleteSettingsForm editing initialHrmax={200} initialEffectiveFrom="2026-08-17" />);
    expect(html).toContain('name="hrmax_apply_mode"');
    expect(html).toContain('<option value="LAST_40_DAYS" selected=""');
    expect(html).toContain('<option value="FROM_TODAY"');
    expect(html).toContain('<option value="FROM_DATE"');
    expect(html).toContain('до 12 месеца назад');
    expect(html).not.toContain('LAST_90_DAYS');
    expect(html).toContain("17.08.2026");
    expect(html).toContain("Измерените данни остават непроменени");
    expect(renderToStaticMarkup(<AthleteSettingsForm />)).not.toContain('name="hrmax_apply_mode"');
  });
  it("exposes the common indicator, relative HR endpoints and Tanaka cold start", () => {
    const html=renderToStaticMarkup(<AthleteSettingsForm initialAge={40} initialHrmax={180}
      initialHrmaxSource="TANAKA" initialIndicator="SPEED" automaticPercentages={[45,72,79,89,94,100]}/>);
    expect(html).toContain("Начален HRmax по Tanaka: 180");
    expect(html).toContain('<option value="SPEED" selected=""');
    expect(html).toContain("81 / 130 / 142 / 160 / 169 / 180");
    expect(html).toContain("45% от максималния пулс");
  });
  it("offers automatic setup only with an expert scheme", () => {
    const manual = renderToStaticMarkup(<AthleteSettingsForm />);
    expect(manual).not.toContain('<option value="AUTOMATIC_HRMAX"');
    expect(manual).toContain("след задаване на експертната схема");
    const automatic = renderToStaticMarkup(<AthleteSettingsForm automaticPercentages={percentages} initialHrmax={200} />);
    expect(automatic).toContain('<option value="AUTOMATIC_HRMAX" selected=""');
    expect(automatic).toContain("100 / 120 / 140 / 160 / 180 / 200");
    expect(automatic).toContain("Начална оценка по експертна схема");
    expect(automatic).toContain('<fieldset disabled="" hidden=""');
  });

  it("keeps manual boundaries selected when an automatic scheme becomes available", () => {
    const html = renderToStaticMarkup(<AthleteSettingsForm editing initialSource="MANUAL" initialBounds={[100, 120, 140, 160, 180, 200]} initialHrmax={205} automaticPercentages={percentages} />);
    expect(html).toContain('<option value="MANUAL" selected=""');
    expect(html).not.toContain('<fieldset disabled=""');
    expect(html).toContain('name="z5_high"');
  });

  it("retains an automatic athlete's saved scheme when the deployment has no default", () => {
    const html = renderToStaticMarkup(<AthleteSettingsForm editing initialSource="AUTOMATIC_HRMAX" initialPercentages={percentages} initialHrmax={190} />);
    expect(html).toContain("95 / 114 / 133 / 152 / 171 / 190");
    expect(html).toContain('<option value="AUTOMATIC_HRMAX" selected=""');
  });
});
