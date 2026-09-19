import type {SpeedModel, SpeedTest} from "./models";
export type SpeedTestMode = "STRICT"|"EXPLORATORY";
export const testModeLabel = (mode:SpeedTestMode) => mode==="EXPLORATORY"?"Пробен · комплексна тренировка · праг 70%":"Стандартен · максимален тест · праг 98%";

export const speedNumber = (value:number) => new Intl.NumberFormat("bg-BG", {maximumFractionDigits:2}).format(value);
export function clockTime(seconds:number) {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2,"0")}`;
}
export function parseClock(value:string):number|null {
  const parts = value.trim().split(":");
  if (parts.length < 2 || parts.length > 3 || parts.some(p => !/^\d+$/.test(p))) return null;
  const values = parts.map(Number);
  if (values.at(-1)! >= 60 || parts.length === 3 && values[1] >= 60) return null;
  const seconds = values.reduce((sum,v) => sum * 60 + v, 0);
  return Number.isSafeInteger(seconds) && seconds <= 216316 ? seconds : null;
}
export function parseManualClock(value:string):number|null {
  const parts=value.trim().replace(",",".").split(":");
  if(parts.length<2||parts.length>3||parts.slice(0,-1).some(p=>!/^\d+$/.test(p))||!/^\d+(\.\d{1,3})?$/.test(parts.at(-1)!))return null;
  const values=parts.map(Number);
  if(values.at(-1)!>=60||parts.length===3&&values[1]>=60)return null;
  const seconds=values.reduce((sum,v)=>sum*60+v,0);
  return Number.isFinite(seconds)&&seconds>=10.8&&seconds<=43516?seconds:null;
}
export function manualClockTime(seconds:number):string {
  const totalMs=Math.round(seconds*1000),minutes=Math.floor(totalMs/60000);
  const remainder=String((totalMs%60000)/1000).split(".");
  return `${minutes}:${remainder[0].padStart(2,"0")}${remainder[1]?","+remainder[1]:""}`;
}
export function manualTestPayload(test:SpeedTest,enabled:boolean) {
  const p=test.payload;
  return {test_id:p.test_id,name:p.name,day:p.day,sport:p.sport,duration_s:p.duration_s,
    ...(p.measurement_input==="SPEED"?{speed_kmh:p.speed_kmh}:{distance_m:p.distance_m}),
    maximal:true,comparable:true,flat_terrain:true,conditions:p.conditions,use_for_cs:p.use_for_cs,
    enabled,expected_revision:test.revision};
}
export function testActivities(activities:SpeedModel["activities"], sport:string, query="") {
  const search = query.trim().toLocaleLowerCase("bg");
  return activities.filter(a => a.sport === sport && `${a.name} ${a.day}`.toLocaleLowerCase("bg").includes(search))
    .sort((a,b) => b.day.localeCompare(a.day) || a.name.localeCompare(b.name));
}
export interface SpeedPreview {
  schema_version:"speed-test-preview-v1"; activity_ref:string; sport:string; name:string; day:string;
  elapsed_s:number; status:"READY"|"ANALYSIS_REQUIRED"|"OUTSIDE_TEST_WINDOW"; source_run_key:string|null;
  uses_legacy_samples?:boolean;
  test_mode?:SpeedTestMode; minimum_coverage_percent?:number;
  series:Array<{elapsed_s:number;speed_kmh:number|null;eligible_fraction:number}>;
  selection:null|{start_s:number;duration_s:number;eligible:boolean;status:string;coverage_percent:number;
    test_mode?:SpeedTestMode;minimum_coverage_percent?:number;measured_duration_s?:number;measured_distance_m?:number;
    speed_kmh:number|null;distance_m:number|null;
    excluded_seconds:{downhill:number;invalid:number;missing_or_paused:number}};
}
export function parseSpeedPreview(value:unknown):SpeedPreview {
  const r = value as SpeedPreview;
  if (!r || r.schema_version !== "speed-test-preview-v1" || !/^act_[a-f0-9]{32}$/.test(r.activity_ref) ||
      !Number.isFinite(r.elapsed_s) || r.elapsed_s < 0 || !Array.isArray(r.series) || r.series.length > 360)
    throw new Error("Прегледът на активността е невалиден.");
  for (const p of r.series) if (!Number.isFinite(p.elapsed_s) || p.speed_kmh !== null && !Number.isFinite(p.speed_kmh))
    throw new Error("Графиката на активността е невалидна.");
  return r;
}
export function speedTestError(detail:unknown):string {
  const messages:Record<string,string> = {
    "Model input changed; reload before editing":"Тестът е променен от друга сесия. Обновете страницата преди запис.",
    "Activity analysis changed; preview the segment again":"Анализът на активността е обновен. Проверете участъка отново преди запис.",
    "Full Vflat samples are required":"Липсват подробни данни за скоростта. Обновете анализите на активностите от началния екран.",
    "Activity requires a shadow refresh":"Активността още няма анализ на скоростта. Обновете анализите от началния екран.",
    "Choose a test within the last 90 days":"Изберете тест от последните 90 дни.",
    "Activity is unavailable for this athlete":"Активността не е достъпна за избрания спортист.",
    "Saved test is unavailable":"Записаният тест вече не е достъпен. Обновете страницата.",
    "Test durations must be different":"Вече има тест със същата продължителност. Изключете стария, ако искате да го замените.",
    "Tests conflict: longer efforts require lower speed and greater distance":"Тестът противоречи на друг избран тест: по-дългото максимално усилие трябва да има по-ниска скорост и по-голяма дистанция. Проверете участъците и условията.",
    "A continuous segment with at least 98% eligible Vflat coverage is required; refresh older activity analyses first":"Участъкът няма необходимите 98% подходящи данни. Проверете покритието, паузите и спусканията в прегледа.",
    "Exploratory calibration requires at least 70% eligible Vflat coverage":"За пробна калибрация са нужни поне 70% подходящи данни. Изберете участък с по-добро покритие.",
    "Outside the calibrated duration range":"Продължителността е извън обхвата на модела.",
    "Outside the activity range":"Краят на участъка е извън записа. Проверете началото и края.",
    "Resolve incompatible Vflat test versions first":"Има включени тестове от различни версии на Vflat. Първо изключете или преизчислете старите тестове.",
  };
  return typeof detail === "string" && messages[detail] || "Проверете времето, дистанцията или скоростта и потвържденията. Записването не завърши.";
}
