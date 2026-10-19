import { matchesManagementScope } from "../../../../../lib/management-scope";
import { revalidatePath } from "next/cache";
import { NextResponse } from "next/server";
import { currentAuthorizedAthlete } from "../../../../../lib/account-access";
import { waitForApi } from "../../../../../lib/api-readiness";
import { isSameOrigin } from "../../../../../lib/account-route";
import { isCalendarDate, isRecord } from "../../../../../lib/training-status";
import { parseManagementProfile } from "../../../../../lib/training-management";
import { METHOD_PHASES, parseMethodPreferences } from "../../../../../lib/method-library";

type Context = { params: Promise<{ path: string[] }> };
const error = (message: string, status: number) => NextResponse.json({ error: message }, { status, headers: { "Cache-Control": "no-store" } });
const revision = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0;

async function proxy(request: Request, context: Context) {
  try {
    const { path } = await context.params;
    const endpoint = path.length === 1 ? path[0] : path.length === 2 && path[0] === "methods" && path[1] === "preferences" ? "methods/preferences" : "";
    const permitted = request.method === "GET" ? ["profile", "drafts", "active", "state", "outlook", "methods", "checks"] : request.method === "PUT" ? ["profile", "methods/preferences"] : ["generate", "activate", "action", "day", "race-duration"];
    if (!permitted.includes(endpoint)) return error("Адресът не е намерен.", 404);
    const startDate = request.method === "GET" && endpoint === "drafts" ? new URL(request.url).searchParams.get("start_date") : null;
    if (startDate !== null && !isCalendarDate(startDate)) return error("Невалидна начална дата на програмата.", 422);
    const phase = request.method === "GET" && endpoint === "methods" ? new URL(request.url).searchParams.get("phase") : null;
    if (phase !== null && !Object.hasOwn(METHOD_PHASES, phase)) return error("Невалиден период на подготовката.", 422);
    const checksQuery = request.method === "GET" && endpoint === "checks" ? new URL(request.url).searchParams : null;
    const before = checksQuery?.get("before") ?? null;
    const limit = checksQuery?.get("limit") ?? null;
    const positiveQuery = (value: string) => /^[1-9]\d*$/.test(value) && Number.isSafeInteger(Number(value));
    if ((before !== null && !positiveQuery(before)) || (limit !== null && (!positiveQuery(limit) || Number(limit) > 50)))
      return error("Невалидна страница с проверки на програмата.", 422);
    const write = request.method !== "GET";
    if (write && !isSameOrigin(request)) return error("Невалиден източник на заявката.", 403);
    const access = await currentAuthorizedAthlete();
    if (!access) return error("Влезте отново в профила си.", 401);
    if (!matchesManagementScope(request, access)) return error("Профилът е сменен. Презареди страницата.", 412);
    if (!access.canViewPlan) return error("Този спортист не е споделил тренировъчния си план с вас.", 403);
    if (write && !access.canEditPlan) return error("Този профил е достъпен само за преглед.", 403);
    let body: string | undefined;
    let plansWeek = endpoint === "generate" || endpoint === "day";
    if (write) {
      if (Number(request.headers.get("content-length") ?? 0) > 20_000) return error("Твърде голяма заявка.", 413);
      const raw = await request.text();
      if (new TextEncoder().encode(raw).length > 20_000) return error("Твърде голяма заявка.", 413);
      let input: unknown;
      try { input = JSON.parse(raw); } catch { return error("Невалидни данни.", 422); }
      if (!isRecord(input)) return error("Невалидни данни.", 422);
      if (endpoint === "methods/preferences") {
        if (!revision(input.expected_revision)) return error("Липсва версия на профила. Зареди последната версия.", 422);
        try { body = JSON.stringify({ expected_revision: input.expected_revision, preferences: parseMethodPreferences(input.preferences) }); }
        catch (caught) { return error(caught instanceof Error ? caught.message : "Невалидни настройки на методите.", 422); }
      } else if (endpoint === "profile") {
        if (!revision(input.expected_revision)) return error("Липсва версия на профила. Презаредете страницата.", 422);
        try { body = JSON.stringify({ profile: parseManagementProfile(input.profile), expected_revision: input.expected_revision }); }
        catch (caught) { return error(caught instanceof Error ? caught.message : "Невалиден профил.", 422); }
      } else if (endpoint === "race-duration") {
        if (typeof input.discipline !== "string" || !input.discipline.trim() || input.discipline.length > 100
          || !["Run", "NordicSki"].includes(String(input.sport))
          || (input.race_duration_min != null && (typeof input.race_duration_min !== "number" || !Number.isFinite(input.race_duration_min) || input.race_duration_min <= 0 || input.race_duration_min > 1440)))
          return error("Провери дисциплината и продължителността.", 422);
        body = JSON.stringify({ discipline: input.discipline, sport: input.sport, race_duration_min: input.race_duration_min ?? null });
      } else if (endpoint === "activate") {
        if (!isCalendarDate(input.start_date) || !revision(input.draft_revision) || Number(input.draft_revision) < 1 || !revision(input.expected_revision)) return error("Проверете версията на проекта.", 422);
        body = JSON.stringify({ start_date: input.start_date, draft_revision: input.draft_revision, expected_revision: input.expected_revision });
      } else if (endpoint === "action") {
        if (!["PAUSE", "RESUME", "REFRESH", "APPROVE"].includes(String(input.action)) || !revision(input.expected_revision) || Number(input.expected_revision) < 1) return error("Невалидно действие за програмата.", 422);
        if ((input.record_unchanged_check !== undefined && typeof input.record_unchanged_check !== "boolean")
          || (input.record_unchanged_check === true && input.action !== "REFRESH")) return error("Невалидна проверка на програмата.", 422);
        plansWeek = input.action === "REFRESH" || input.action === "RESUME";
        body = JSON.stringify({ action: input.action, expected_revision: input.expected_revision,
          ...(input.record_unchanged_check === true ? { record_unchanged_check: true } : {}) });
      } else if (endpoint === "day") {
        if (!isCalendarDate(input.date) || !["SKIP", "REST", "CLEAR"].includes(String(input.action)) || !revision(input.expected_revision) || Number(input.expected_revision) < 1 || typeof input.note !== "string" || input.note.length > 250) return error("Проверете деня и избраното действие.", 422);
        body = JSON.stringify({ date: input.date, action: input.action, note: input.note, expected_revision: input.expected_revision });
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
    if (phase !== null) url.searchParams.set("phase", phase);
    if (before !== null) url.searchParams.set("before", before);
    if (limit !== null) url.searchParams.set("limit", limit);
    const response = await fetch(url, {
      method: request.method, cache: "no-store", signal: request.method === "GET"
        ? AbortSignal.any([request.signal, AbortSignal.timeout(75_000)])
        // Planning can finish after the ordinary read/write deadline on staging.
        // Keep one bounded mutation; never retry a potentially committed write.
        : AbortSignal.timeout(plansWeek ? 180_000 : 75_000),
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "X-OnFlows-Athlete-Alias": access.athleteAlias, "X-OnFlows-Actor-Id": access.actorUserId },
      ...(body ? { body } : {}),
    });
    if (!response.ok) return error(response.status === 409
      ? "Профилът, програмата или входните данни са променени. Презаредете и прегледайте последната версия."
      : response.status === 422 ? endpoint === "methods/preferences" ? "Провери диапазоните. Методът трябва да поддържа избраната корекция; комбинираните отсечки имат собствена структура. Настройките не бяха записани." : "Проверете профила, периода и календара. Програмата не беше записана."
        : "Управлението временно не е достъпно. Въведените стойности остават във формата.", [404, 409, 422].includes(response.status) ? response.status : 503);
    if (request.method !== "GET" && endpoint !== "race-duration") {
      revalidatePath("/planning");
      revalidatePath("/management");
      revalidatePath("/management/outlook");
      revalidatePath("/methods");
    }
    return NextResponse.json(await response.json(), { headers: { "Cache-Control": "no-store" } });
  } catch { return error("Управлението временно не е достъпно. Опитайте отново.", 503); }
}

export const GET = proxy;
export const PUT = proxy;
export const POST = proxy;
