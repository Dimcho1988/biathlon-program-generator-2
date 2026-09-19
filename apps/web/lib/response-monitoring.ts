export const RESPONSE_VERSION = "response-monitoring-v1";
export const GROUPS = ["subjective", "rpe", "physiology"] as const;
export type Group = typeof GROUPS[number];
export const GROUP_LABELS: Record<Group,string> = {subjective:"Субективно състояние", rpe:"Отговор към предишната тренировка", physiology:"Пулс в покой и HRV"};
export const FIELD_LABELS: Record<string,string> = {sleep_quality:"Качество на съня",fatigue:"Умора",soreness:"Мускулна болезненост",stress:"Психически стрес",motivation:"Липса на мотивация"};
export const PHASES: Record<string,string> = {BUILD:"Натрупване",MAINTAIN:"Поддържане",RECOVERY:"Разтоварване",TAPER:"Тейпър",UNSPECIFIED:"Не е зададена"};
export const STATES: Record<string,string> = {INSUFFICIENT_DATA:"Натрупваме индивидуална база",WITHIN_USUAL:"В обичайния диапазон",EXPECTED_ELEVATION:"Покачване в натоварващ блок",ELEVATED:"Повишена субективна реакция",REVIEW:"Нужен е преглед с треньора",REVIEW_AFTER_RECOVERY:"Задържане след разтоварване"};
export interface Baseline {median:number;spread:number;count:number}
export interface Session {activity_ref:string;day:string;name:string;sport:string;rpe:number|null;duration_minutes:number|null;suggested_duration_minutes:number|null;timing:string;source:string|null;provider_rpe:number|null;revision:number;note:string;srpe_load:number|null;expected_rpe:number|null;deviation_score:number|null;comparable_count:number}
export interface DailyReport {day:string;observed_at:string|null;sleep_quality:number;fatigue:number;soreness:number;stress:number;motivation:number;pain_or_illness:boolean;note:string}
export interface ResponseDay {day:string;total:number|null;coverage:number;groups:Array<{key:Group;score:number|null;weight:number;contribution:number|null}>;state:string;phase:string;baseline:Baseline|null;deviation:number|null;baseline_anchor:string;daily_report:DailyReport|null;daily_revision:number;device_metrics:Record<string,{value:number;unit:string}>;physiology:Record<string,{raw:number|null;score:number|null;baseline:Baseline|null}>;rpe_sessions:Session[];block_key:string|null;automatic_action:"NONE";data_age_days:number}
export interface ResponseEntry {kind:string;entry_key:string;revision:number;payload:Record<string,unknown>;recorded_at:string;summary?:{peak_deviation:number|null;elevated_days:number;observed_days:number;tracked_days:number;returned_on:string|null;status:string}}
export interface ResponseHistory {schema_version:typeof RESPONSE_VERSION;today:string;timezone:string;period_start:string;period_end:string;mode:"OBSERVATION_ONLY";automatic_increase:false;changes_recovery:false;weights:Record<Group,number>;days:ResponseDay[];sessions:Session[];blocks:ResponseEntry[];tests:ResponseEntry[];revision:number|null}
const finite = (v:unknown): v is number => typeof v === "number" && Number.isFinite(v);
const nullable = (v:unknown) => v === null || finite(v);
export function parseResponseHistory(value:unknown):ResponseHistory {
  const r = value as ResponseHistory;
  if (!r || r.schema_version!==RESPONSE_VERSION || r.mode!=="OBSERVATION_ONLY" || r.automatic_increase!==false || r.changes_recovery!==false
    || !Array.isArray(r.days) || !Array.isArray(r.sessions) || !Array.isArray(r.blocks) || !Array.isArray(r.tests)
    || !r.weights || r.weights.subjective!==.5 || r.weights.rpe!==.3 || r.weights.physiology!==.2) throw new Error("Неподдържана версия на оценката.");
  for (const d of r.days) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d.day) || !nullable(d.total) || !finite(d.coverage) || d.automatic_action!=="NONE"
      || !Array.isArray(d.groups) || d.groups.length!==3 || new Set(d.groups.map(g=>g.key)).size!==3) throw new Error("Невалидна дневна оценка.");
    for (const g of d.groups) if (!GROUPS.includes(g.key) || g.weight!==r.weights[g.key] || !nullable(g.score) || !nullable(g.contribution)
      || (g.score===null ? g.contribution!==null : g.score<0 || g.score>100 || g.contribution===null || Math.abs(g.contribution-g.score*g.weight)>.002)) throw new Error("Невалиден принос на компонент.");
    const full = d.groups.every(g=>g.score!==null);
    if (full !== (d.total!==null) || Math.abs(d.coverage-d.groups.reduce((s,g)=>s+(g.score===null?0:g.weight*100),0))>.01
      || (d.total!==null && Math.abs(d.total-d.groups.reduce((s,g)=>s+g.contribution!,0))>.01)) throw new Error("Непълните данни не могат да бъдат обща оценка.");
  }
  return r;
}
