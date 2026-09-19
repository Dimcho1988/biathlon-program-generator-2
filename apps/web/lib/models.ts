import type {WellnessCoverageDiagnostics} from "./recovery-history";
export const MODEL_ZONES = ["Z1","Z2","Z3","Z4","Z5","STR"] as const;
export type ModelZone = typeof MODEL_ZONES[number];
export interface ZoneConfig {duration_coefficient:number;shape:number;sensitivity:number;initial_daily_min:number}
export interface RecoveryConfig {expected_revision:number;zones:Record<ModelZone,ZoneConfig>}
interface Baseline {baseline_daily_min:number;baseline_raw_daily_min:number|null;history_days:number;baseline_source:"NO_HISTORY"|"NO_ZONE_LOAD"|"SHORT_HISTORY"|"SPARSE_ZONE_HISTORY"|"PERSONAL"}
export interface RecoveryV2 {
  schema_version:"recovery-history-v2";athlete_id:string;period_start:string;period_end:string;as_of:string;
  basis:"load-only";time_resolution:"calendar-day";ready_threshold_percent:90;
  model:{algorithm_version:"recovery-daily-e-biexponential-v2"|"recovery-daily-e-biexponential-v2.1"|"recovery-daily-e-biexponential-v2.2";parameter_version:string;parameter_fingerprint:string;practical_full_recovery_percent:90};
  config_revision:number;settings:Record<ModelZone,ZoneConfig>;
  current:Array<Baseline & {zone:ModelZone;readiness_percent:number;residual_fatigue:number;days_to_practical_recovery:number}>;
  daily:Array<Baseline & {date:string;zone:ModelZone;readiness_before_percent:number;readiness_after_percent:number;residual_fatigue_after:number;impulse:number;effective_load:number;isolated_days_to_90:number;residual_fatigue_now?:number|null}>;
  forecast:Array<{zone:ModelZone;days:number;readiness_percent:number}>;
  source_as_of:string;source_stale:boolean;warnings:string[];
  wellness_diagnostics?:WellnessCoverageDiagnostics|null;
}
const finite=(v:unknown):v is number=>typeof v==="number"&&Number.isFinite(v);
export function parseRecoveryV2(value:unknown):RecoveryV2 {
  const r=value as RecoveryV2;
  if(!r || r.schema_version!=="recovery-history-v2" || r.ready_threshold_percent!==90 || r.basis!=="load-only"
    || r.time_resolution!=="calendar-day" || !r.model || !["recovery-daily-e-biexponential-v2","recovery-daily-e-biexponential-v2.1","recovery-daily-e-biexponential-v2.2"].includes(r.model.algorithm_version)
    || r.model.practical_full_recovery_percent!==90 || !Number.isInteger(r.config_revision)
    || !Array.isArray(r.current)||r.current.length!==6||!Array.isArray(r.daily)||!Array.isArray(r.forecast)||!r.settings) throw new Error("Невалиден Recovery v2 модел.");
  for(const [i,c] of r.current.entries()) {
    if(c.zone!==MODEL_ZONES[i]||!finite(c.readiness_percent)||c.readiness_percent<0||c.readiness_percent>100
      ||!finite(c.residual_fatigue)||c.residual_fatigue<0||!finite(c.days_to_practical_recovery)||c.days_to_practical_recovery<0
      ||Math.abs(c.readiness_percent-Math.max(0,100-c.residual_fatigue))>.001
      ||!finite(c.baseline_daily_min)||c.baseline_daily_min<=0) throw new Error("Несъгласувана готовност.");
    const s=r.settings[c.zone];
    if(!s||!Object.values(s).every(v=>finite(v)&&v>0)||s.shape<1||s.shape>10)throw new Error("Невалидни коефициенти.");
    if(r.model.algorithm_version==="recovery-daily-e-biexponential-v2.2"
      && ((c.baseline_raw_daily_min!==null&&(!finite(c.baseline_raw_daily_min)||c.baseline_raw_daily_min<0))
        ||Math.abs(c.baseline_daily_min-s.initial_daily_min-(c.baseline_raw_daily_min??0))>.001))
      throw new Error("Несъгласувана базова добавка.");
  }
  for(const p of r.forecast) if(!MODEL_ZONES.includes(p.zone)||!finite(p.days)||p.days<0||!finite(p.readiness_percent)||p.readiness_percent<0||p.readiness_percent>100)throw new Error("Невалидна възстановителна крива.");
  for(const d of r.daily) if(!MODEL_ZONES.includes(d.zone)||!/^\d{4}-\d{2}-\d{2}$/.test(d.date)||!finite(d.readiness_after_percent)||d.readiness_after_percent<0||d.readiness_after_percent>100)throw new Error("Невалидна история на възстановяването.");
  return r;
}
export interface Prediction {duration_s:number;speed_kmh:number;distance_m:number;estimated_hr_bpm:number|null;hr_prediction_source?:string|null;hr_prediction_reason?:string|null;zone?:string|null}
export interface SpeedTest {kind:"SPEED_TEST";entry_key:string;revision:number;recorded_at:string;payload:{activity_ref?:string;source?:"MANUAL";test_id?:string;name?:string;flat_terrain?:true;measurement_input?:"DISTANCE"|"SPEED";distance_m?:number;start_s:number;duration_s:number;speed_kmh:number;day:string;sport:string;enabled:boolean;use_for_cs:boolean;maximal:boolean;test_mode?:"STRICT"|"EXPLORATORY";exploratory_confirmed?:boolean;measured_duration_s?:number;comparable:true;conditions:string;coverage_percent:number|null}}
export interface HrModel {curve_duration_range_s?:number[];curve_speed_range_kmh?:number[];conflicting_zones?:string[][];model_version:string;hr_range_bpm:number[];speed_range_kmh:number[];zones:Array<{zone:string;hr_bpm:number;duration_s:number;duration_min_s:number;duration_max_s:number;speed_kmh:number;source:string;reason:string|null;index:number|null;count:number;candidate_speed_kmh?:number|null;candidate_duration_s?:number|null;candidate_reason?:string}>}
export interface SpeedModel {test_window?:{start:string;end:string};hr_model?:HrModel|null;index_admission?:{activities:number;excluded:number;refresh_required:number};volume_position_basis?:string;schema_version:"speed-model-v1";model_version:string;sport:string;sports:string[];activities:Array<{activity_ref:string;name:string;day:string;sport:string;elapsed_s?:number|null}>;status:"CALIBRATED"|"REFERENCE_ONLY";tests:SpeedTest[];active_test_count:number;exploratory_test_count?:number;active_test_keys:string[];points:Prediction[];volume_scope?:"ALL_SPORTS";volume_weekly_min:Record<string,number|null>;history_days:number;zone_corrections:Record<string,number>;correction_applied_fraction:number;critical_speed:{status:string;count:number;speed_kmh?:number;d_prime_m?:number;distance_rmse_m?:number};prediction:Prediction|null;prediction_error?:string|null;warnings:string[];source_generation_id:string|null;source_revision:number|null;hr_speed_range_kmh:number[]|null}
export function parseSpeedModel(value:unknown):SpeedModel {
  const r=value as SpeedModel;
  if(!r||r.schema_version!=="speed-model-v1"||!Array.isArray(r.points)||!Array.isArray(r.tests))throw new Error("Невалиден скоростен модел.");
  for(const p of r.points)if(!finite(p.duration_s)||p.duration_s<=0||!finite(p.speed_kmh)||p.speed_kmh<=0||!finite(p.distance_m)||p.distance_m<=0)throw new Error("Невалидна скоростна крива.");
  return r;
}

export async function saveModel(kind:"recovery"|"speed-test"|"speed-test-manual",payload:unknown){
  const result=await fetch("/api/athlete/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind,payload})});
  const body=await result.json();
  if(!result.ok)throw new Error(body.error||"Записването не завърши.");
  return body as {saved:boolean;revision:number;entry_key?:string};
}
