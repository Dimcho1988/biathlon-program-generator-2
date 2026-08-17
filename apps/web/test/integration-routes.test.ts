import { afterEach, describe, expect, it, vi } from "vitest";
import { cookies } from "next/headers";
import { GET as connect } from "../app/api/integrations/intervals/connect/route";
import { POST as refresh } from "../app/api/integrations/intervals/refresh/route";
import { POST as saveSettings } from "../app/api/athlete/settings/route";
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

  it("returns a relative dashboard redirect when API readiness fails", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await connect();

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

    const responsePromise = connect();
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

    const response = await connect();

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

    const response = await connect();

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
    form.set("timezone", "Europe/Sofia");

    const response = await saveSettings(new Request("https://web.example.test/api/athlete/settings", { method: "POST", body: form }));

    expect(response.headers.get("location")).toBe("/?settings=saved");
    const [, init] = fetchMock.mock.calls[1];
    expect(init.headers["X-OnFlows-Athlete-Alias"]).toBe("ath-test-profile");
    expect(JSON.parse(init.body)).toEqual({
      hr_zone_bounds_bpm: [100, 120, 140, 160, 180, 200],
      timezone: "Europe/Sofia",
    });
  });
});
