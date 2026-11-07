import "server-only";
import { fetchApiResource } from "./api";
import { parseManagementProfileResponse, parseManagementOutlook } from "./training-management";
import { parseManagementView } from "./management-overview";

async function read(path: string, athleteAlias: string, actorUserId: string) {
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!token) throw new Error("Профилът временно не е достъпен.");
  return fetchApiResource(`/api/v2/athlete/management/${path}`, token, athleteAlias, { actorUserId });
}

export async function getManagementProfile(athleteAlias: string, actorUserId: string) {
  return parseManagementProfileResponse(await read("profile", athleteAlias, actorUserId));
}

export async function getManagementOutlook(athleteAlias: string, actorUserId: string) {
  if (!process.env.ONFLOWS_API_BASE_URL || !process.env.ONFLOWS_SERVICE_TOKEN) return null;
  return parseManagementOutlook(await read("outlook", athleteAlias, actorUserId));
}

export async function getManagementView(athleteAlias: string, actorUserId: string, view: "week" | "overview") {
  const payload = await read(`view?view=${view}${view === "overview" ? "&projection=overview-v1" : ""}`, athleteAlias, actorUserId);
  return parseManagementView(payload);
}
