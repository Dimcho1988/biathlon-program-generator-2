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
export type CurveEvidence = "MEASURED"|"INTERPOLATED"|"EXTRAPOLATED"|"ESTIMATED";
export interface CurveMetadata {mode:string;absolute_speed_available:boolean;measured_window_s?:[number,number]|null;cap_percent?:number|null;limited_tails?:("SHORT"|"LONG")[];reasons?:string[]}
export interface PreliminaryCapacity {status:string;anchors:Array<{zone:string;duration_s:number;duration_min_s:number;duration_max_s:number;duration_position:number;duration_source:string;measured_weekly_q:number|null;hr_bpm:number|null;speed_kmh:number|null}>;warnings:string[]}
export interface Prediction {duration_s:number;speed_kmh:number;distance_m:number;estimated_hr_bpm:number|null;evidence?:CurveEvidence;capped?:boolean;hr_prediction_source?:string|null;hr_prediction_reason?:string|null;zone?:string|null}
export interface DosingModel {
  model_version:string;status:"AVAILABLE"|"UNAVAILABLE";reason?:string|null;
  weights:{index:number;tests:number};weight_basis:string;is_maximal_test:false;use:"METHOD_DOSING";
  points:Array<{duration_s:number;speed_kmh:number;distance_m:number;index_speed_kmh:number;test_speed_kmh:number;estimated_hr_bpm:number|null}>;
  hr_model:HrModel|null;speed_zones:SpeedZoneProfile|null;
  zone_comparison?:Array<{zone:string;hr_bpm:number;duration_s:number;index_speed_kmh:number;test_speed_kmh:number;speed_kmh:number}>;
}
export interface StandardizedCS {status:string;speed_kmh?:number;d_prime_m?:number;difference_percent?:number|null;uses_extrapolation?:boolean;points:Array<{duration_s:number;speed_kmh:number;evidence:string}>}
export interface SpeedZoneProfile {status:string;zones:Array<{zone:string;low_kmh:number;high_kmh:number|null;source:string;upper_percent_cs?:number|null}>;note?:string}
export interface SpeedTest {kind:"SPEED_TEST";entry_key:string;revision:number;recorded_at:string;payload:{activity_ref?:string;source?:"MANUAL";test_id?:string;name?:string;flat_terrain?:true;measurement_input?:"DISTANCE"|"SPEED";distance_m?:number;start_s:number;duration_s:number;speed_kmh:number;day:string;sport:string;enabled:boolean;use_for_cs:boolean;maximal:boolean;test_mode?:"STRICT"|"EXPLORATORY";exploratory_confirmed?:boolean;measured_duration_s?:number;comparable:true;conditions:string;coverage_percent:number|null}}
export interface HrModel {curve_duration_range_s?:number[];curve_speed_range_kmh?:number[];conflicting_zones?:string[][];model_version:string;hr_range_bpm:number[];speed_range_kmh:number[];zones:Array<{zone:string;hr_bpm:number;duration_s:number;duration_min_s:number;duration_max_s:number;speed_kmh:number;source:string;reason:string|null;index:number|null;count:number;candidate_speed_kmh?:number|null;candidate_duration_s?:number|null;candidate_reason?:string}>}
export interface FunctionalProfilePoint {
  duration_s:number;speed_kmh:number;reference_speed_kmh:number;difference_percent:number;shape_difference_percent:number;
  evidence:"MEASURED_TEST"|"TEST_SUPPORTED_ESTIMATE"|"EXTRAPOLATED"|"ESTIMATED";extrapolation_capped:boolean;used_for_shape:boolean;
}
export interface FunctionalProfile {
  schema_version:"athlete-functional-profile-v1";status:"UNAVAILABLE"|"ESTIMATED"|"SINGLE_TEST"|"TEST_SUPPORTED";
  overall_level:{ratio_to_reference:number;difference_percent:number;basis:"REAL_TESTS"|"ESTIMATED_FIXED_DURATIONS";duration_range_s:[number,number]}|null;
  shape:{status:"INSUFFICIENT_REAL_TESTS"|"TEST_SUPPORTED_WINDOW";orientation:"LONGER_DURATION_ADVANTAGE"|"SHORTER_DURATION_ADVANTAGE"|"NO_RELATIVE_DIFFERENCE"|null;endurance_contrast_percent:number|null;test_duration_range_s:[number,number]|null;accepted_test_count:number;uncertainty:"NOT_QUANTIFIED"}|null;
  points:FunctionalProfilePoint[];test_points:FunctionalProfilePoint[];
  training_context:{source:string;unit?:"EQUIVALENT_MINUTES";zones:Array<{zone:string;weekly_minutes:number|null;share_percent:number|null}>;interpretation:"ASSOCIATION_ONLY";hypotheses:string[]};warnings:string[];
}
export interface SpeedModel {dosing_model?:DosingModel;speed_zones?:SpeedZoneProfile;hr_zone_source?:"MANUAL"|"AUTOMATIC_HRMAX";test_window?:{start:string;end:string};index_window?:{start:string;end:string;days:number;last_activity_date:string|null};hr_model?:HrModel|null;index_admission?:{activities:number;excluded:number;refresh_required:number;used?:number;incompatible?:number};volume_position_basis?:string;schema_version:"speed-model-v1";model_version:string;sport:string;sports:string[];activities:Array<{activity_ref:string;name:string;day:string;sport:string;elapsed_s?:number|null}>;status:"CALIBRATED"|"PRELIMINARY"|"UNAVAILABLE"|"REFERENCE_ONLY"|"CONFLICTING_TESTS";curve_metadata?:CurveMetadata;functional_profile?:FunctionalProfile;preliminary_capacity?:PreliminaryCapacity;volume_history_basis?:"HR_MEASURED"|"HR_PARTIAL"|"TOTAL_DURATION"|"UNAVAILABLE";total_weekly_minutes?:number|null;calibration_diagnostics?:{residuals?:Array<{duration_s:number;measured_speed_kmh:number;prior_speed_kmh:number;measured_vs_prior_percent:number}>;real_test_anchors_take_precedence?:boolean;prior_shape_used?:boolean;blend_status?:string;blend_reason?:string;model_error?:string};tests:SpeedTest[];active_test_count:number;exploratory_test_count?:number;active_test_keys:string[];points:Prediction[];volume_scope?:"ALL_SPORTS";volume_weekly_min:Record<string,number|null>;history_days:number;zone_corrections:Record<string,number>;correction_applied_fraction:number;critical_speed:{standardized?:StandardizedCS;status:string;count:number;speed_kmh?:number;d_prime_m?:number;distance_rmse_m?:number};prediction:Prediction|null;prediction_error?:string|null;warnings:string[];source_generation_id:string|null;source_revision:number|null;hr_speed_range_kmh:number[]|null}
export interface SportHrPolicy {
  version:string; offset_bpm:number; basis:string;
  reference_zone_bounds_bpm:number[]; sport_zone_bounds_bpm:number[];
  reference_hrmax_bpm:number|null; sport_hrmax_bpm:number|null; raw_hr_unchanged:true;
}
export interface SpeedModel {sport_hr_policy?:SportHrPolicy}
export function parseSpeedModel(value:unknown):SpeedModel {
  const r=value as SpeedModel;
  if(!r||r.schema_version!=="speed-model-v1"||!Array.isArray(r.points)||!Array.isArray(r.tests))throw new Error("Невалиден скоростен модел.");
  for(const p of r.points)if(!finite(p.duration_s)||p.duration_s<=0||!finite(p.speed_kmh)||p.speed_kmh<=0||!finite(p.distance_m)||p.distance_m<=0)throw new Error("Невалидна скоростна крива.");
  const metadata=r.curve_metadata;
  if(metadata&&(typeof metadata.absolute_speed_available!=="boolean"||typeof metadata.mode!=="string"
    ||metadata.cap_percent!=null&&(!finite(metadata.cap_percent)||metadata.cap_percent<0||metadata.cap_percent>5)
    ||metadata.measured_window_s!=null&&(!Array.isArray(metadata.measured_window_s)||metadata.measured_window_s.length!==2||!metadata.measured_window_s.every(v=>finite(v)&&v>0)||metadata.measured_window_s[1]<metadata.measured_window_s[0])))throw new Error("Невалидна основа на скоростния модел.");
  if(r.functional_profile&&(r.functional_profile.schema_version!=="athlete-functional-profile-v1"||!Array.isArray(r.functional_profile.points)||!Array.isArray(r.functional_profile.test_points)||!Array.isArray(r.functional_profile.training_context?.zones)))throw new Error("Невалиден скоростно-издръжливостен профил.");
  const zones=r.speed_zones;
  if(zones?.status==="AVAILABLE"&&(!Array.isArray(zones.zones)||zones.zones.length!==5||zones.zones.some((z,i)=>
    z.zone!==`Z${i+1}`||!finite(z.low_kmh)||z.low_kmh<0||(i<4? !finite(z.high_kmh)||z.high_kmh<=z.low_kmh : z.high_kmh!==null)
    ||i>0&&z.low_kmh!==zones.zones[i-1].high_kmh)))throw new Error("Невалидни скоростни зони.");
  const dose=r.dosing_model;
  if(dose&&(dose.model_version!=="dosing-index-test-30-70-v1"||!["AVAILABLE","UNAVAILABLE"].includes(dose.status)
    ||dose.weights?.index!==.3||dose.weights?.tests!==.7||dose.is_maximal_test!==false||dose.use!=="METHOD_DOSING"
    ||!Array.isArray(dose.points)||dose.status==="AVAILABLE"&&dose.points.length<2
    ||dose.points.some((p,i)=>![p.duration_s,p.speed_kmh,p.distance_m,p.index_speed_kmh,p.test_speed_kmh].every(v=>finite(v)&&v>0)
      ||Math.abs(p.speed_kmh-(.3*p.index_speed_kmh+.7*p.test_speed_kmh))>1e-8
      ||p.estimated_hr_bpm!==null&&(!finite(p.estimated_hr_bpm)||p.estimated_hr_bpm<=0)
      ||i>0&&(p.duration_s<=dose.points[i-1].duration_s||p.speed_kmh>=dose.points[i-1].speed_kmh||p.distance_m<=dose.points[i-1].distance_m))))throw new Error("Невалидна обща крива за дозиране.");
  const cs=r.critical_speed?.standardized;
  if(cs?.status==="MODEL_ESTIMATE"&&(!finite(cs.speed_kmh)||cs.speed_kmh<=0||!Array.isArray(cs.points)||cs.points.length!==2
    ||cs.points.some((p,i)=>p.duration_s!==[180,720][i]||!finite(p.speed_kmh)||p.speed_kmh<=0)))throw new Error("Невалидна стандартизирана CS.");
  return r;
}

export async function saveModel(kind:"recovery"|"speed-test"|"speed-test-manual",payload:unknown){
  const result=await fetch("/api/athlete/models",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind,payload})});
  const body=await result.json();
  if(!result.ok)throw new Error(body.error||"Записването не завърши.");
  return body as {saved:boolean;revision:number;entry_key?:string};
}
