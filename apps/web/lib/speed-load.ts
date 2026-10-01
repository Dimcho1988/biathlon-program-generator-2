import type {SportHrPolicy} from "./models";
import {isCalendarDate} from "./training-status";

export interface SpeedLoad {
  schema_version:"speed-load-history-v1"; model_version:"independent-speed-load-causal-ti-v1";
  status:"AVAILABLE"|"PARTIAL"|"UNAVAILABLE"; sport:string|null; sports:string[];
  source_generation_id:string|null; source_revision:number|null; start_date:string; end_date:string;
  load_role:"PARALLEL_ESTIMATE_NOT_ADDED_TO_HR"; mapping_policy:string;
  recorded_minutes:number; classified_minutes:number; coverage_percent:number;
  zones:Array<{zone:string; minutes:number; equivalent_minutes:number; effective_load:number; e7_daily:number; e40_daily:number; ratio_7_40:number|null}>;
  daily:Array<{date:string; zone:string; equivalent_minutes:number; effective_load:number; ratio_7_40:number|null}>;
  sport_indices:Array<{sport:string; reference_hr_bpm:number; comparison_speed_kmh:number|null; hr_policy:SportHrPolicy;
    reason:string|null; indices:Record<string,{index:number|null; count:number; seconds:number}>;
    mapping:{uses_general_index:boolean;mapping_basis?:string}|null}>;
  activities:Array<{activity_ref:string; date:string; sport:string; recorded_minutes:number; classified_minutes:number; reason:string|null}>;
  warnings:string[];
}
const nonnegative=(v:unknown):v is number=>typeof v==="number"&&Number.isFinite(v)&&v>=0;
export function parseSpeedLoad(value:unknown):SpeedLoad {
  const r=value as SpeedLoad;
  const bad=()=>new Error("Невалиден отчет по скорост.");
  if(!r||r.schema_version!=="speed-load-history-v1"||r.model_version!=="independent-speed-load-causal-ti-v1"
    ||r.load_role!=="PARALLEL_ESTIMATE_NOT_ADDED_TO_HR"||!["AVAILABLE","PARTIAL","UNAVAILABLE"].includes(r.status)
    ||!isCalendarDate(r.start_date)||!isCalendarDate(r.end_date)||!Array.isArray(r.zones)||r.zones.length!==5
    ||!Array.isArray(r.daily)||!Array.isArray(r.sport_indices)||!Array.isArray(r.activities)||!Array.isArray(r.sports)
    ||![r.recorded_minutes,r.classified_minutes,r.coverage_percent].every(nonnegative)
    ||r.coverage_percent>100.001||r.classified_minutes>r.recorded_minutes+.001)throw bad();
  for(const [i,z] of r.zones.entries())if(z.zone!==`Z${i+1}`
    ||![z.minutes,z.equivalent_minutes,z.effective_load,z.e7_daily,z.e40_daily].every(nonnegative)
    ||z.ratio_7_40!==null&&!nonnegative(z.ratio_7_40))throw bad();
  for(const d of r.daily)if(!isCalendarDate(d.date)||!/^Z[1-5]$/.test(d.zone)
    ||![d.equivalent_minutes,d.effective_load].every(nonnegative)||d.ratio_7_40!==null&&!nonnegative(d.ratio_7_40))throw bad();
  for(const s of r.sport_indices)if(typeof s.sport!=="string"||!nonnegative(s.reference_hr_bpm)
    ||s.comparison_speed_kmh!==null&&!nonnegative(s.comparison_speed_kmh)||!s.hr_policy||!nonnegative(s.hr_policy.offset_bpm)
    ||s.hr_policy.raw_hr_unchanged!==true||!s.indices||Object.values(s.indices).some(b=>!b||!nonnegative(b.count)||!nonnegative(b.seconds)||b.index!==null&&!nonnegative(b.index)))throw bad();
  return r;
}
