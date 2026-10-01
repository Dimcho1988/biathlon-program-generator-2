import {NextResponse} from "next/server";
import {currentAuthorizedAthlete} from "../../../../../lib/account-access";
import {getSpeedLoad} from "../../../../../lib/api";

export async function GET(request:Request) {
  try {
    const access=await currentAuthorizedAthlete();
    if(!access)return NextResponse.json({error:"Влезте в профила си."},{status:401});
    if(!access.canViewRecovery)return NextResponse.json({error:"Нямате достъп до тези данни."},{status:403});
    const sport=new URL(request.url).searchParams.get("sport")||undefined;
    if(sport&&!/^[A-Za-z]{2,40}$/.test(sport))return NextResponse.json({error:"Невалидно средство."},{status:422});
    return NextResponse.json(await getSpeedLoad(access.athleteAlias,sport),{headers:{"Cache-Control":"private, no-store"}});
  } catch {
    return NextResponse.json({error:"Отчетът по скорост временно не е достъпен."},{status:503});
  }
}
