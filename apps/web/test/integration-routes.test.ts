import { afterEach, describe, expect, it, vi } from "vitest";
import { GET as connect } from "../app/api/integrations/intervals/connect/route";
import { POST as refresh } from "../app/api/integrations/intervals/refresh/route";

describe("integration route redirects behind a reverse proxy", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.ONFLOWS_API_BASE_URL;
    delete process.env.ONFLOWS_SERVICE_TOKEN;
  });

  it("returns a relative dashboard redirect when OAuth start fails", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 503 })));

    const response = await connect();

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/?intervals=connect-error");
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
});
