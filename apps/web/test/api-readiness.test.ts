import { afterEach, describe, expect, it, vi } from "vitest";
import { markApiUnavailable, waitForApi } from "../lib/api-readiness";

const base = "https://readiness.example.test";
afterEach(() => { markApiUnavailable(base); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("successful health probe reuse", () => {
  it("avoids repeated probes within 30 seconds and expires after that", async () => {
    let now = 100_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const fetchMock = vi.fn(() => Promise.resolve(Response.json({ status: "ok" })));
    vi.stubGlobal("fetch", fetchMock);
    await waitForApi(base);
    await waitForApi(base);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    now += 30_001;
    await waitForApi(base);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it("rechecks immediately after a failed resource request", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(Response.json({ status: "ok" })));
    vi.stubGlobal("fetch", fetchMock);
    await waitForApi(base);
    markApiUnavailable(base);
    await waitForApi(base);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it("does not cache a rate limit as successful health", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("limited", { status: 429 }))
      .mockResolvedValueOnce(Response.json({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(waitForApi(base)).rejects.toThrow("429");
    await waitForApi(base);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
