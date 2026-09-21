import { NextResponse } from "next/server";
import { currentAuthorizedAthlete } from "../../../../../lib/account-access";
import { waitForApi } from "../../../../../lib/api-readiness";
import { isSameOrigin } from "../../../../../lib/account-route";
import { isCalendarDate, isRecord } from "../../../../../lib/training-status";
import { parseManagementProfile } from "../../../../../lib/training-management";

type Context = { params: Promise<{ path: string[] }> };
const error = (message: string, status: number) => NextResponse.json({ error: message }, { status, headers: { "Cache-Control": "no-store" } });
const revision = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0;

async function proxy(request: Request, context: Context) {
  try {
    const { path } = await context.params;
    const endpoint = path.length === 1 ? path[0] : "";
    const permitted = request.method === "GET" ? ["profile", "drafts"] : request.method === "PUT" ? ["profile"] : ["generate"];
    if (!permitted.includes(endpoint)) return error("Адресът не е намерен.", 404);
    const startDate = request.method === "GET" && endpoint === "drafts" ? new URL(request.url).searchParams.get("start_date") : null;
    if (startDate !== null && !isCalendarDate(startDate)) return error("Невалидна начална дата на програмата.", 422);
    const write = request.method !== "GET";
    if (write && !isSameOrigin(request)) return error("Невалиден източник на заявката.", 403);
    const access = await currentAuthorizedAthlete();
    if (!access) return error("Влезте отново в профила си.", 401);
    if (!access.canViewPlan) return error("Този спортист не е споделил тренировъчния си план с вас.", 403);
    if (write && !access.canEditPlan) return error("Този профил е достъпен само за преглед.", 403);
    let body: string | undefined;
    if (write) {
      if (Number(request.headers.get("content-length") ?? 0) > 20_000) return error("Твърде голяма заявка.", 413);
      const raw = await request.text();
      if (new TextEncoder().encode(raw).length > 20_000) return error("Твърде голяма заявка.", 413);
      let input: unknown;
      try { input = JSON.parse(raw); } catch { return error("Невалидни данни.", 422); }
      if (!isRecord(input)) return error("Невалидни данни.", 422);
      if (endpoint === "profile") {
        if (!revision(input.expected_revision)) return error("Липсва версия на профила. Презаредете страницата.", 422);
        try { body = JSON.stringify({ profile: parseManagementProfile(input.profile), expected_revision: input.expected_revision }); }
        catch (caught) { return error(caught instanceof Error ? caught.message : "Невалиден профил.", 422); }
      } else {
        if (!isCalendarDate(input.start_date) || !revision(input.expected_profile_revision) || !revision(input.expected_draft_revision)) return error("Проверете началната дата и версията на програмата.", 422);
        body = JSON.stringify({ start_date: input.start_date, expected_profile_revision: input.expected_profile_revision, expected_draft_revision: input.expected_draft_revision });
      }
    }
    const base = process.env.ONFLOWS_API_BASE_URL;
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!base || !token || !access.actorUserId) return error("Услугата за управление временно не е достъпна.", 503);
    await waitForApi(base);
    const url = new URL(`/api/v2/athlete/management/${endpoint}`, base);
    if (startDate !== null) url.searchParams.set("start_date", startDate);
    const response = await fetch(url, {
      method: request.method, cache: "no-store", signal: AbortSignal.timeout(75_000),
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "X-OnFlows-Athlete-Alias": access.athleteAlias, "X-OnFlows-Actor-Id": access.actorUserId },
      ...(body ? { body } : {}),
    });
    if (!response.ok) return error(response.status === 409
      ? "Профилът, програмата или входните данни са променени. Презаредете и прегледайте последната версия."
      : response.status === 422 ? "Проверете профила, периода и календара. Програмата не беше записана."
        : "Управлението временно не е достъпно. Въведените стойности остават във формата.", [404, 409, 422].includes(response.status) ? response.status : 503);
    return NextResponse.json(await response.json(), { headers: { "Cache-Control": "no-store" } });
  } catch { return error("Управлението временно не е достъпно. Опитайте отново.", 503); }
}

export const GET = proxy;
export const PUT = proxy;
export const POST = proxy;
