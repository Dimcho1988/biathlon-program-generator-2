import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PlanningProfileForm } from "../components/planning-profile-form";
import { getAthletePlanningProfile } from "../lib/api";
import { parsePlanningProfileResponse, type PlanningProfile } from "../lib/planning-profile";

const profile: PlanningProfile = {
  schema_version: "planning-profile-v1",
  season_start: "2026-01-01",
  season_end: "2026-12-31",
  annual_target_hours: 600,
  sessions_per_week: 9,
  rest_days: [0],
  double_session_days: [2, 5],
  long_session_day: 6,
  intensity_days: [2, 5],
  strength_days: [1, 4],
  max_key_sessions_per_week: 3,
  mesocycle_anchor_date: "2026-01-01",
  mesocycle_length_weeks: 4,
  camp_default_accent_limit: 2,
  double_threshold_enabled: false,
  double_threshold_day: 2,
  double_threshold_components: ["Z3", "Z4"],
};

describe("planning-profile-v1 contract", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.ONFLOWS_API_BASE_URL;
    delete process.env.ONFLOWS_SERVICE_TOKEN;
  });

  it("accepts only the individual, versioned planning inputs", () => {
    expect(parsePlanningProfileResponse({ configured: true, profile })).toEqual({ configured: true, profile });
    expect(() => parsePlanningProfileResponse({
      configured: true,
      profile: { ...profile, annual_goal_influence: 0.4 },
    })).toThrow(/структура/);
    expect(() => parsePlanningProfileResponse({
      configured: true,
      profile: { ...profile, sessions_per_week: 13, rest_days: [0] },
    })).toThrow(/стойности/);
  });

  it("preserves an explicitly unconfigured profile without defaults", () => {
    expect(parsePlanningProfileResponse({ configured: false, profile: null })).toEqual({ configured: false, profile: null });
    const html = renderToStaticMarkup(<PlanningProfileForm profile={null} />);
    expect(html).toContain("Стойностите не се предполагат автоматично");
    expect(html).not.toContain('name="annual_target_hours" type="number" min="50" max="1500" step="1" value=');
  });

  it("renders all individual inputs but no shared scientific coefficients", () => {
    const html = renderToStaticMarkup(<PlanningProfileForm profile={profile} />);
    for (const label of [
      "Сезонна цел",
      "Седмична структура",
      "Мезоцикъл",
      "Двойна прагова тренировка",
      "Целеви обем",
      "Дни за пълна почивка",
    ]) expect(html).toContain(label);
    expect(html).toContain('action="/api/athlete/planning-profile"');
    expect(html).not.toContain("annual_goal_influence");
    expect(html).not.toContain("double_threshold_min_readiness");
    expect(html).not.toContain("between_sessions_recovery_days");
  });

  it("loads the profile through server-only authentication for one athlete alias", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json({ configured: true, profile }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getAthletePlanningProfile("ath-profile")).resolves.toEqual({ configured: true, profile });

    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toBe("https://api.example.test/api/v2/athlete/planning-profile");
    expect(init.headers.Authorization).toBe("Bearer server-secret");
    expect(init.headers["X-OnFlows-Athlete-Alias"]).toBe("ath-profile");
  });
});
