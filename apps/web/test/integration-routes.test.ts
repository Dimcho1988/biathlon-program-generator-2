import { afterEach, describe, expect, it, vi } from "vitest";
import { cookies } from "next/headers";
import { GET as connect } from "../app/api/integrations/intervals/connect/route";
import * as refreshRoute from "../app/api/integrations/intervals/refresh/route";
import { GET as syncStatus } from "../app/api/integrations/intervals/status/route";
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
    delete process.env.ONFLOWS_API_RESOURCE;
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

  it("enqueues a full sync and returns immediately to the active dashboard", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn().mockResolvedValueOnce(Response.json({
      schema_version: "sync-enqueue-v1",
      job_id: "sync-job-1",
      scope: "FULL",
      state: "QUEUED",
      coalesced: false,
    }, { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    const info = vi.spyOn(console, "info").mockImplementation(() => undefined);
    const request = new Request("https://web.example.test/api/integrations/intervals/refresh", { method: "POST", body: new FormData() });

    const response = await refreshRoute.POST(request);

    expect(response.headers.get("location")).toBe("/?sync=queued&wake=ready");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toBe("https://api.example.test/api/v2/real/sync-jobs");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ scope: "FULL" });
    expect(info).toHaveBeenCalledWith("intervals_sync_enqueued state=QUEUED coalesced=false");
  });

  it("does not expose a GET mutation handler", () => expect("GET" in refreshRoute).toBe(false));

  it("rejects an explicit unknown sync scope without enqueueing", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const body = new FormData();
    body.set("scope", "EVERYTHING");

    const response = await refreshRoute.POST(new Request(
      "https://web.example.test/api/integrations/intervals/refresh",
      { method: "POST", body },
    ));

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual({ error: "invalid_sync_scope" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns a calendar refresh to the same validated period", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn().mockResolvedValueOnce(Response.json({
      schema_version: "sync-enqueue-v1", job_id: "sync-job-2", scope: "WELLNESS", state: "QUEUED", coalesced: false,
    }, { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    const body = new FormData();
    body.set("returnTo", "/activities?start=2026-07-15&end=2026-08-25");
    body.set("scope", "wellness");

    const response = await refreshRoute.POST(new Request("https://web.example.test/api/integrations/intervals/refresh", { method: "POST", body }));

    expect(response.headers.get("location")).toBe("/activities?start=2026-07-15&end=2026-08-25&sync=queued");
    expect(String(fetchMock.mock.calls[0][0])).toBe("https://api.example.test/api/v2/real/sync-jobs");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ scope: "WELLNESS" });
  });

  it("enqueues recovery restore through the same durable job boundary", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_API_RESOURCE = "real";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn().mockResolvedValueOnce(Response.json({
      schema_version: "sync-enqueue-v1", job_id: "sync-job-3", scope: "RECOVERY", state: "QUEUED", coalesced: false,
    }, { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    const body = new FormData();
    body.set("scope", "recovery");

    const response = await refreshRoute.POST(new Request("https://web.example.test/api/integrations/intervals/refresh", { method: "POST", body }));

    expect(response.headers.get("location")).toBe("/?sync=queued&wake=ready");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ scope: "RECOVERY" });
  });

  it("accepts a coalesced job without starting a second synchronous refresh", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn().mockResolvedValueOnce(Response.json({
      schema_version: "sync-enqueue-v1", job_id: "sync-job-existing", scope: "FULL", state: "RUNNING", coalesced: true,
    }, { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const body = new FormData();
    body.set("scope", "FULL");

    const response = await refreshRoute.POST(new Request("https://web.example.test/api/integrations/intervals/refresh", { method: "POST", body }));

    expect(response.headers.get("location")).toBe("/?sync=coalesced&wake=ready");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("fails safely when enqueue cannot be confirmed", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(null, { status: 503 })));
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = await refreshRoute.POST(new Request(
      "https://web.example.test/api/integrations/intervals/refresh",
      { method: "POST", body: new FormData() },
    ));
    expect(response.headers.get("location")).toBe("/?sync=enqueue-error&wake=ready");
  });

  it("returns the profile-scoped persisted sync status without caching it", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    process.env.ONFLOWS_PROFILE_MODE = "multi";
    process.env.ONFLOWS_SESSION_SECRET = "a-secret-value-with-at-least-32-characters";
    const session = createAthleteSession("ath-test-profile");
    vi.mocked(cookies).mockResolvedValue({ get: () => ({ value: session }) } as never);
    const state = {
      schema_version: "sync-state-v1", job_id: "sync-job-4", scope: "FULL", state: "RUNNING", stage: "MODELS",
      progress_percent: 45, requested_at: "2026-08-27T06:00:00Z", started_at: "2026-08-27T06:00:01Z",
      finished_at: null, retry_at: null, failure_code: null, active_generation_id: "gen-41", active_revision: 41,
      analysis_as_of: "2026-08-26", activated_at: "2026-08-26T18:00:00Z",
    };
    const fetchMock = vi.fn().mockResolvedValueOnce(Response.json(state));
    vi.stubGlobal("fetch", fetchMock);

    const response = await syncStatus();

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toContain("no-store");
    await expect(response.json()).resolves.toEqual(state);
    expect(String(fetchMock.mock.calls[0][0])).toBe("https://api.example.test/api/v2/real/sync-status");
    expect(fetchMock.mock.calls[0][1].headers["X-OnFlows-Athlete-Alias"]).toBe("ath-test-profile");
  });

  it("does not expose another profile's sync status without a signed session", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    process.env.ONFLOWS_PROFILE_MODE = "multi";
    process.env.ONFLOWS_SESSION_SECRET = "a-secret-value-with-at-least-32-characters";
    vi.mocked(cookies).mockResolvedValue({ get: () => undefined } as never);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await syncStatus();

    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(fetchMock).not.toHaveBeenCalled();
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
      sessions_per_week: "8",
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
    });
  });

  it("rejects planning structures that exceed explicit weekly capacity", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    process.env.ONFLOWS_PROFILE_MODE = "multi";
    process.env.ONFLOWS_SESSION_SECRET = "a-secret-value-with-at-least-32-characters";
    const session = createAthleteSession("ath-test-profile");
    vi.mocked(cookies).mockResolvedValue({ get: () => ({ value: session }) } as never);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const planningForm = ({
      sessions = "8",
      restDays = ["0"],
      doubleDays = ["2", "5"],
      doubleThreshold = false,
      thresholdDay = "2",
    }: {
      sessions?: string;
      restDays?: string[];
      doubleDays?: string[];
      doubleThreshold?: boolean;
      thresholdDay?: string;
    }) => {
      const form = new FormData();
      for (const [name, value] of Object.entries({
        schema_version: "planning-profile-v1",
        season_start: "2026-01-01",
        season_end: "2026-12-31",
        annual_target_hours: "600",
        sessions_per_week: sessions,
        long_session_day: "6",
        max_key_sessions_per_week: "3",
        mesocycle_anchor_date: "2026-01-01",
        mesocycle_length_weeks: "4",
        camp_default_accent_limit: "2",
        double_threshold_day: thresholdDay,
      })) form.set(name, value);
      for (const day of restDays) form.append("rest_days", day);
      for (const day of doubleDays) form.append("double_session_days", day);
      form.append("intensity_days", "2");
      form.append("strength_days", "4");
      form.append("double_threshold_components", "Z3");
      if (doubleThreshold) form.set("double_threshold_enabled", "true");
      return form;
    };

    const invalidForms = [
      planningForm({ sessions: "9" }),
      planningForm({ restDays: ["0", "2"] }),
      planningForm({ doubleThreshold: true, thresholdDay: "4" }),
    ];
    for (const form of invalidForms) {
      const response = await savePlanningProfile(new Request(
        "https://web.example.test/api/athlete/planning-profile",
        { method: "POST", body: form },
      ));
      expect(response.headers.get("location")).toBe("/planning?planning=invalid");
    }
    expect(fetchMock).not.toHaveBeenCalled();
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
