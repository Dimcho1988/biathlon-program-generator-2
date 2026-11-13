import type { CurrentAthleteAccess } from "./account-access";

export function matchesManagementScope(request: Request, access: CurrentAthleteAccess): boolean {
  return request.headers.get("X-Onflows-Athlete") === access.athleteAlias
    && request.headers.get("X-Onflows-Actor") === access.actorUserId;
}
