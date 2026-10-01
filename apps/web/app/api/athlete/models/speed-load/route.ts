import {isCalendarDate} from "../../../../../lib/training-status";
import {NextResponse} from "next/server";
import {currentAuthorizedAthlete} from "../../../../../lib/account-access";
import {getSpeedLoad} from "../../../../../lib/api";

export async function GET(request:Request) {
  try {
    const access=await currentAuthorizedAthlete();
    if(!access)return NextResponse.json({error:"Влезте в профила си."},{status:401});
    if(!access.canViewRecovery)return NextResponse.json({error:"Нямате достъп до тези данни."},{status:403});
    const params = new URL(request.url).searchParams;
    const sport=params.get("sport")||undefined;
    const start=params.get("period_start")||undefined, end=params.get("period_end")||undefined;
    if((start || end) && (!isCalendarDate(start) || !isCalendarDate(end) || start! > end! || Date.parse(end!)-Date.parse(start!) >= 366*86400000))
      return NextResponse.json({error:"Изберете валиден период от 1 до 366 дни."},{status:422});
    if(sport&&!/^[A-Za-z]{2,40}$/.test(sport))return NextResponse.json({error:"Невалидно средство."},{status:422});
    return NextResponse.json(await getSpeedLoad(access.athleteAlias,sport,start,end),{headers:{"Cache-Control":"private, no-store"}});
  } catch {
    return NextResponse.json({error:"Отчетът по скорост временно не е достъпен."},{status:503});
  }
}
