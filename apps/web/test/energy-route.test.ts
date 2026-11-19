import { beforeEach, describe, expect, it, vi } from "vitest";
const access = vi.hoisted(() => vi.fn());
const origin = vi.hoisted(() => vi.fn());
vi.mock("../lib/account-access", () => ({ currentAuthorizedAthlete: access }));
vi.mock("../lib/account-route", () => ({ isSameOrigin: origin }));
vi.mock("../lib/api-readiness", () => ({ waitForApi: vi.fn() }));
import { POST } from "../app/api/athlete/energy/profile/route";
const request = () =>
  new Request("https://example.org/api/athlete/energy/profile", {
    method: "POST",
    body: JSON.stringify({ sex: "FEMALE", athlete_alias: "other" }),
  });
beforeEach(() => {
  vi.stubEnv("ONFLOWS_API_BASE_URL", "https://api.example.org");
  vi.stubEnv("ONFLOWS_SERVICE_TOKEN", "test-only");
  origin.mockReturnValue(true);
  access.mockResolvedValue({
    athleteAlias: "authorized",
    actorUserId: "actor",
    canViewRecovery: true,
    isOwner: true,
    canEditPlan: false,
  });
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ saved: true, revision: 1 }), {
          status: 200,
        }),
      ),
  );
});
describe("energy profile boundary", () => {
  it("denies plan editing without recovery sharing", async () => {
    access.mockResolvedValue({ canEditPlan: true, canViewRecovery: false });
    expect((await POST(request())).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("denies a recovery viewer without editing rights", async () => {
    access.mockResolvedValue({
      canEditPlan: false,
      isOwner: false,
      canViewRecovery: true,
    });
    expect((await POST(request())).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("rejects foreign origins before any write", async () => {
    origin.mockReturnValue(false);
    expect((await POST(request())).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("selects athlete and actor from authenticated session", async () => {
    expect((await POST(request())).status).toBe(200);
    const call = vi.mocked(fetch).mock.calls[0];
    const headers = call[1]?.headers as Record<string, string>;
    expect(headers["X-OnFlows-Athlete-Alias"]).toBe("authorized");
    expect(headers["X-OnFlows-Actor-Id"]).toBe("actor");
  });
});
