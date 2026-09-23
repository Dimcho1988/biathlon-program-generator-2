import "server-only";
import { waitForApi } from "./api-readiness";
import { parseManagementProfileResponse, parseManagementOutlook } from "./training-management";
export async function getManagementProfile(athleteAlias: string, actorUserId: string) {
  const base = process.env.ONFLOWS_API_BASE_URL, token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!base || !token) throw new Error("Профилът временно не е достъпен.");
  await waitForApi(base);
  const response = await fetch(new URL("/api/v2/athlete/management/profile", base), { cache: "no-store", signal: AbortSignal.timeout(75_000), headers: { Authorization: `Bearer ${token}`, "X-OnFlows-Athlete-Alias": athleteAlias, "X-OnFlows-Actor-Id": actorUserId } });
  if (!response.ok) throw new Error("Профилът временно не е достъпен.");
  return parseManagementProfileResponse(await response.json());
}

export async function getManagementOutlook(athleteAlias: string, actorUserId: string) {
  const base=process.env.ONFLOWS_API_BASE_URL, token=process.env.ONFLOWS_SERVICE_TOKEN;
  if (!base||!token) return null;
  await waitForApi(base);
  const response=await fetch(new URL("/api/v2/athlete/management/outlook",base),{cache:"no-store",signal:AbortSignal.timeout(75_000),headers:{Authorization:`Bearer ${token}`,"X-OnFlows-Athlete-Alias":athleteAlias,"X-OnFlows-Actor-Id":actorUserId}});
  if(!response.ok) return null;
  return parseManagementOutlook(await response.json());
}
