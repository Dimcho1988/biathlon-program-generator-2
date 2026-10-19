import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentAuthorizedAthlete } from "../lib/account-access";
import { currentAthleteAlias } from "../lib/athlete-session";
import { createClient } from "../lib/supabase/server";

vi.mock("../lib/athlete-session", () => ({ currentAthleteAlias: vi.fn() }));
vi.mock("../lib/supabase/server", () => ({ createClient: vi.fn() }));

const alias = "selected-athlete";
const ownerId = "athlete-user";
const graphTables = ["onflows_organization_memberships", "onflows_coach_athlete_assignments", "onflows_sharing_grants"];
type Result = { data: unknown; error: null | { message: string } };

function database(actorId = ownerId, owns = true) {
  const rows: Record<string, Result> = {
    onflows_user_athletes: { data: { user_id: ownerId, athlete_alias: alias, is_owner: owns }, error: null },
    onflows_profiles: { data: { display_name: "Спортист" }, error: null },
    onflows_organization_memberships: { data: [], error: null },
    onflows_coach_athlete_assignments: { data: [], error: null },
    onflows_sharing_grants: { data: [], error: null },
  };
  const filters: Array<[string, string, unknown]> = [];
  const from = vi.fn((table: string) => ({
    select: vi.fn(() => {
      if (graphTables.includes(table)) return Promise.resolve(rows[table]);
      return { eq: (column: string, value: unknown) => {
        filters.push([table, column, value]);
        return { maybeSingle: () => Promise.resolve(rows[table]) };
      } };
    }),
  }));
  const getClaims = vi.fn().mockResolvedValue({ data: { claims: { sub: actorId } } });
  vi.mocked(createClient).mockResolvedValue({ auth: { getClaims }, from } as never);
  return { rows, from, getClaims, filters };
}

beforeEach(() => { vi.mocked(currentAthleteAlias).mockResolvedValue(alias); });
afterEach(() => { vi.resetAllMocks(); });

describe("request-local athlete authorization reads", () => {
  it("uses the freshly verified owner identity without three unrelated graph reads", async () => {
    const db = database();
    for (const table of graphTables) db.rows[table] = { data: null, error: { message: "Unavailable" } };

    await expect(currentAuthorizedAthlete()).resolves.toEqual({
      actorUserId: ownerId, userId: ownerId, athleteAlias: alias, displayName: "Спортист",
      isOwner: true, canEditPlan: true, canViewPlan: true, canViewRecovery: true,
    });
    expect(db.from.mock.calls.map(([table]) => table)).toEqual(["onflows_user_athletes", "onflows_profiles"]);
    expect(db.getClaims).toHaveBeenCalledOnce();
    expect(db.filters).toEqual([
      ["onflows_user_athletes", "athlete_alias", alias], ["onflows_profiles", "user_id", ownerId],
    ]);
  });

  it("does not turn missing ownership into edit access for the same account", async () => {
    const db = database(ownerId, false);
    await expect(currentAuthorizedAthlete()).resolves.toMatchObject({
      isOwner: false, canEditPlan: false, canViewPlan: true, canViewRecovery: true,
    });
    expect(db.from.mock.calls.map(([table]) => table)).toEqual(["onflows_user_athletes", "onflows_profiles", ...graphTables]);
  });

  it("keeps the profile-name fallback for an owner", async () => {
    const db = database();
    db.rows.onflows_profiles = { data: null, error: { message: "Unavailable" } };
    await expect(currentAuthorizedAthlete()).resolves.toMatchObject({ displayName: "Спортист", isOwner: true });
  });

  it("rechecks ownership and the authorization graph when a later request selects another athlete", async () => {
    const db = database();
    await expect(currentAuthorizedAthlete()).resolves.toMatchObject({ isOwner: true });
    db.rows.onflows_user_athletes = { data: { user_id: "another-athlete", athlete_alias: "other-alias", is_owner: true }, error: null };
    vi.mocked(currentAthleteAlias).mockResolvedValue("other-alias");

    await expect(currentAuthorizedAthlete()).resolves.toMatchObject({
      actorUserId: ownerId, userId: "another-athlete", athleteAlias: "other-alias",
      isOwner: false, canEditPlan: false, canViewPlan: false, canViewRecovery: false,
    });
    expect(db.getClaims).toHaveBeenCalledTimes(2);
    expect(db.from.mock.calls.map(([table]) => table)).toEqual([
      "onflows_user_athletes", "onflows_profiles", "onflows_user_athletes", "onflows_profiles", ...graphTables,
    ]);
  });

  it("retains separate viewer permissions and observes a sharing revocation on the next request", async () => {
    const db = database("viewer");
    db.rows.onflows_sharing_grants.data = [{
      owner_user_id: ownerId, viewer_user_id: "viewer", edit_plan: false, view_plan: true, view_recovery: false,
    }];
    await expect(currentAuthorizedAthlete()).resolves.toMatchObject({
      isOwner: false, canEditPlan: false, canViewPlan: true, canViewRecovery: false,
    });
    db.rows.onflows_sharing_grants.data = [];
    await expect(currentAuthorizedAthlete()).resolves.toMatchObject({
      isOwner: false, canEditPlan: false, canViewPlan: false, canViewRecovery: false,
    });
    for (const table of graphTables) expect(db.from.mock.calls.filter(([read]) => read === table)).toHaveLength(2);
  });

  it.each(graphTables)("denies a viewer if the fresh %s read fails", async table => {
    const db = database("viewer");
    db.rows[table] = { data: null, error: { message: "Unavailable" } };
    await expect(currentAuthorizedAthlete()).resolves.toBeNull();
    expect(db.from).toHaveBeenCalledTimes(5);
  });

  it.each([null, { user_id: ownerId, athlete_alias: alias, is_owner: true }])(
    "denies missing or unreadable athlete ownership before loading the profile or graph",
    async data => {
      const db = database();
      db.rows.onflows_user_athletes = { data, error: data === null ? null : { message: "Denied" } };
      await expect(currentAuthorizedAthlete()).resolves.toBeNull();
      expect(db.from.mock.calls.map(([table]) => table)).toEqual(["onflows_user_athletes"]);
    },
  );

  it("does not accept an athlete selection without verified claims", async () => {
    const db = database();
    db.getClaims.mockResolvedValue({ data: { claims: null } });
    await expect(currentAuthorizedAthlete()).resolves.toBeNull();
    expect(db.from).not.toHaveBeenCalled();
  });
});
