import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { AthleteSettingsForm } from "../components/athlete-settings-form";

const percentages = [50, 60, 70, 80, 90, 100]; // Synthetic test scheme, no product default.

describe("athlete HR zone setup", () => {
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
