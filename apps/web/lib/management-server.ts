import "server-only";
import { fetchApiResource } from "./api";
import { isRecord } from "./training-status";
import { parseSyncState } from "./sync";
import { parseManagementProfileResponse, parseManagementOutlook, parseDrafts, parseActivePlanResponse } from "./training-management";

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
  const payload = await read(`view?view=${view}`, athleteAlias, actorUserId);
  if (!isRecord(payload)) throw new Error("Управлението временно не е достъпно.");
  return {
    profile: parseManagementProfileResponse(payload.profile),
    active: parseActivePlanResponse(payload.active),
    drafts: parseDrafts(payload.drafts),
    outlook: payload.outlook == null ? null : parseManagementOutlook(payload.outlook),
    sync: payload.sync == null ? null : parseSyncState(payload.sync),
    inputFingerprint: isRecord(payload.drafts) && typeof payload.drafts.current_input_fingerprint === "string"
      ? payload.drafts.current_input_fingerprint : "",
  };
}
