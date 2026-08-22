import { afterEach, describe, expect, it, vi } from "vitest";
import { cookies } from "next/headers";
import { GET as connect } from "../app/api/integrations/intervals/connect/route";
import { GET as refreshFromNavigation, POST as refresh } from "../app/api/integrations/intervals/refresh/route";
import { POST as saveSettings } from "../app/api/athlete/settings/route";
import { POST as savePlanningProfile } from "../app/api/athlete/planning-profile/route";
import { POST as saveMesocycleAccents } from "../app/api/athlete/mesocycle-accents/route";
import { POST as savePlanningCalendar } from "../app/api/athlete/planning-calendar/route";
import { GET as complete } from "../app/api/session/complete/route";
import { createAthleteSession, verifyAthleteSession } from "../lib/athlete-session";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));

describe("integration route redirects behind a reverse proxy", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    delete process.env.ONFLOWS_API_BASE_URL;
    delete process.env.ONFLOWS_SERVICE_TOKEN;
    delete process.env.ONFLOWS_PROFILE_MODE;
    delete process.env.ONFLOWS_SESSION_SECRET;
  });

  it("routes the browser through the API wake endpoint before OAuth", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await connect(new Request("https://web.example.test/api/integrations/intervals/connect"));

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("https://api.example.test/api/v2/wake?resume=connect");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns a relative dashboard redirect when API readiness fails", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await connect(new Request("https://web.example.test/api/integrations/intervals/connect?wake=ready"));

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/?intervals=connect-start-error");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("waits for the preview API health check to become ready before retrying OAuth", async () => {
    vi.useFakeTimers();
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const authorizationUrl = "https://intervals.icu/oauth/authorize?state=opaque";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json({ authorization_url: authorizationUrl }));
    vi.stubGlobal("fetch", fetchMock);

    const responsePromise = connect(new Request("https://web.example.test/api/integrations/intervals/connect?wake=ready"));
    await vi.advanceTimersByTimeAsync(3_000);
    const response = await responsePromise;

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(authorizationUrl);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(String(fetchMock.mock.calls[0][0])).toBe("https://api.example.test/health");
    expect(String(fetchMock.mock.calls[1][0])).toBe("https://api.example.test/health");
  });

  it("starts OAuth only after the preview API is healthy", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const authorizationUrl = "https://intervals.icu/oauth/authorize?state=opaque";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json({ authorization_url: authorizationUrl }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await connect(new Request("https://web.example.test/api/integrations/intervals/connect?wake=ready"));

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(authorizationUrl);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toBe("https://api.example.test/health");
    const [, authorizationRequest] = fetchMock.mock.calls[1];
    expect(authorizationRequest.headers).not.toHaveProperty("X-OnFlows-Athlete-Alias");
  });

  it("rechecks readiness and retries OAuth after a transient gateway response", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const authorizationUrl = "https://intervals.icu/oauth/authorize?state=opaque";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(new Response(null, { status: 502 }))
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json({ authorization_url: authorizationUrl }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await connect(new Request("https://web.example.test/api/integrations/intervals/connect?wake=ready"));

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(authorizationUrl);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("returns relative dashboard redirects after refresh success and failure", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(new Response(null, { status: 202 }))
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(new Response(null, { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);

    expect((await refresh()).headers.get("location")).toBe("/");
    expect((await refresh()).headers.get("location")).toBe("/?intervals=refresh-error");
  });

  it("handles a browser navigation to refresh without exposing a 405 page", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(new Response(null, { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await refreshFromNavigation();

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "POST" });
  });

  it("signs, validates and expires opaque athlete sessions", () => {
    process.env.ONFLOWS_SESSION_SECRET = "a-secret-value-with-at-least-32-characters";
    const now = 1_700_000_000_000;
    const token = createAthleteSession("ath-test-profile", now);
    expect(verifyAthleteSession(token, now)).toBe("ath-test-profile");
    expect(verifyAthleteSession(`${token}x`, now)).toBeNull();
    expect(verifyAthleteSession(token, now + 31 * 24 * 60 * 60 * 1000)).toBeNull();
  });

  it("exchanges a one-time callback ticket and sets an HttpOnly session", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    process.env.ONFLOWS_SESSION_SECRET = "a-secret-value-with-at-least-32-characters";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json({ athlete_alias: "ath-test-profile" }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await complete(new Request(`https://web.example.test/api/session/complete?ticket=${"t".repeat(40)}`));

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/?intervals=connected");
    expect(response.headers.get("set-cookie")).toContain("onflows-athlete-session=");
    expect(response.headers.get("set-cookie")).toContain("HttpOnly");
    expect(response.headers.get("set-cookie")).toContain("Secure");
    const [, init] = fetchMock.mock.calls[1];
    expect(JSON.parse(init.body)).toEqual({ ticket: "t".repeat(40) });
  });

  it("distinguishes a failed session handoff from an OAuth start failure", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(new Response(null, { status: 401 })));

    const response = await complete(new Request(`https://web.example.test/api/session/complete?ticket=${"t".repeat(40)}`));

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/?intervals=session-error");
  });

  it("saves physiological inputs only for the signed athlete session", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    process.env.ONFLOWS_PROFILE_MODE = "multi";
    process.env.ONFLOWS_SESSION_SECRET = "a-secret-value-with-at-least-32-characters";
    const session = createAthleteSession("ath-test-profile");
    vi.mocked(cookies).mockResolvedValue({ get: () => ({ value: session }) } as never);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json({ configured: true }));
    vi.stubGlobal("fetch", fetchMock);
    const form = new FormData();
    [100, 120, 140, 160, 180, 200].forEach((value, index) =>
      form.set(["z1_low", "z2_low", "z3_low", "z4_low", "z5_low", "z5_high"][index], String(value))
    );
    form.set("hrmax_bpm", "205");
    form.set("timezone", "Europe/Sofia");

    const response = await saveSettings(new Request("https://web.example.test/api/athlete/settings", { method: "POST", body: form }));

    expect(response.headers.get("location")).toBe("/?settings=saved");
    const [, init] = fetchMock.mock.calls[1];
    expect(init.headers["X-OnFlows-Athlete-Alias"]).toBe("ath-test-profile");
    expect(JSON.parse(init.body)).toEqual({
      hr_zone_bounds_bpm: [100, 120, 140, 160, 180, 200],
      timezone: "Europe/Sofia",
      hrmax_bpm: 205,
    });
  });

  it("saves planning inputs only for the signed athlete session", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    process.env.ONFLOWS_PROFILE_MODE = "multi";
    process.env.ONFLOWS_SESSION_SECRET = "a-secret-value-with-at-least-32-characters";
    const session = createAthleteSession("ath-test-profile");
    vi.mocked(cookies).mockResolvedValue({ get: () => ({ value: session }) } as never);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json({ configured: true }));
    vi.stubGlobal("fetch", fetchMock);
    const form = new FormData();
    const values = {
      schema_version: "planning-profile-v1",
      season_start: "2026-01-01",
      season_end: "2026-12-31",
      annual_target_hours: "600",
      sessions_per_week: "9",
      long_session_day: "6",
      max_key_sessions_per_week: "3",
      mesocycle_anchor_date: "2026-01-01",
      mesocycle_length_weeks: "4",
      camp_default_accent_limit: "2",
      double_threshold_day: "2",
    };
    Object.entries(values).forEach(([name, value]) => form.set(name, value));
    form.append("rest_days", "0");
    form.append("double_session_days", "2");
    form.append("double_session_days", "5");
    form.append("intensity_days", "2");
    form.append("intensity_days", "5");
    form.append("strength_days", "1");
    form.append("strength_days", "4");
    form.append("double_threshold_components", "Z3");
    form.append("double_threshold_components", "Z4");

    const response = await savePlanningProfile(new Request(
      "https://web.example.test/api/athlete/planning-profile",
      { method: "POST", body: form },
    ));

    expect(response.headers.get("location")).toBe("/planning?planning=saved");
    const [, init] = fetchMock.mock.calls[1];
    expect(init.headers["X-OnFlows-Athlete-Alias"]).toBe("ath-test-profile");
    expect(JSON.parse(init.body)).toEqual({
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
    });
  });

  it("saves canonicalized mesocycle accents only for the signed athlete session", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    process.env.ONFLOWS_PROFILE_MODE = "multi";
    process.env.ONFLOWS_SESSION_SECRET = "a-secret-value-with-at-least-32-characters";
    const session = createAthleteSession("ath-test-profile");
    vi.mocked(cookies).mockResolvedValue({ get: () => ({ value: session }) } as never);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json({ configured: true }));
    vi.stubGlobal("fetch", fetchMock);
    const form = new FormData();
    form.set("schema_version", "mesocycle-accent-preferences-v1");
    form.set("accent_mode", "HYBRID");
    form.set("accent_limit", "3");
    form.append("manual_components", "Z5");
    form.append("manual_components", "Z3");

    const response = await saveMesocycleAccents(new Request(
      "https://web.example.test/api/athlete/mesocycle-accents",
      { method: "POST", body: form },
    ));

    expect(response.headers.get("location")).toBe("/planning?planning=accents-saved");
    const [, init] = fetchMock.mock.calls[1];
    expect(init.headers["X-OnFlows-Athlete-Alias"]).toBe("ath-test-profile");
    expect(JSON.parse(init.body)).toEqual({
      schema_version: "mesocycle-accent-preferences-v1",
      accent_mode: "HYBRID",
      accent_limit: 3,
      manual_components: ["Z3", "Z5"],
    });
  });

  it("saves a canonical planning calendar only for the signed athlete session", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    process.env.ONFLOWS_PROFILE_MODE = "multi";
    process.env.ONFLOWS_SESSION_SECRET = "a-secret-value-with-at-least-32-characters";
    const session = createAthleteSession("ath-test-profile");
    vi.mocked(cookies).mockResolvedValue({ get: () => ({ value: session }) } as never);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json({ configured: true }));
    vi.stubGlobal("fetch", fetchMock);
    const events = [
      {
        event_id: "event-main-0001",
        event_type: "MAIN_RACE",
        name: "Основен старт",
        start_date: "2026-12-12",
        end_date: "2026-12-13",
      },
    ];
    const form = new FormData();
    form.set("schema_version", "planning-calendar-v1");
    form.set("events_json", JSON.stringify(events));

    const response = await savePlanningCalendar(new Request(
      "https://web.example.test/api/athlete/planning-calendar",
      { method: "POST", body: form },
    ));

    expect(response.headers.get("location")).toBe("/planning?planning=calendar-saved");
    const [, init] = fetchMock.mock.calls[1];
    expect(init.headers["X-OnFlows-Athlete-Alias"]).toBe("ath-test-profile");
    expect(JSON.parse(init.body)).toEqual({
      schema_version: "planning-calendar-v1",
      events,
    });
  });
});
