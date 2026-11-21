import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ActivitiesPage from "../app/activities/page";
import TrainabilityPage from "../app/trainability/page";
import ActivityPage from "../app/activities/[activityRef]/page";
import ActivityShadowPage from "../app/activities/[activityRef]/shadow/page";
import { currentAuthorizedAthlete } from "../lib/account-access";
import { multiProfileMode } from "../lib/athlete-session";
import { getActivityCalendar, getActivityView, getAthleteSettings, getSyncState, getTrainabilityHistory } from "../lib/api";
import { ErrorState } from "../components/error-state";

vi.mock("../lib/account-access", () => ({ currentAuthorizedAthlete: vi.fn() }));
vi.mock("../lib/athlete-session", () => ({ multiProfileMode: vi.fn() }));
vi.mock("../lib/api", () => ({ getActivityCalendar: vi.fn(), getActivityView: vi.fn(), getAthleteSettings: vi.fn(), getSyncState: vi.fn(), getTrainabilityHistory: vi.fn() }));
vi.mock("../components/error-state", () => ({ ErrorState: () => null }));

const actor = { actorUserId: "viewer", userId: "athlete", athleteAlias: "athlete-selected", displayName: "Спортист",
  isOwner: false, canViewPlan: true, canViewRecovery: false, canEditPlan: false };
const requests = [getActivityCalendar, getActivityView, getAthleteSettings, getSyncState, getTrainabilityHistory];
const surfaces = [
  ["activity calendar", () => ActivitiesPage({ searchParams: Promise.resolve({}) }), getActivityCalendar],
  ["trainability history", () => TrainabilityPage({ searchParams: Promise.resolve({}) }), getTrainabilityHistory],
  ["activity detail", () => ActivityPage({ params: Promise.resolve({ activityRef: "act_" + "a".repeat(32) }) }), getActivityView],
  ["activity shadow", () => ActivityShadowPage({ params: Promise.resolve({ activityRef: "act_" + "a".repeat(32) }) }), getActivityView],
] as const;

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(multiProfileMode).mockReturnValue(true);
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue(actor);
  // An allowed read may be unavailable; that must not weaken the access gate.
  for (const request of requests) vi.mocked(request).mockRejectedValue(new Error("Unavailable fixture"));
});

describe("physiological pages respect separately shared recovery access", () => {
  it.each(surfaces)("blocks a plan-only viewer before any %s request", async (_name, render) => {
    const result = await render() as ReactElement<{ message: string }>;
    expect(result.type).toBe(ErrorState);
    expect(result.props.message).toContain("не е споделил физиологичните");
    for (const request of requests) expect(request).not.toHaveBeenCalled();
  });

  it.each(surfaces)("rechecks revoked access on a later %s navigation", async (_name, render, read) => {
    vi.mocked(currentAuthorizedAthlete).mockResolvedValueOnce({ ...actor, canViewRecovery: true }).mockResolvedValueOnce(actor);
    await render();
    expect(read).toHaveBeenCalledOnce();
    const calls = requests.map(request => vi.mocked(request).mock.calls.length);
    const result = await render() as ReactElement<{ message: string }>;
    expect(result.props.message).toContain("не е споделил физиологичните");
    expect(requests.map(request => vi.mocked(request).mock.calls.length)).toEqual(calls);
    expect(currentAuthorizedAthlete).toHaveBeenCalledTimes(2);
  });

  it.each(surfaces)("keeps the %s fixture/offline path available", async (_name, render, read) => {
    vi.mocked(multiProfileMode).mockReturnValue(false);
    await render();
    expect(currentAuthorizedAthlete).not.toHaveBeenCalled();
    expect(read).toHaveBeenCalledOnce();
  });
});
