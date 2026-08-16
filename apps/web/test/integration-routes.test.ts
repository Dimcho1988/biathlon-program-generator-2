import { afterEach, describe, expect, it, vi } from "vitest";
import { GET as connect } from "../app/api/integrations/intervals/connect/route";
import { POST as refresh } from "../app/api/integrations/intervals/refresh/route";
import { GET as complete } from "../app/api/session/complete/route";
import { createAthleteSession, verifyAthleteSession } from "../lib/athlete-session";

describe("integration route redirects behind a reverse proxy", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.ONFLOWS_API_BASE_URL;
    delete process.env.ONFLOWS_SERVICE_TOKEN;
    delete process.env.ONFLOWS_PROFILE_MODE;
    delete process.env.ONFLOWS_SESSION_SECRET;
  });

  it("returns a relative dashboard redirect when OAuth start fails", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await connect();

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/?intervals=connect-start-error");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("wakes a sleeping preview API and retries OAuth start once", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const authorizationUrl = "https://intervals.icu/oauth/authorize?state=opaque";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 502 }))
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json({ authorization_url: authorizationUrl }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await connect();

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(authorizationUrl);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(String(fetchMock.mock.calls[1][0])).toBe("https://api.example.test/health");
  });

  it("returns relative dashboard redirects after refresh success and failure", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 202 }))
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
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ athlete_alias: "ath-test-profile" }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await complete(new Request(`https://web.example.test/api/session/complete?ticket=${"t".repeat(40)}`));

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/?intervals=connected");
    expect(response.headers.get("set-cookie")).toContain("onflows-athlete-session=");
    expect(response.headers.get("set-cookie")).toContain("HttpOnly");
    expect(response.headers.get("set-cookie")).toContain("Secure");
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ ticket: "t".repeat(40) });
  });

  it("distinguishes a failed session handoff from an OAuth start failure", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));

    const response = await complete(new Request(`https://web.example.test/api/session/complete?ticket=${"t".repeat(40)}`));

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/?intervals=session-error");
  });
});
