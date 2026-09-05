import { currentAthleteAlias } from "./athlete-session";
import { createClient } from "./supabase/server";

export const ACCOUNT_ROLES = ["ADMIN", "HEAD_COACH", "COACH", "ATHLETE"] as const;
export type AccountRole = typeof ACCOUNT_ROLES[number];

type ProfileRow = { user_id: string; display_name: string };
type UserAthleteRow = { user_id: string; athlete_alias: string; is_owner: boolean };
type OrganizationRow = { id: string; name: string; slug: string };
type MembershipRow = {
  organization_id: string;
  user_id: string;
  role: AccountRole;
  status: "INVITED" | "ACTIVE" | "SUSPENDED" | "LEFT";
};
type AssignmentRow = {
  organization_id: string;
  coach_user_id: string;
  athlete_user_id: string;
  can_edit_plan: boolean;
};
type SharingGrantRow = {
  owner_user_id: string;
  viewer_user_id: string;
  edit_plan: boolean;
};
type InviteRow = {
  id: string;
  inviter_user_id: string;
  invitee_email: string;
  organization_id: string | null;
  membership_role: AccountRole | null;
  status: "PENDING" | "ACCEPTED" | "DECLINED" | "REVOKED" | "EXPIRED";
  expires_at: string;
  created_at: string;
};

export interface AccessibleAthlete {
  userId: string;
  athleteAlias: string;
  displayName: string;
  isOwner: boolean;
  canEditPlan: boolean;
}

export interface AccountMembership {
  organizationId: string;
  organizationName: string;
  organizationSlug: string;
  role: AccountRole;
}

export interface OrganizationMember {
  organizationId: string;
  userId: string;
  displayName: string;
  role: AccountRole;
  status: MembershipRow["status"];
}

export interface CoachAthleteAssignment {
  organizationId: string;
  coachUserId: string;
  athleteUserId: string;
  canEditPlan: boolean;
}

export interface AccountInvite {
  id: string;
  inviterUserId: string;
  inviteeEmail: string;
  organizationId: string;
  organizationName: string;
  role: AccountRole;
  expiresAt: string;
  incoming: boolean;
}

export interface AccountWorkspace {
  userId: string;
  displayName: string | null;
  roles: AccountRole[];
  memberships: AccountMembership[];
  accessibleAthletes: AccessibleAthlete[];
  members: OrganizationMember[];
  assignments: CoachAthleteAssignment[];
  invites: AccountInvite[];
}

export type CurrentAthleteAccess = AccessibleAthlete;

const uniqueRoles = (roles: AccountRole[]) => ACCOUNT_ROLES.filter((role) => roles.includes(role));

const canEditAthlete = ({
  userId,
  athleteUserId,
  isOwner,
  memberships,
  assignments,
  sharingGrants,
}: {
  userId: string;
  athleteUserId: string;
  isOwner: boolean;
  memberships: MembershipRow[];
  assignments: AssignmentRow[];
  sharingGrants: SharingGrantRow[];
}) => {
  if (isOwner && athleteUserId === userId) return true;
  if (sharingGrants.some((grant) => grant.owner_user_id === athleteUserId && grant.viewer_user_id === userId && grant.edit_plan))
    return true;
  const athleteOrganizations = new Set(memberships
    .filter((membership) => membership.user_id === athleteUserId && membership.role === "ATHLETE" && membership.status === "ACTIVE")
    .map((membership) => membership.organization_id));
  if (memberships.some((membership) => membership.user_id === userId
    && membership.status === "ACTIVE"
    && ["ADMIN", "HEAD_COACH"].includes(membership.role)
    && athleteOrganizations.has(membership.organization_id))) return true;
  return assignments.some((assignment) => assignment.coach_user_id === userId
    && assignment.athlete_user_id === athleteUserId
    && assignment.can_edit_plan
    && athleteOrganizations.has(assignment.organization_id));
};

export const roleLabel = (role: AccountRole) => ({
  ADMIN: "Администратор",
  HEAD_COACH: "Главен треньор",
  COACH: "Треньор",
  ATHLETE: "Спортист",
})[role];

export async function loadAccountWorkspace(): Promise<AccountWorkspace | null> {
  const supabase = await createClient({ requestTimeoutMs: 10_000 });
  const { data: claimsData } = await supabase.auth.getClaims();
  const userId = claimsData?.claims?.sub;
  if (!userId) return null;

  const [profilesResult, athletesResult, organizationsResult, membershipsResult, assignmentsResult, sharingResult, invitesResult] = await Promise.all([
    supabase.from("onflows_profiles").select("user_id, display_name"),
    supabase.from("onflows_user_athletes").select("user_id, athlete_alias, is_owner"),
    supabase.from("onflows_organizations").select("id, name, slug"),
    supabase.from("onflows_organization_memberships").select("organization_id, user_id, role, status"),
    supabase.from("onflows_coach_athlete_assignments").select("organization_id, coach_user_id, athlete_user_id, can_edit_plan"),
    supabase.from("onflows_sharing_grants").select("owner_user_id, viewer_user_id, edit_plan"),
    supabase.from("onflows_connection_invites")
      .select("id, inviter_user_id, invitee_email, organization_id, membership_role, status, expires_at, created_at")
      .eq("status", "PENDING")
      .gt("expires_at", new Date().toISOString()),
  ]);

  const firstError = [profilesResult, athletesResult, organizationsResult, membershipsResult, assignmentsResult, sharingResult, invitesResult]
    .find((result) => result.error)?.error;
  if (firstError) throw new Error("Ролевият профил временно не е достъпен.", { cause: firstError });

  const profiles = (profilesResult.data ?? []) as ProfileRow[];
  const athletes = (athletesResult.data ?? []) as UserAthleteRow[];
  const organizations = (organizationsResult.data ?? []) as OrganizationRow[];
  const memberships = (membershipsResult.data ?? []) as MembershipRow[];
  const assignments = (assignmentsResult.data ?? []) as AssignmentRow[];
  const sharingGrants = (sharingResult.data ?? []) as SharingGrantRow[];
  const invites = (invitesResult.data ?? []) as InviteRow[];
  const profileNames = new Map(profiles.map((profile) => [profile.user_id, profile.display_name]));
  const organizationsById = new Map(organizations.map((organization) => [organization.id, organization]));
  const ownMemberships = memberships.filter((membership) => membership.user_id === userId && membership.status === "ACTIVE");
  const roles = uniqueRoles([
    ...ownMemberships.map((membership) => membership.role),
    ...(athletes.some((athlete) => athlete.user_id === userId && athlete.is_owner) ? ["ATHLETE" as const] : []),
  ]);

  return {
    userId,
    displayName: profileNames.get(userId) ?? null,
    roles,
    memberships: ownMemberships.flatMap((membership) => {
      const organization = organizationsById.get(membership.organization_id);
      return organization ? [{
        organizationId: organization.id,
        organizationName: organization.name,
        organizationSlug: organization.slug,
        role: membership.role,
      }] : [];
    }),
    accessibleAthletes: athletes.map((athlete) => ({
      userId: athlete.user_id,
      athleteAlias: athlete.athlete_alias,
      displayName: profileNames.get(athlete.user_id) ?? "Спортист",
      isOwner: athlete.user_id === userId && athlete.is_owner,
      canEditPlan: canEditAthlete({
        userId,
        athleteUserId: athlete.user_id,
        isOwner: athlete.is_owner,
        memberships,
        assignments,
        sharingGrants,
      }),
    })).sort((left, right) => left.displayName.localeCompare(right.displayName, "bg")),
    members: memberships.map((membership) => ({
      organizationId: membership.organization_id,
      userId: membership.user_id,
      displayName: profileNames.get(membership.user_id) ?? "Потребител",
      role: membership.role,
      status: membership.status,
    })),
    assignments: assignments.map((assignment) => ({
      organizationId: assignment.organization_id,
      coachUserId: assignment.coach_user_id,
      athleteUserId: assignment.athlete_user_id,
      canEditPlan: assignment.can_edit_plan,
    })),
    invites: invites.flatMap((invite) => {
      if (!invite.organization_id || !invite.membership_role) return [];
      const organization = organizationsById.get(invite.organization_id);
      if (!organization) return [];
      return [{
        id: invite.id,
        inviterUserId: invite.inviter_user_id,
        inviteeEmail: invite.invitee_email,
        organizationId: invite.organization_id,
        organizationName: organization.name,
        role: invite.membership_role,
        expiresAt: invite.expires_at,
        incoming: invite.inviter_user_id !== userId,
      }];
    }),
  };
}

export async function currentAuthorizedAthlete(): Promise<CurrentAthleteAccess | null> {
  const athleteAlias = await currentAthleteAlias();
  if (!athleteAlias) return null;
  try {
    const supabase = await createClient({ requestTimeoutMs: 7_500 });
    const { data: claimsData } = await supabase.auth.getClaims();
    if (!claimsData?.claims?.sub) return null;
    const { data: athlete, error } = await supabase.from("onflows_user_athletes")
      .select("user_id, athlete_alias, is_owner")
      .eq("athlete_alias", athleteAlias)
      .maybeSingle<UserAthleteRow>();
    if (error || !athlete) return null;
    const [profileResult, membershipsResult, assignmentsResult, sharingResult] = await Promise.all([
      supabase.from("onflows_profiles")
        .select("display_name")
        .eq("user_id", athlete.user_id)
        .maybeSingle<{ display_name: string }>(),
      supabase.from("onflows_organization_memberships")
        .select("organization_id, user_id, role, status"),
      supabase.from("onflows_coach_athlete_assignments")
        .select("organization_id, coach_user_id, athlete_user_id, can_edit_plan"),
      supabase.from("onflows_sharing_grants")
        .select("owner_user_id, viewer_user_id, edit_plan"),
    ]);
    if (membershipsResult.error || assignmentsResult.error || sharingResult.error) return null;
    return {
      userId: athlete.user_id,
      athleteAlias: athlete.athlete_alias,
      displayName: profileResult.data?.display_name ?? "Спортист",
      isOwner: athlete.user_id === claimsData.claims.sub && athlete.is_owner,
      canEditPlan: canEditAthlete({
        userId: claimsData.claims.sub,
        athleteUserId: athlete.user_id,
        isOwner: athlete.is_owner,
        memberships: (membershipsResult.data ?? []) as MembershipRow[],
        assignments: (assignmentsResult.data ?? []) as AssignmentRow[],
        sharingGrants: (sharingResult.data ?? []) as SharingGrantRow[],
      }),
    };
  } catch {
    return null;
  }
}

export async function currentAccountRoles(): Promise<AccountRole[]> {
  try {
    const supabase = await createClient({ requestTimeoutMs: 7_500 });
    const { data: claimsData } = await supabase.auth.getClaims();
    const userId = claimsData?.claims?.sub;
    if (!userId) return [];
    const [membershipsResult, athletesResult] = await Promise.all([
      supabase.from("onflows_organization_memberships")
        .select("role")
        .eq("user_id", userId)
        .eq("status", "ACTIVE"),
      supabase.from("onflows_user_athletes")
        .select("is_owner")
        .eq("user_id", userId)
        .eq("is_owner", true),
    ]);
    if (membershipsResult.error || athletesResult.error) return [];
    const membershipRoles = (membershipsResult.data ?? []) as Array<{ role: AccountRole }>;
    return uniqueRoles([
      ...membershipRoles.map((membership) => membership.role),
      ...((athletesResult.data?.length ?? 0) > 0 ? ["ATHLETE" as const] : []),
    ]);
  } catch {
    return [];
  }
}
