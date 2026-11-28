import { NextResponse } from "next/server";
import { currentAuthorizedAthlete } from "../../../../../lib/account-access";
import { isSameOrigin } from "../../../../../lib/account-route";
import { waitForApi } from "../../../../../lib/api-readiness";
export async function POST(request: Request) {
  if (!isSameOrigin(request))
    return NextResponse.json({ error: "Невалиден източник." }, { status: 403 });
  const a = await currentAuthorizedAthlete();
  if (!a)
    return NextResponse.json({ error: "Влезте отново." }, { status: 401 });
  if (!a.canViewRecovery || !(a.isOwner || a.canEditPlan))
    return NextResponse.json(
      { error: "Нямате право за редакция." },
      { status: 403 },
    );
  try {
    const text = await request.text();
    if (new TextEncoder().encode(text).length > 10000)
      return NextResponse.json(
        { error: "Твърде голяма заявка." },
        { status: 413 },
      );
    let input;
    try {
      input = JSON.parse(text);
    } catch {
      return NextResponse.json({ error: "Невалидни данни." }, { status: 422 });
    }
    const base = process.env.ONFLOWS_API_BASE_URL,
      token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!base || !token || !a.actorUserId)
      throw new Error("Missing configuration");
    await waitForApi(base);
    const r = await fetch(new URL("/api/v2/athlete/energy/profile", base), {
      method: "PUT",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "X-OnFlows-Athlete-Alias": a.athleteAlias,
        "X-OnFlows-Actor-Id": a.actorUserId,
      },
      body: JSON.stringify(input),
      signal: AbortSignal.timeout(75000),
    });
    if (!r.ok)
      return NextResponse.json(
        {
          error:
            r.status === 409
              ? "Данните са променени. Презаредете."
              : r.status === 422
                ? "Проверете въведените стойности."
                : "Записът не успя.",
        },
        { status: [409, 422].includes(r.status) ? r.status : 503 },
      );
    return NextResponse.json(await r.json());
  } catch {
    return NextResponse.json(
      { error: "Записът не успя. Въведеното остава във формата." },
      { status: 503 },
    );
  }
}
