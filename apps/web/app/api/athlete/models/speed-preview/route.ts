import {NextResponse} from "next/server";
import {currentAuthorizedAthlete} from "../../../../../lib/account-access";
import {getSpeedPreview} from "../../../../../lib/api";

export async function GET(request:Request) {
  try {
    const access=await currentAuthorizedAthlete();
    if(!access)return NextResponse.json({error:"Влезте в профила си."},{status:401});
    if(!access.canViewRecovery)return NextResponse.json({error:"Нямате достъп до данните на този спортист."},{status:403});
    const params=new URL(request.url).searchParams;
    const ref=params.get("activity_ref")||"",start=params.get("start_s"),duration=params.get("duration_s");
    if(!/^act_[a-f0-9]{32}$/.test(ref)|| (start===null)!==(duration===null) ||
      start!==null && (!/^\d+$/.test(start)||Number(start)>172800) ||
      duration!==null && (!/^\d+$/.test(duration)||Number(duration)<11||Number(duration)>43516))
      return NextResponse.json({error:"Проверете активността и началото и края на участъка."},{status:422});
    const query:Record<string,string>={activity_ref:ref};
    if(start!==null&&duration!==null)Object.assign(query,{start_s:start,duration_s:duration});
    return NextResponse.json(await getSpeedPreview(access.athleteAlias,query),{headers:{"Cache-Control":"private, no-store"}});
  } catch {
    return NextResponse.json({error:"Прегледът временно не е достъпен. Опитайте отново; изборът ви остава запазен във формата."},{status:503});
  }
}
