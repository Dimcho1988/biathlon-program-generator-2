import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Page from "../app/page";
import { currentAuthorizedAthlete } from "../lib/account-access";
import * as api from "../lib/api";

vi.mock("../lib/account-access", () => ({ currentAuthorizedAthlete: vi.fn(), currentAccountRoles: vi.fn().mockResolvedValue([]) }));
vi.mock("../lib/account-profile", () => ({ currentAccountDisplayName: vi.fn().mockResolvedValue("Треньор") }));
vi.mock("../lib/athlete-session", () => ({ multiProfileMode: () => true }));
vi.mock("../lib/api", () => ({ getAthleteSettings: vi.fn(), getCompletedWork: vi.fn(), getDashboardView: vi.fn(), getLoadHistory: vi.fn(), getRecoveryHistory: vi.fn(), getSyncState: vi.fn(), getTrainingStatus: vi.fn(), getVolumeHistory: vi.fn() }));
vi.mock("../components/error-state", () => ({ ErrorState: ({ message }: { message: string }) => <main role="alert">{message}</main> }));
vi.mock("../components/dashboard", () => ({ Dashboard: ({ recoveryHistory }: { recoveryHistory: unknown }) => <main data-testid="dashboard">{JSON.stringify(recoveryHistory)}</main> }));
const access = { userId: "owner", actorUserId: "viewer", athleteAlias: "athlete-a", displayName: "Спортист", isOwner: false, canViewPlan: true, canViewRecovery: false, canEditPlan: false };
beforeEach(() => { vi.stubEnv("ONFLOWS_API_RESOURCE", "real"); vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access); });
afterEach(() => { vi.unstubAllEnvs(); vi.clearAllMocks(); });
const reads = [api.getAthleteSettings, api.getCompletedWork, api.getDashboardView, api.getLoadHistory, api.getRecoveryHistory, api.getSyncState, api.getTrainingStatus, api.getVolumeHistory];

it.each([undefined, "overview", "recovery", "report", "details"])("blocks all physiological aggregate reads for plan-only dashboard view %s", async view => {
  const html = renderToStaticMarkup(await Page({ searchParams: Promise.resolve({ view }) }));
  expect(html).toContain("Нямате достъп до физиологичните данни"); expect(html).toContain("Седмична програма");
  for (const read of reads) expect(read).not.toHaveBeenCalled();
  expect(html).not.toContain('data-testid="dashboard"');
});

it("checks the physiological permission before the settings shortcut can expose HRmax or zones", async () => {
  const html = renderToStaticMarkup(await Page({ searchParams: Promise.resolve({ settings: "edit" }) }));
  expect(html).toContain("Нямате достъп до физиологичните данни");
  for (const read of reads) expect(read).not.toHaveBeenCalled();
});

it("retains dashboard access for an authorized recovery viewer", async () => {
  vi.mocked(currentAuthorizedAthlete).mockResolvedValueOnce({ ...access, canViewRecovery: true });
  vi.mocked(api.getDashboardView).mockResolvedValueOnce({ training_status: {}, recovery_history: { permitted: true } } as never);
  vi.mocked(api.getSyncState).mockResolvedValueOnce(null as never);
  const html = renderToStaticMarkup(await Page({ searchParams: Promise.resolve({}) }));
  expect(html).toContain('data-testid="dashboard"'); expect(html).toContain("permitted");
  expect(api.getDashboardView).toHaveBeenCalledOnce(); expect(api.getDashboardView).toHaveBeenCalledWith("athlete-a", undefined, undefined);
});

it("requires an accessible selected athlete before any aggregate read", async () => {
  vi.mocked(currentAuthorizedAthlete).mockResolvedValueOnce(null);
  const html = renderToStaticMarkup(await Page({ searchParams: Promise.resolve({}) }));
  expect(html).toContain("Няма избран достъпен профил"); for (const read of reads) expect(read).not.toHaveBeenCalled();
});
