import type {SpeedModel} from "./models";

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
export function testActivities(activities:SpeedModel["activities"], sport:string, query="") {
  const search = query.trim().toLocaleLowerCase("bg");
  return activities.filter(a => a.sport === sport && `${a.name} ${a.day}`.toLocaleLowerCase("bg").includes(search))
    .sort((a,b) => b.day.localeCompare(a.day) || a.name.localeCompare(b.name));
}
export interface SpeedPreview {
  schema_version:"speed-test-preview-v1"; activity_ref:string; sport:string; name:string; day:string;
  elapsed_s:number; status:"READY"|"ANALYSIS_REQUIRED"|"OUTSIDE_TEST_WINDOW"; source_run_key:string|null;
  uses_legacy_samples?:boolean;
  series:Array<{elapsed_s:number;speed_kmh:number|null;eligible_fraction:number}>;
  selection:null|{start_s:number;duration_s:number;eligible:boolean;status:string;coverage_percent:number;
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
    "Outside the calibrated duration range":"Продължителността е извън обхвата на модела.",
    "Outside the activity range":"Краят на участъка е извън записа. Проверете началото и края.",
  };
  return typeof detail === "string" && messages[detail] || "Проверете участъка и потвържденията. Записването не завърши.";
}
