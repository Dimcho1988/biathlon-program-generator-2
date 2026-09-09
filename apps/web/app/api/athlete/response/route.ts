import { NextResponse } from "next/server";
import { currentAuthorizedAthlete } from "../../../../lib/account-access";
import { waitForApi } from "../../../../lib/api-readiness";
import { isSameOrigin } from "../../../../lib/account-route";

export async function POST(request:Request) {
  try {
    if (!isSameOrigin(request)) return NextResponse.json({error:"Невалиден източник на заявката."},{status:403});
    const access = await currentAuthorizedAthlete();
    if (!access) return NextResponse.json({error:"Влезте отново в профила си."},{status:401});
    if (!access.canViewRecovery) return NextResponse.json({error:"Нямате достъп до данните за възстановяване."},{status:403});
    if (Number(request.headers.get("content-length")||0)>20000) return NextResponse.json({error:"Твърде голяма заявка."},{status:413});
    const text = await request.text();
    if (new TextEncoder().encode(text).length>20000) return NextResponse.json({error:"Твърде голяма заявка."},{status:413});
    let input;
    try { input=JSON.parse(text); } catch {return NextResponse.json({error:"Невалидна оценка."},{status:422});}
    const {kind,payload} = input || {};
    if (!["daily","session","block","test"].includes(kind) || !payload || typeof payload!=="object") return NextResponse.json({error:"Невалидна оценка."},{status:422});
    if ((kind==="daily" || kind==="session") ? !access.isOwner : !access.canEditPlan) return NextResponse.json({error:"Нямате право да променяте тази оценка."},{status:403});
    const base = process.env.ONFLOWS_API_BASE_URL;
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!base || !token || !access.actorUserId) throw new Error("Missing server configuration");
    await waitForApi(base);
    const result = await fetch(new URL(`/api/v2/athlete/response/${kind}`,base),{method:"PUT",cache:"no-store",
      headers:{Authorization:`Bearer ${token}`,"Content-Type":"application/json","X-OnFlows-Athlete-Alias":access.athleteAlias,"X-OnFlows-Actor-Id":access.actorUserId},
      body:JSON.stringify(payload),signal:AbortSignal.timeout(75000)});
    if (!result.ok) return NextResponse.json({error:result.status===409?"Записът е променен или периодът вече е започнал. Презаредете и проверете датите.":result.status===422?"Проверете датите, часа и стойностите.":"Записването временно не е достъпно. Въведеното е запазено във формата."},{status:[404,409,422].includes(result.status)?result.status:503});
    return NextResponse.json(await result.json());
  } catch {
    return NextResponse.json({error:"Неуспешно записване. Въведеното остава във формата — опитайте отново."},{status:503});
  }
}
