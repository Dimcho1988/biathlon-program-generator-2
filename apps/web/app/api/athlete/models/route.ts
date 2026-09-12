import { NextResponse } from "next/server";
import { currentAuthorizedAthlete } from "../../../../lib/account-access";
import { isSameOrigin } from "../../../../lib/account-route";
import { waitForApi } from "../../../../lib/api-readiness";

export async function POST(request:Request){
  try{
    if(!isSameOrigin(request))return NextResponse.json({error:"Невалиден източник."},{status:403});
    const access=await currentAuthorizedAthlete();
    if(!access)return NextResponse.json({error:"Влезте в профила си."},{status:401});
    if(!access.canEditPlan||!access.canViewRecovery)return NextResponse.json({error:"Нямате право да променяте модела на този спортист."},{status:403});
    if(Number(request.headers.get("content-length")||0)>20000)return NextResponse.json({error:"Твърде голяма заявка."},{status:413});
    const text=await request.text();
    if(new TextEncoder().encode(text).length>20000)return NextResponse.json({error:"Твърде голяма заявка."},{status:413});
    let input;
    try{input=JSON.parse(text);}catch{return NextResponse.json({error:"Невалидни настройки."},{status:422});}
    const {kind,payload}=input||{};
    if(!["recovery","speed-test"].includes(kind)||!payload||typeof payload!=="object")return NextResponse.json({error:"Невалидни настройки."},{status:422});
    const base=process.env.ONFLOWS_API_BASE_URL,token=process.env.ONFLOWS_SERVICE_TOKEN;
    if(!base||!token||!access.actorUserId)throw new Error("Missing configuration");
    await waitForApi(base);
    const result=await fetch(new URL(`/api/v2/athlete/models/${kind}`,base),{method:"PUT",cache:"no-store",
      headers:{Authorization:`Bearer ${token}`,"Content-Type":"application/json","X-OnFlows-Athlete-Alias":access.athleteAlias,"X-OnFlows-Actor-Id":access.actorUserId},
      body:JSON.stringify(payload),signal:AbortSignal.timeout(75000)});
    if(!result.ok)return NextResponse.json({error:result.status===409?"Данните са променени или липсва необходимият анализ. Презаредете.":result.status===422?"Проверете стойностите. За теста е нужен непрекъснат максимален участък с достатъчно Vflat данни и съгласуваност с останалите тестове.":"Записването временно не е достъпно."},{status:[404,409,422].includes(result.status)?result.status:503});
    return NextResponse.json(await result.json());
  }catch{return NextResponse.json({error:"Неуспешен запис. Въведеното остава във формата."},{status:503});}
}
