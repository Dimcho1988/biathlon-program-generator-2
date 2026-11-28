import { NextResponse } from "next/server";
import { createClient } from "../../../../lib/supabase/server";

const WEB = "https://onflows-web-staging.onrender.com";
const CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect";
const headers = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" };

function failure(text: string, status: number) {
  return new NextResponse(text, { status, headers });
}

export async function POST(request: Request) {
  if (request.headers.get("origin") !== WEB)
    return failure("Невалиден източник на заявката.", 403);
  const supabase = await createClient({ requestTimeoutMs: 10000 });
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return failure("Влез отново в onFlows.", 401);
  const form = await request.formData();
  const decision = form.get("decision");
  if (decision !== "revoke" && form.get("displayedAccount") !== user.id)
    return failure("Акаунтът е сменен. Отвори свързването отново.", 409);
  const api = process.env.ONFLOWS_API_BASE_URL, token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!api || !token) return failure("Връзката временно не е достъпна.", 503);
  const endpoint = "/api/v2/integrations/chatgpt/";
  const init = { method: "POST", cache: "no-store" as const, signal: AbortSignal.timeout(20000),
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } };
  if (decision === "revoke") {
    const result = await fetch(new URL(endpoint + "disconnect/" + user.id, api), init);
    if (!result.ok) return failure("Достъпът не е прекратен. Опитай отново.", 503);
    return NextResponse.redirect(new URL("/account?saved=chatgpt-revoked", WEB), { status: 303, headers });
  }
  if (decision !== "approve" && decision !== "deny") return failure("Невалиден избор.", 400);
  const athletes = form.getAll("athlete").map(String);
  if (decision === "approve" && !athletes.length) return failure("Върни се и избери поне един спортист.", 400);
  const params = new URLSearchParams(String(form.get("params") ?? ""));
  const result = await fetch(new URL(endpoint + "consent", api), { ...init, body: JSON.stringify({
    actor: user.id, athlete_aliases: athletes, approve: decision === "approve",
    allow_write: form.get("allowWrite") === "on", params: Object.fromEntries(params),
  }) });
  if (!result.ok) return failure(result.status === 403 ? "Нямаш необходимите права за избрания профил." :
    "Свързването не е завършено. Върни се в ChatGPT и започни отново.", result.status);
  const target = new URL((await result.json()).location);
  if (target.origin + target.pathname !== CALLBACK) return failure("Невалиден адрес за връщане.", 502);
  return NextResponse.redirect(target, { status: 303, headers });
}
