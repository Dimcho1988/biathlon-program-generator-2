import {isCalendarDate} from "../../../../../lib/training-status";
import {NextResponse} from "next/server";
import {currentAuthorizedAthlete} from "../../../../../lib/account-access";
import {getSpeedLoad} from "../../../../../lib/api";
import {createHash} from "node:crypto";
import {speedLoadCacheScope} from "../../../../../lib/speed-load-scope";

export async function GET(request:Request) {
  const started = performance.now();
  try {
    const access=await currentAuthorizedAthlete();
    const authorizedAt = performance.now();
    if(!access)return NextResponse.json({error:"Влезте в профила си."},{status:401});
    if(!access.canViewRecovery)return NextResponse.json({error:"Нямате достъп до тези данни."},{status:403});
    const params = new URL(request.url).searchParams;
    const scope = speedLoadCacheScope(access);
    if(params.has("cache_scope") && params.get("cache_scope") !== scope)
      return NextResponse.json({error:"Избраният профил е променен."},{status:409,headers:{"Cache-Control":"private, no-store"}});
    const sport=params.get("sport")||undefined;
    const start=params.get("period_start")||undefined, end=params.get("period_end")||undefined;
    if((start || end) && (!isCalendarDate(start) || !isCalendarDate(end) || start! > end! || Date.parse(end!)-Date.parse(start!) >= 366*86400000))
      return NextResponse.json({error:"Изберете валиден период от 1 до 366 дни."},{status:422});
    if(sport&&!/^[A-Za-z]{2,40}$/.test(sport))return NextResponse.json({error:"Невалидно средство."},{status:422});
    // Revalidate the actual derived result, including settings and repaired
    // analyses that can change under the same generation. Authorization and
    // fresh backend input reads happen before comparing this private ETag.
    const apiStarted = performance.now();
    const result = await getSpeedLoad(access.athleteAlias,sport,start,end);
    const apiFinished = performance.now();
    const body = JSON.stringify(result);
    const etag = `"${createHash("sha256").update(scope).update(body).digest("hex")}"`;
    const serializedAt = performance.now();
    const unchanged = request.headers.get("If-None-Match") === etag;
    const timing = { auth: authorizedAt-started, api: apiFinished-apiStarted, serialize: serializedAt-apiFinished, total: serializedAt-started };
    // Timing/size only: no aliases, scope hashes, query strings or payloads.
    console.info(`onflows_speed_route auth_ms=${timing.auth.toFixed(1)} api_ms=${timing.api.toFixed(1)} serialize_ms=${timing.serialize.toFixed(1)} total_ms=${timing.total.toFixed(1)} response_bytes=${unchanged?0:Buffer.byteLength(body)} cache_status=${unchanged?304:200}`);
    const headers = {"Cache-Control":"private, no-store", "ETag":etag, "Content-Type":"application/json",
      "Server-Timing":`authorization;dur=${timing.auth.toFixed(1)}, api;dur=${timing.api.toFixed(1)}, serialize;dur=${timing.serialize.toFixed(1)}`};
    if(unchanged) return new NextResponse(null,{status:304,headers});
    return new NextResponse(body,{headers});
  } catch {
    return NextResponse.json({error:"Отчетът по скорост временно не е достъпен."},{status:503});
  }
}
