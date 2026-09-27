import type {BodyObservationsDay, LabReport} from "./body-observations";
import {ANALYTES} from "./body-observations";
import {isRecord} from "./training-status";
export const RESPONSE_VERSION = "response-monitoring-v2";
export const GROUPS = ["subjective", "functional", "physiology", "weight", "biochemistry"] as const;
export type Group = typeof GROUPS[number];
export const WEIGHTS:Record<Group,number> = {subjective:.35,functional:.25,physiology:.15,weight:.15,biochemistry:.10};
export const GROUP_LABELS:Record<Group,string> = {subjective:"Субективно състояние",functional:"Работоспособност",physiology:"Пулс и HRV",weight:"Динамика на теглото",biochemistry:"Биохимия"};
export const FIELD_LABELS:Record<string,string> = {sleep_quality:"Качество на съня",fatigue:"Умора",soreness:"Мускулна болезненост",stress:"Психически стрес",motivation:"Желание за тренировка",competition_motivation:"Желание за състезание"};
export const CHANNEL_LABELS:Record<string,string> = {...FIELD_LABELS,sleep_duration:"Продължителност на съня",performance:"Скорост–пулс / ТИ и изпълнение",rpe:"Усилие при сходна работа",volume:"Изпълнение на обема",control_test:"Контролни тестове",hrv:"HRV · RMSSD",resting_hr:"Пулс в покой",morning_weight:"Сутрешен индекс",session_weight:"Индекс след / преди",CK:"Креатинкиназа",UREA:"Урея",TC_RATIO:"Тестостерон / кортизол",HEMOGLOBIN:"Хемоглобин",FERRITIN:"Феритин",TSAT:"Трансферинова сатурация",CRP:"CRP"};
export const PHASES:Record<string,string> = {BUILD:"Натрупване",MAINTAIN:"Поддържане",RECOVERY:"Разтоварване",TAPER:"Тейпър",UNSPECIFIED:"Не е зададена"};
export const STATES:Record<string,string> = {PARTIAL:"Частична оценка",INSUFFICIENT_DATA:"Недостатъчно данни",WITHIN_USUAL:"В обичайния диапазон",EXPECTED_ELEVATION:"Покачване в натоварващ блок",ELEVATED:"Повишена реакция",REVIEW:"Нужен е преглед",REVIEW_AFTER_RECOVERY:"Задържане след разтоварване"};
export const REASONS:Record<string,string> = {UNKNOWN:"Не е уточнено",AS_PLANNED:"Според плана",FATIGUE:"Умора / невъзможност",TIME:"Липса на време",CONDITIONS:"Терен / условия",COACH:"Треньорско решение",OTHER:"Друга причина"};
export interface Baseline {median:number;spread:number;count:number}
export interface ExecutionMethod {id:string;title:string;zone:string;sports:string[]}
export interface Execution {planned_duration_minutes:number|null;planned_speed_kmh:number|null;executed_speed_kmh:number|null;execution_comparable:boolean|null;execution_reason:string|null;executed_method_id?:string|null;method_confirmed?:boolean|null;executed_method?:Record<string,unknown>|null}
export interface Session {activity_ref:string;day:string;name:string;sport:string;rpe:number|null;duration_minutes:number|null;suggested_duration_minutes:number|null;timing:string;source:string|null;provider_rpe:number|null;revision:number;note:string;srpe_load:number|null;expected_rpe:number|null;deviation_score:number|null;comparable_count:number;execution?:Execution}
export interface DailyReport {day:string;observed_at:string|null;sleep_quality:number|null;fatigue:number|null;soreness:number|null;stress:number|null;motivation:number|null;competition_motivation?:number|null;sleep_hours?:number|null;pain_or_illness:boolean;note:string}
export interface Channel {key:string;group:Group;weight:number;effective_weight:number;contribution:number|null;score:number|null;raw:number|null;unit:string|null;source:string|null;observed_on:string|null;baseline:Baseline|null;status:string}
export interface GroupScore {key:Group;score:number|null;weight:number;available_weight:number;effective_weight:number;contribution:number|null}
export interface ResponseDay {body_observations?:BodyObservationsDay;day:string;total:number|null;coverage:number;groups:GroupScore[];channels:Channel[];assessment_quality:"NO_DATA"|"PARTIAL"|"SUFFICIENT";trend_3d:number|null;mix_changed:boolean;comparison_previous:{coverage:number;delta:number|null}|null;state:string;phase:string;baseline:Baseline|null;deviation:number|null;baseline_anchor:string;daily_report:DailyReport|null;daily_revision:number;device_metrics:Record<string,{value:number;unit:string}>;physiology:Record<string,{raw:number|null;score:number|null;baseline:Baseline|null}>;rpe_sessions:Session[];block_key:string|null;automatic_action:"NONE";data_age_days:number}
export interface ResponseEntry {kind:string;entry_key:string;revision:number;payload:Record<string,unknown>;recorded_at:string;summary?:{peak_deviation:number|null;elevated_days:number;observed_days:number;tracked_days:number;returned_on:string|null;status:string}}
export interface ResponseHistory {execution_methods?:ExecutionMethod[];trainability_unavailable?:boolean;body_observations_version?:"body-observations-v2";lab_reports?:LabReport[];settings?:{indicator_weights:Record<string,number>;validated:false};symptom_context?:{latest_report_day:string|null;report_age_days:number|null;hold_for_reported_illness_or_pain:boolean};schema_version:typeof RESPONSE_VERSION;today:string;timezone:string;period_start:string;period_end:string;mode:"OBSERVATION_ONLY";automatic_increase:false;changes_recovery:false;weights:Record<Group,number>;days:ResponseDay[];sessions:Session[];blocks:ResponseEntry[];tests:ResponseEntry[];revision:number|null}
const finite=(v:unknown):v is number=>typeof v==="number"&&Number.isFinite(v);
const nullable=(v:unknown)=>v===null||finite(v);
export function parseResponseHistory(value:unknown):ResponseHistory {
  const r=value as ResponseHistory;
  if(!r||r.schema_version!==RESPONSE_VERSION||r.mode!=="OBSERVATION_ONLY"||r.automatic_increase!==false||r.changes_recovery!==false||!Array.isArray(r.days)||!Array.isArray(r.sessions)||!Array.isArray(r.blocks)||!Array.isArray(r.tests)||!r.weights||GROUPS.some(g=>r.weights[g]!==WEIGHTS[g]))throw new Error("Неподдържана версия на оценката.");
  if(r.execution_methods !== undefined && (!Array.isArray(r.execution_methods) || r.execution_methods.some(method =>
    !isRecord(method) || typeof method.id !== "string" || typeof method.title !== "string" || typeof method.zone !== "string"
    || !Array.isArray(method.sports) || !method.sports.every(sport => typeof sport === "string")))) throw new Error("Невалиден списък на тренировъчните методи.");
  for(const d of r.days){
    if(!/^\d{4}-\d{2}-\d{2}$/.test(d.day)||!nullable(d.total)||!finite(d.coverage)||d.coverage<0||d.coverage>100||d.automatic_action!=="NONE"||!Array.isArray(d.groups)||d.groups.length!==5||new Set(d.groups.map(g=>g.key)).size!==5||!Array.isArray(d.channels)||d.channels.length!==22||new Set(d.channels.map(c=>c.key)).size!==22)throw new Error("Невалидна дневна оценка.");
    let available=0,sum=0;
    for(const c of d.channels){
      if(!GROUPS.includes(c.group)||!Object.hasOwn(CHANNEL_LABELS,c.key)||!finite(c.weight)||c.weight<=0||!nullable(c.score)||!nullable(c.raw)||!nullable(c.contribution)||!finite(c.effective_weight)||(c.score!==null&&(c.score<0||c.score>100)))throw new Error("Невалиден показател.");
      if(c.score!==null){available+=c.weight;sum+=c.weight*c.score;}
    }
    for(const g of d.groups){
      const rows=d.channels.filter(c=>c.group===g.key),observed=rows.filter(c=>c.score!==null),aw=observed.reduce((s,c)=>s+c.weight,0),subtotal=observed.reduce((s,c)=>s+c.weight*c.score!,0);
      if(!GROUPS.includes(g.key)||g.weight!==WEIGHTS[g.key]||!nullable(g.score)||!nullable(g.contribution)||!finite(g.available_weight)||!finite(g.effective_weight)||Math.abs(rows.reduce((s,c)=>s+c.weight,0)-g.weight)>.00001||Math.abs(g.available_weight-aw)>.00001||Math.abs(g.effective_weight-(available?aw/available:0))>.00001||(aw===0?(g.score!==null||g.contribution!==null):(g.score===null||g.contribution===null||Math.abs(g.score-subtotal/aw)>.002||Math.abs(g.contribution-subtotal/available)>.002)))throw new Error("Невалиден принос на компонент.");
    }
    for(const c of d.channels){const ew=available&&c.score!==null?c.weight/available:0;if(Math.abs(c.effective_weight-ew)>.00001||(c.score===null?c.contribution!==null:c.contribution===null||Math.abs(c.contribution-c.score*ew)>.002))throw new Error("Невалидно претегляне.");}
    if(Math.abs(d.coverage-available*100)>.01||(available===0?d.total!==null:d.total===null||Math.abs(d.total-sum/available)>.002)||!nullable(d.trend_3d))throw new Error("Невалидна обща оценка.");
    const quality=!available?"NO_DATA":available<.4-1e-9||d.groups.filter(g=>g.score!==null).length<2?"PARTIAL":"SUFFICIENT";
    if(d.assessment_quality!==quality)throw new Error("Невалидно покритие.");
  }
  if (r.body_observations_version !== undefined) {
    if(r.body_observations_version!=="body-observations-v2" || !Array.isArray(r.lab_reports))throw new Error("Неподдържана версия на телесните наблюдения.");
    for(const d of r.days){
      const b=d.body_observations,w=b?.weight;
      if(!b || !w || w.automatic_weight!==0 || w.validated!==false || !Array.isArray(w.sessions) || !Array.isArray(b.lab_sample_keys)
        || !Number.isInteger(w.morning_count) || w.morning_count<0 || w.morning_count>7 || w.minimum_morning_days!==4 || w.window_days!==7
        || ![w.morning_mean_kg,w.morning_ratio,w.morning_change_percent,w.session_ratio].every(nullable)
        || (w.morning_ratio!==null && (w.morning_count<4 || w.morning_ratio<=0)))throw new Error("Невалидни наблюдения за теглото.");
    }
    for(const lab of r.lab_reports){
      if(lab.automatic_weight!==0 || !lab.payload || !Array.isArray(lab.results) || !nullable(lab.testosterone_cortisol_ratio)
        || new Set(lab.results.map(v=>v.analyte)).size!==lab.results.length)throw new Error("Невалидно лабораторно изследване.");
      for(const v of lab.results)if(!Object.hasOwn(ANALYTES,v.analyte) || !finite(v.value) || !finite(v.normalized_value)
        || !nullable(v.reference_low) || !nullable(v.reference_high) || !nullable(v.change_percent)
        || !nullable(v.baseline_median))throw new Error("Невалиден лабораторен резултат.");
    }
  }
  return r;
}
