import { describe, expect, it } from "vitest";
import { canEditAthlete, canViewAthletePlan } from "../lib/account-access";

type Memberships = Parameters<typeof canViewAthletePlan>[2];
type Assignments = Parameters<typeof canViewAthletePlan>[3];

const memberships: Memberships = [
  { organization_id: "org", user_id: "athlete", role: "ATHLETE", status: "ACTIVE" },
  { organization_id: "org", user_id: "coach", role: "COACH", status: "ACTIVE" },
];
const assignments: Assignments = [
  { organization_id: "org", coach_user_id: "coach", athlete_user_id: "athlete", can_edit_plan: false },
];

describe("explicit training-plan visibility", () => {
  it("allows the athlete to see their own plan without a sharing grant", () => {
    expect(canViewAthletePlan("athlete", "athlete", [], [], [])).toBe(true);
  });

  it("does not derive plan access from overview or recovery visibility", () => {
    expect(canViewAthletePlan("coach", "athlete", [], [], [])).toBe(false);
    expect(canViewAthletePlan("coach", "athlete", [], [], [
      { owner_user_id: "athlete", viewer_user_id: "coach", edit_plan: false, view_recovery: true, view_plan: false },
    ])).toBe(false);
  });

  it("allows explicit read-only plan sharing and checks both grant identities", () => {
    const grant = { owner_user_id: "athlete", viewer_user_id: "coach", edit_plan: false, view_plan: true };
    expect(canViewAthletePlan("coach", "athlete", [], [], [grant])).toBe(true);
    expect(canViewAthletePlan("other", "athlete", [], [], [grant])).toBe(false);
    expect(canViewAthletePlan("coach", "other", [], [], [grant])).toBe(false);
    expect(canViewAthletePlan("coach", "athlete", [], [], [{ ...grant, view_plan: undefined }])).toBe(false);
  });

  it("allows assigned read-only coaches only with active memberships", () => {
    expect(canViewAthletePlan("coach", "athlete", memberships, assignments, [])).toBe(true);
    for (const index of [0, 1]) {
      for (const status of ["INVITED", "SUSPENDED", "LEFT"] as const) {
        const changed = memberships.map((member, i) => i === index ? { ...member, status } : member);
        expect(canViewAthletePlan("coach", "athlete", changed, assignments, [])).toBe(false);
      }
    }
  });

  it("rejects a coach's assignment for another athlete or organization", () => {
    expect(canViewAthletePlan("coach", "athlete", memberships, [], [])).toBe(false);
    expect(canViewAthletePlan("coach", "athlete", memberships, [{ ...assignments[0], organization_id: "other" }], [])).toBe(false);
    expect(canViewAthletePlan("coach", "athlete", memberships, [{ ...assignments[0], athlete_user_id: "other" }], [])).toBe(false);
    expect(canViewAthletePlan("coach", "athlete", memberships, [{ ...assignments[0], coach_user_id: "other" }], [])).toBe(false);
  });

  it("allows active organization leadership without granting unrelated organization access", () => {
    for (const role of ["HEAD_COACH", "ADMIN"] as const) {
      const leaders: Memberships = [memberships[0], { ...memberships[1], role }];
      expect(canViewAthletePlan("coach", "athlete", leaders, [], [])).toBe(true);
      expect(canViewAthletePlan("coach", "athlete", [leaders[0], { ...leaders[1], organization_id: "other" }], [], [])).toBe(false);
      expect(canViewAthletePlan("coach", "athlete", [leaders[0], { ...leaders[1], status: "SUSPENDED" }], [], [])).toBe(false);
    }
  });

  it("requires an active coach role for editing through an existing assignment", () => {
    const input = { userId: "coach", athleteUserId: "athlete", isOwner: false, memberships,
      assignments: [{ ...assignments[0], can_edit_plan: true }], sharingGrants: [] };
    expect(canEditAthlete(input)).toBe(true);
    expect(canEditAthlete({ ...input, assignments })).toBe(false);
    expect(canEditAthlete({ ...input, memberships: [memberships[0]] })).toBe(false);
    expect(canEditAthlete({ ...input, memberships: [memberships[0], { ...memberships[1], role: "ATHLETE" }] })).toBe(false);
    expect(canEditAthlete({ ...input, memberships: [memberships[0], { ...memberships[1], organization_id: "other" }] })).toBe(false);
    for (const status of ["INVITED", "SUSPENDED", "LEFT"] as const) {
      expect(canEditAthlete({ ...input, memberships: [memberships[0], { ...memberships[1], status }] })).toBe(false);
    }
  });

  it("preserves explicit plan-edit sharing and active leadership independent of assignments", () => {
    const input = { userId: "coach", athleteUserId: "athlete", isOwner: false, memberships: [], assignments: [],
      sharingGrants: [{ owner_user_id: "athlete", viewer_user_id: "coach", edit_plan: true, view_plan: true }] };
    expect(canEditAthlete(input)).toBe(true);
    expect(canEditAthlete({ ...input, userId: "other" })).toBe(false);
    for (const role of ["ADMIN", "HEAD_COACH"] as const) {
      expect(canEditAthlete({ ...input, sharingGrants: [], memberships: [memberships[0], { ...memberships[1], role }] })).toBe(true);
    }
  });
});
