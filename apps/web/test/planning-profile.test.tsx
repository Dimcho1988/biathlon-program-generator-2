import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PlanningProfileForm } from "../components/planning-profile-form";
import {
  getAthletePlanningProfile,
  getMesocycleAccentPreferences,
  getPlanningMethodology,
} from "../lib/api";
import {
  PLANNING_PROFILE_RESAVE_MESSAGE,
  parseMesocycleAccentPreferencesResponse,
  parsePlanningMethodology,
  parsePlanningProfileResponse,
  type MesocycleAccentPreferencesResponse,
  type PlanningMethodology,
  type PlanningProfile,
} from "../lib/planning-profile";
import type { PlanningCalendarResponse } from "../lib/planning-calendar";
import planningProfileContract from "./fixtures/planning-profile-response-v1.json";

const methodology: PlanningMethodology = {
  schema_version: "planning-methodology-v1",
  methodology_id: "onflows-canonical",
  methodology_version: "onflows-canonical-v1",
  source_scope: "BUILT_IN",
  mesocycle_pattern: [0.96, 1.04, 1.10, 0.78],
  supported_accent_modes: ["AUTO", "MANUAL", "HYBRID"],
  accent_components: ["Z1", "Z2", "Z3", "Z4", "Z5", "STR"],
  default_accent_limit: 2,
  maximum_accent_limit: 6,
  hybrid_rule: "manual-first-auto-fill",
  stress_mesocycle: {
    status: "DESIGNED_NOT_ACTIVE",
    automatic_enabled: false,
    manual_dose_required: true,
    selected_accents_only: true,
    mandatory_recovery: true,
    affects_canonical_result: false,
  },
};

const profile: PlanningProfile = {
  schema_version: "planning-profile-v1",
  season_start: "2026-01-01",
  season_end: "2026-12-31",
  annual_target_hours: 600,
  sessions_per_week: 8,
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

const unconfiguredAccents: MesocycleAccentPreferencesResponse = {
  configured: false,
  preferences: null,
  resolution: null,
};

const accentPreferences: MesocycleAccentPreferencesResponse = {
  configured: true,
  preferences: {
    schema_version: "mesocycle-accent-preferences-v1",
    accent_mode: "HYBRID",
    accent_limit: 3,
    manual_components: ["Z5"],
  },
  resolution: {
    methodology_version: "onflows-canonical-v1",
    fixed_components: ["Z5"],
    automatic_slots: 2,
    resolution_stage: "PLAN_GENERATION",
  },
};

const planningCalendar: PlanningCalendarResponse = {
  configured: false,
  calendar: null,
  context: {
    schema_version: "planning-context-v1",
    as_of: "2026-08-19",
    ready_for_generation: false,
    generator_status: "NOT_ACTIVE",
    missing_inputs: ["FUTURE_MAIN_RACE", "TRAINING_SNAPSHOT"],
    next_main_race: null,
    methodology_version: "onflows-canonical-v1",
    recovery_basis: "LOAD_ONLY",
    wellness_integration: "DIAGNOSTIC_ONLY",
  },
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
    })).toThrow(PLANNING_PROFILE_RESAVE_MESSAGE);
  });

  it("accepts the shared Python/TypeScript planning contract fixture", () => {
    expect(parsePlanningProfileResponse(planningProfileContract)).toEqual(planningProfileContract);
  });

  it("fails closed for impossible stored weekly capacity and explains re-save", () => {
    const impossibleProfiles = [
      { ...profile, sessions_per_week: 9 },
      { ...profile, rest_days: [0, 2] },
      { ...profile, double_threshold_enabled: true, double_threshold_day: 4 },
    ];
    for (const invalidProfile of impossibleProfiles)
      expect(() => parsePlanningProfileResponse({ configured: true, profile: invalidProfile }))
        .toThrow(PLANNING_PROFILE_RESAVE_MESSAGE);
  });

  it("preserves an explicitly unconfigured profile without defaults", () => {
    expect(parsePlanningProfileResponse({ configured: false, profile: null })).toEqual({ configured: false, profile: null });
    const html = renderToStaticMarkup(<PlanningProfileForm
      profile={null}
      methodology={methodology}
      accentPreferences={unconfiguredAccents}
      planningCalendar={planningCalendar}
    />);
    expect(html).toContain("Стойностите не се предполагат автоматично");
    expect(html).not.toContain('name="annual_target_hours" type="number" min="50" max="1500" step="1" value=');
  });

  it("renders all individual inputs but no shared scientific coefficients", () => {
    const html = renderToStaticMarkup(<PlanningProfileForm
      profile={profile}
      methodology={methodology}
      accentPreferences={accentPreferences}
      planningCalendar={planningCalendar}
    />);
    for (const label of [
      "Сезонна цел",
      "Седмична структура",
      "Мезоцикъл",
      "Двойна прагова тренировка",
      "Целеви обем",
      "Дни за пълна почивка",
    ]) expect(html).toContain(label);
    expect(html).toContain('action="/api/athlete/planning-profile"');
    expect(html).toContain("още една само за всеки изрично избран двоен ден");
    expect(html).toContain("трябва да е избран и като ден с две сесии");
    expect(html).not.toContain("annual_goal_influence");
    expect(html).not.toContain("double_threshold_min_readiness");
    expect(html).not.toContain("between_sessions_recovery_days");
  });

  it("shows the immutable methodology and inactive stress policy", () => {
    expect(parsePlanningMethodology(methodology)).toEqual(methodology);
    expect(() => parsePlanningMethodology({
      ...methodology,
      stress_mesocycle: {
        ...methodology.stress_mesocycle,
        affects_canonical_result: true,
      },
    })).toThrow(/методология/);

    const html = renderToStaticMarkup(<PlanningProfileForm
      profile={profile}
      methodology={methodology}
      accentPreferences={accentPreferences}
      planningCalendar={planningCalendar}
    />);

    expect(html).toContain("onflows-canonical-v1");
    expect(html).toContain("96% · 104% · 110% · 78%");
    expect(html).toContain("AUTO · MANUAL · HYBRID");
    expect(html).toContain("неактивен до одобрена доза");
  });

  it("renders and validates the stored hybrid accent resolution without guessing components", () => {
    expect(parseMesocycleAccentPreferencesResponse(accentPreferences)).toEqual(accentPreferences);
    expect(() => parseMesocycleAccentPreferencesResponse({
      ...accentPreferences,
      resolution: {
        ...accentPreferences.resolution,
        automatic_slots: 1,
      },
    })).toThrow(/разрешаване/);

    const html = renderToStaticMarkup(<PlanningProfileForm
      profile={profile}
      methodology={methodology}
      accentPreferences={accentPreferences}
      planningCalendar={planningCalendar}
    />);

    expect(html).toContain('action="/api/athlete/mesocycle-accents"');
    expect(html).toContain("Z5 · ръчно");
    expect(html).toContain("AUTO 1");
    expect(html).toContain("AUTO 2");
    expect(html).toContain("STRESS остава неактивен");
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

  it("loads the shared methodology through the protected endpoint", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json(methodology));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getPlanningMethodology("ath-profile")).resolves.toEqual(methodology);

    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toBe("https://api.example.test/api/v2/planning/methodology");
    expect(init.headers.Authorization).toBe("Bearer server-secret");
    expect(init.headers["X-OnFlows-Athlete-Alias"]).toBe("ath-profile");
  });

  it("loads athlete-scoped mesocycle accents through the protected endpoint", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json(accentPreferences));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getMesocycleAccentPreferences("ath-profile"))
      .resolves.toEqual(accentPreferences);

    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toBe(
      "https://api.example.test/api/v2/athlete/mesocycle-accent-preferences",
    );
    expect(init.headers.Authorization).toBe("Bearer server-secret");
    expect(init.headers["X-OnFlows-Athlete-Alias"]).toBe("ath-profile");
  });
});
