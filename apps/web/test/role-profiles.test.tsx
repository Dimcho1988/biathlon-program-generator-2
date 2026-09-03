import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { cookies } from "next/headers";
import { AccountWorkspacePanel } from "../components/account-workspace";
import { currentAuthorizedAthlete, type AccountWorkspace } from "../lib/account-access";
import { createClient } from "../lib/supabase/server";
import { createAthleteSession } from "../lib/athlete-session";
import { POST as createOrganization } from "../app/api/account/organizations/route";
import { POST as createInvite } from "../app/api/account/invites/route";
import { POST as saveAssignment } from "../app/api/account/assignments/route";
import { POST as selectAthlete } from "../app/api/account/athletes/select/route";

vi.mock("../lib/supabase/server", () => ({ createClient: vi.fn() }));
vi.mock("next/headers", () => ({ cookies: vi.fn() }));

const userId = "11111111-1111-4111-8111-111111111111";
const organizationId = "22222222-2222-4222-8222-222222222222";
const coachId = "33333333-3333-4333-8333-333333333333";
const athleteId = "44444444-4444-4444-8444-444444444444";

const request = (path: string, body: FormData, origin = "https://web.example.test") => new Request(
  `https://web.example.test${path}`,
  { method: "POST", headers: { Origin: origin }, body },
);

const auth = () => ({
  getClaims: vi.fn().mockResolvedValue({ data: { claims: { sub: userId } } }),
});

describe("working role profiles", () => {
  afterEach(() => {
    vi.clearAllMocks();
    delete process.env.ONFLOWS_SESSION_SECRET;
  });

  it("renders head-coach management, personal athlete access and assignments", () => {
    const workspace: AccountWorkspace = {
      userId,
      displayName: "Димчо",
      roles: ["HEAD_COACH", "ATHLETE"],
      memberships: [{
        organizationId,
        organizationName: "Национален отбор",
        organizationSlug: "national-team",
        role: "HEAD_COACH",
      }],
      accessibleAthletes: [{
        userId: athleteId,
        athleteAlias: "ath-biathlon-01",
        displayName: "Мария Иванова",
        isOwner: false,
        canEditPlan: true,
      }],
      members: [
        { organizationId, userId, displayName: "Димчо", role: "HEAD_COACH", status: "ACTIVE" },
        { organizationId, userId: coachId, displayName: "Иван Треньоров", role: "COACH", status: "ACTIVE" },
        { organizationId, userId: athleteId, displayName: "Мария Иванова", role: "ATHLETE", status: "ACTIVE" },
      ],
      assignments: [{ organizationId, coachUserId: coachId, athleteUserId: athleteId, canEditPlan: true }],
      invites: [{
        id: "55555555-5555-4555-8555-555555555555",
        inviterUserId: userId,
        inviteeEmail: "new@example.test",
        organizationId,
        organizationName: "Национален отбор",
        role: "COACH",
        expiresAt: "2026-09-09T00:00:00Z",
        incoming: false,
      }],
    };

    const html = renderToStaticMarkup(<AccountWorkspacePanel workspace={workspace} />);

    expect(html).toContain("Главен треньор");
    expect(html).toContain("Спортист");
    expect(html).toContain("Мария Иванова");
    expect(html).toContain("Треньор: Иван Треньоров");
    expect(html).toContain('action="/api/account/invites"');
    expect(html).toContain('action="/api/account/assignments"');
    expect(html).toContain("new@example.test");
  });

  it("creates an organization atomically through the protected RPC", async () => {
    const rpc = vi.fn().mockResolvedValue({ error: null });
    vi.mocked(createClient).mockResolvedValue({ auth: auth(), rpc } as never);
    const body = new FormData();
    body.set("organization_name", "  Биатлон клуб  ");

    const response = await createOrganization(request("/api/account/organizations", body));

    expect(response.headers.get("location")).toBe("https://web.example.test/account?saved=organization");
    expect(rpc).toHaveBeenCalledWith("create_onflows_organization", {
      p_name: "Биатлон клуб",
      p_slug: expect.stringMatching(/^team-[0-9a-f]{16}$/),
    });
  });

  it("rejects cross-origin role mutations before contacting Supabase", async () => {
    const body = new FormData();
    body.set("organization_name", "Bad team");

    const response = await createOrganization(request(
      "/api/account/organizations",
      body,
      "https://attacker.example.test",
    ));

    expect(response.status).toBe(403);
    expect(createClient).not.toHaveBeenCalled();
  });

  it("allows only coach or athlete invitations from the web form", async () => {
    const from = vi.fn();
    vi.mocked(createClient).mockResolvedValue({ auth: auth(), from } as never);
    const body = new FormData();
    body.set("organization_id", organizationId);
    body.set("invitee_email", "target@example.test");
    body.set("membership_role", "HEAD_COACH");

    const response = await createInvite(request("/api/account/invites", body));

    expect(response.headers.get("location")).toBe("https://web.example.test/account?error=invalid");
    expect(from).not.toHaveBeenCalled();
  });

  it("binds assignments to the verified manager id", async () => {
    const upsert = vi.fn().mockResolvedValue({ error: null });
    vi.mocked(createClient).mockResolvedValue({
      auth: auth(),
      from: vi.fn().mockReturnValue({ upsert }),
    } as never);
    const body = new FormData();
    body.set("organization_id", organizationId);
    body.set("coach_user_id", coachId);
    body.set("athlete_user_id", athleteId);
    body.set("can_edit_plan", "on");

    const response = await saveAssignment(request("/api/account/assignments", body));

    expect(response.headers.get("location")).toBe("https://web.example.test/account?saved=assignment");
    expect(upsert).toHaveBeenCalledWith({
      organization_id: organizationId,
      coach_user_id: coachId,
      athlete_user_id: athleteId,
      assigned_by_user_id: userId,
      can_edit_plan: true,
    }, { onConflict: "organization_id,coach_user_id,athlete_user_id" });
  });

  it("issues an athlete cookie only after the RLS-visible alias is found", async () => {
    process.env.ONFLOWS_SESSION_SECRET = "a-secret-value-with-at-least-32-characters";
    const maybeSingle = vi.fn().mockResolvedValue({ data: { athlete_alias: "ath-biathlon-01" }, error: null });
    const eq = vi.fn().mockReturnValue({ maybeSingle });
    vi.mocked(createClient).mockResolvedValue({
      auth: auth(),
      from: vi.fn().mockReturnValue({ select: vi.fn().mockReturnValue({ eq }) }),
    } as never);
    const body = new FormData();
    body.set("athlete_alias", "ath-biathlon-01");

    const response = await selectAthlete(request("/api/account/athletes/select", body));

    expect(response.headers.get("location")).toBe("https://web.example.test/?profile=selected");
    expect(response.headers.get("set-cookie")).toContain("onflows-athlete-session=");
    expect(response.headers.get("set-cookie")).toContain("HttpOnly");
    expect(eq).toHaveBeenCalledWith("athlete_alias", "ath-biathlon-01");
  });

  it("derives coach edit rights from the active database assignment", async () => {
    process.env.ONFLOWS_PROFILE_MODE = "multi";
    process.env.ONFLOWS_SESSION_SECRET = "a-secret-value-with-at-least-32-characters";
    const session = createAthleteSession("ath-biathlon-01");
    vi.mocked(cookies).mockResolvedValue({ get: () => ({ value: session }) } as never);
    vi.mocked(createClient).mockResolvedValue({
      auth: auth(),
      from: vi.fn((table: string) => ({
        select: vi.fn(() => {
          if (table === "onflows_user_athletes") return {
            eq: vi.fn().mockReturnValue({
              maybeSingle: vi.fn().mockResolvedValue({
                data: { user_id: athleteId, athlete_alias: "ath-biathlon-01", is_owner: true },
                error: null,
              }),
            }),
          };
          if (table === "onflows_profiles") return {
            eq: vi.fn().mockReturnValue({
              maybeSingle: vi.fn().mockResolvedValue({ data: { display_name: "Мария Иванова" }, error: null }),
            }),
          };
          if (table === "onflows_organization_memberships") return Promise.resolve({
            data: [
              { organization_id: organizationId, user_id: userId, role: "COACH", status: "ACTIVE" },
              { organization_id: organizationId, user_id: athleteId, role: "ATHLETE", status: "ACTIVE" },
            ],
            error: null,
          });
          if (table === "onflows_coach_athlete_assignments") return Promise.resolve({
            data: [{
              organization_id: organizationId,
              coach_user_id: userId,
              athlete_user_id: athleteId,
              can_edit_plan: true,
            }],
            error: null,
          });
          return Promise.resolve({ data: [], error: null });
        }),
      })),
    } as never);

    await expect(currentAuthorizedAthlete()).resolves.toEqual({
      userId: athleteId,
      athleteAlias: "ath-biathlon-01",
      displayName: "Мария Иванова",
      isOwner: false,
      canEditPlan: true,
    });
  });
});
