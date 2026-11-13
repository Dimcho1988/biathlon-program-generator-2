import {NextResponse} from "next/server";
import {currentAuthorizedAthlete} from "../../../../../lib/account-access";
import {matchesManagementScope} from "../../../../../lib/management-scope";
const headers = {"Cache-Control": "no-store"};
const fail = (status: number) => NextResponse.json({error: "Състоянието временно не е достъпно."}, {status, headers});
export async function GET(request: Request) {
  try {
    const access = await currentAuthorizedAthlete();
    if (!access) return fail(401);
    if (!matchesManagementScope(request, access)) return fail(412);
    if (!access.canViewPlan) return fail(403);
    const view = new URL(request.url).searchParams.get("view") ?? "week";
    if (!["week", "overview"].includes(view)) return fail(422);
    const base = process.env.ONFLOWS_API_BASE_URL, token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!base || !token) return fail(503);
    const url = new URL("/api/v2/athlete/management/preparation-status", base);
    url.searchParams.set("view", view);
    const response = await fetch(url, {cache: "no-store", signal: AbortSignal.timeout(10_000), headers: {
      Authorization: `Bearer ${token}`, "X-OnFlows-Athlete-Alias": access.athleteAlias, "X-OnFlows-Actor-Id": access.actorUserId,
    }});
    if (!response.ok) return fail([401,403,412,422,429].includes(response.status) ? response.status : 503);
    const result = await response.json();
    if (typeof result?.ready !== "boolean") return fail(503);
    return NextResponse.json({ready: result.ready, limited: result.limited === true}, {headers});
  } catch { return fail(503); }
}
