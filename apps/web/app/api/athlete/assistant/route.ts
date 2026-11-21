import { NextResponse } from "next/server";
import { currentAuthorizedAthlete } from "../../../../lib/account-access";
import { isSameOrigin, UUID_PATTERN } from "../../../../lib/account-route";
import { matchesManagementScope } from "../../../../lib/management-scope";
import { waitForApi } from "../../../../lib/api-readiness";
import { isRecord } from "../../../../lib/training-status";

const headers = { "Cache-Control": "no-store" };
const fail = (error: string, status: number) => NextResponse.json({ error }, { status, headers });

async function proxy(request: Request) {
  try {
    if (request.method !== "GET" && !isSameOrigin(request)) return fail("Невалиден източник на заявката.", 403);
    const access = await currentAuthorizedAthlete();
    if (!access) return fail("Влез отново в профила си.", 401);
    if (!matchesManagementScope(request, access)) return fail("Профилът е сменен. Започни нов разговор.", 412);
    if (!access.canViewPlan && !access.canViewRecovery) return fail("Нямаш достъп до данните на този спортист.", 403);
    const url = new URL("/api/v2/athlete/assistant", process.env.ONFLOWS_API_BASE_URL || "http://invalid");
    let body: string | undefined;
    if (request.method === "GET") {
      const params = new URL(request.url).searchParams;
      for (const [key, value] of params) {
        if (!["conversation_id", "operation_id"].includes(key) || !UUID_PATTERN.test(value)) return fail("Невалиден разговор или заявка.", 422);
        url.searchParams.set(key, value);
      }
    } else {
      if (Number(request.headers.get("content-length") ?? 0) > 20_000) return fail("Твърде голяма заявка.", 413);
      const raw = await request.text();
      if (new TextEncoder().encode(raw).length > 20_000) return fail("Твърде голяма заявка.", 413);
      let input: unknown;
      try { input = JSON.parse(raw); } catch { return fail("Невалидно съобщение.", 422); }
      if (!isRecord(input)) return fail("Невалидно съобщение.", 422);
      if (request.method === "DELETE") {
        if (Object.keys(input).some(key => key !== "conversation_id") || typeof input.conversation_id !== "string" || !UUID_PATTERN.test(input.conversation_id)) return fail("Посочи точния разговор.", 422);
        body = JSON.stringify({ conversation_id: input.conversation_id });
      } else {
        if (Object.keys(input).some(key => !["conversation_id", "operation_id", "message"].includes(key))
          || typeof input.operation_id !== "string" || !UUID_PATTERN.test(input.operation_id)
          || (input.conversation_id != null && (typeof input.conversation_id !== "string" || !UUID_PATTERN.test(input.conversation_id)))
          || typeof input.message !== "string" || !input.message.trim() || input.message.length > 4000) return fail("Провери съобщението и заявката.", 422);
        body = JSON.stringify({ conversation_id: input.conversation_id ?? null, operation_id: input.operation_id, message: input.message });
      }
    }
    const base = process.env.ONFLOWS_API_BASE_URL;
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!base || !token) return fail("Асистентът още не е конфигуриран за тази среда.", 503);
    await waitForApi(base);
    // The API rechecks database grants for every tool, write and cached receipt.
    const result = await fetch(url, {
      // A confirmed plan action uses the same synchronous lifecycle as the
      // management form. Model reasoning retains its separate API deadline.
      method: request.method, cache: "no-store", signal: AbortSignal.timeout(request.method === "POST" ? 360_000 : 20_000),
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "X-OnFlows-Athlete-Alias": access.athleteAlias, "X-OnFlows-Actor-Id": access.actorUserId },
      ...(body ? { body } : {}),
    });
    if (!result.ok) {
      let detail: unknown;
      try { detail = (await result.json()).detail; } catch { /* bounded public fallback */ }
      return fail(typeof detail === "string" ? detail : "Асистентът временно не е достъпен. Можеш да използваш останалите екрани.", [401, 403, 404, 409, 412, 422, 429].includes(result.status) ? result.status : 503);
    }
    return NextResponse.json(await result.json(), { headers });
  } catch {
    return fail("Не получихме потвърден отговор. Провери статуса на същата заявка преди повторение.", 503);
  }
}
export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
