import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { getDashboardView } from "../lib/api";
import { API_RATE_LIMIT_MESSAGE, renderWakeHref, wakeReturnHref } from "../lib/api-wake";
import { ErrorState } from "../components/error-state";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("Render cold-start recovery", () => {
  it("preserves a cold-start 429 and does not hammer the protected API", async () => {
    vi.stubEnv("ONFLOWS_API_BASE_URL", "https://api.example.test");
    vi.stubEnv("ONFLOWS_SERVICE_TOKEN", "test-secret");
    const fetchMock = vi.fn().mockResolvedValue(new Response("Too Many Requests", { status: 429 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getDashboardView("test-athlete")).rejects.toThrow(API_RATE_LIMIT_MESSAGE);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toBe("https://api.example.test/health");
    expect(fetchMock.mock.calls[0][1]).not.toHaveProperty("headers.Authorization");
  });

  it("offers the existing public browser wake flow instead of enqueueing a sync", () => {
    vi.stubEnv("ONFLOWS_API_BASE_URL", "https://onflows-api-staging.onrender.com");
    const html = renderToStaticMarkup(<ErrorState message={API_RATE_LIMIT_MESSAGE} integrationActions connectAvailable={false} retryAvailable />);
    expect(html).toContain('href="https://onflows-api-staging.onrender.com/api/v2/wake"');
    expect(html).toContain("Опитай отново");
    expect(html).not.toContain("Обнови реалните данни");
    expect(html).not.toContain("Свържи нов Intervals");
  });

  it("keeps the report, analysis dates and activity return path after a public wake", () => {
    expect(wakeReturnHref("/?view=report&report_start=2026-09-01&report_end=2026-09-15&wake=ready&athlete_alias=private")).toBe("/?view=report&report_start=2026-09-01&report_end=2026-09-15");
    expect(wakeReturnHref("/trainability?start=2026-09-01&end=2026-09-15")).toBe("/trainability?start=2026-09-01&end=2026-09-15");
    expect(wakeReturnHref("/activities/act_123?token=private")).toBe("/activities/act_123");
    expect(wakeReturnHref("/?settings=edit")).toBe("/?settings=edit");
  });

  it("rejects external, action and malformed return destinations", () => {
    for (const saved of ["https://example.com/", "//example.com/", "javascript:alert(1)", "/api/auth/logout", "/activities/%2f%2fevil.example"]) {
      expect(wakeReturnHref(saved)).toBe("/");
    }
    expect(renderWakeHref("https://example.com")).toBeUndefined();
    expect(renderWakeHref("http://api.onrender.com")).toBeUndefined();
    expect(renderWakeHref("https://secret@api.onrender.com")).toBeUndefined();
    expect(renderWakeHref("https://api.onrender.com/?token=private")).toBe("https://api.onrender.com/api/v2/wake");
  });
});
