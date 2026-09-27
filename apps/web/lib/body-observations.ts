export const ANALYTES = {
  UREA:{label:"Урея (не BUN)",units:["mmol/L","mg/dL"]},
  CK:{label:"Креатинкиназа · CK / КФК",units:["U/L"]},
  HEMOGLOBIN:{label:"Хемоглобин",units:["g/L","g/dL"]},
  TESTOSTERONE:{label:"Общ тестостерон",units:["nmol/L","ng/dL","ng/mL"]},
  CORTISOL:{label:"Кортизол",units:["nmol/L","ug/dL"]},
  FERRITIN:{label:"Феритин",units:["ug/L","ng/mL"]},
  CRP:{label:"CRP",units:["mg/L","mg/dL"]},
  TSAT:{label:"Трансферинова сатурация",units:["%"]},
} as const;
export type Analyte = keyof typeof ANALYTES;
export interface WeightPair {session:number;before_kg:number|null;after_kg:number|null;comparable:boolean;fluid_l:number|null;urine_l:number|null;ratio?:number|null;mass_loss_percent?:number|null}
export interface WeightReport {day:string;morning_kg:number|null;morning_standardized:boolean;sessions:WeightPair[];note:string;body_water_percent?:number|null;body_fat_percent?:number|null;composition_method?:string}
export interface WeightContext {report:WeightReport|null;revision:number;morning_mean_kg:number|null;morning_count:number;window_days:number;minimum_morning_days:number;morning_ratio:number|null;morning_change_percent:number|null;status:string;gap_days:number|null;session_ratio:number|null;paired_sessions:number;sessions:WeightPair[];automatic_weight:0;validated:false}
export interface LabResult {analyte:Analyte;value:number;unit:string;qualifier:"EQ"|"LT"|"GT";sample:"SERUM"|"PLASMA"|"WHOLE_BLOOD"|"SALIVA";reference_low:number|null;reference_high:number|null;normalized_value?:number;normalized_unit?:string;reference_status?:string;baseline_median?:number|null;baseline_count?:number;change_percent?:number|null}
export interface LabPayload {sample_id:string;day:string;collection_time:string|null;laboratory:string;protocol:string;comparable:boolean;hours_since_training:number|null;fasting:"YES"|"NO"|"UNKNOWN";note:string;results:LabResult[]}
export interface LabReport {entry_key:string;revision:number;payload:LabPayload;results:LabResult[];testosterone_cortisol_ratio:number|null;age_days:number;outside_reference:boolean;automatic_weight:0}
export interface BodyObservationsDay {weight:WeightContext;lab_sample_keys:string[];context_status:"REVIEW_LAB_REFERENCE"|"OBSERVATIONS_AVAILABLE"|"NO_OBSERVATIONS"}

export const optionalNumber = (f:FormData,key:string) => {const v=String(f.get(key)??"").trim();return v===""?null:Number(v);};
export function weightPayload(f:FormData,day:string,revision:number) {
  return {day,expected_revision:revision,morning_kg:optionalNumber(f,"morning_kg"),morning_standardized:f.get("morning_standardized")==="on",note:String(f.get("weight_note")||""),body_water_percent:optionalNumber(f,"body_water_percent"),body_fat_percent:optionalNumber(f,"body_fat_percent"),composition_method:String(f.get("composition_method")||""),
    sessions:[1,2,3,4].map(session=>({session,before_kg:optionalNumber(f,`before_${session}`),after_kg:optionalNumber(f,`after_${session}`),comparable:f.get(`comparable_${session}`)==="on",fluid_l:optionalNumber(f,`fluid_${session}`),urine_l:optionalNumber(f,`urine_${session}`)})).filter(p=>p.before_kg!==null||p.after_kg!==null)};
}
export function labPayload(f:FormData,day:string,sampleId:string,revision:number) {
  const results=(Object.keys(ANALYTES) as Analyte[]).filter(k=>optionalNumber(f,`${k}_value`)!==null).map(analyte=>({analyte,value:optionalNumber(f,`${analyte}_value`),unit:f.get(`${analyte}_unit`),qualifier:f.get(`${analyte}_qualifier`),sample:f.get(`${analyte}_sample`),reference_low:optionalNumber(f,`${analyte}_low`),reference_high:optionalNumber(f,`${analyte}_high`)}));
  if(!results.length)throw new Error("Въведете поне един лабораторен резултат.");
  return {day,sample_id:sampleId,expected_revision:revision,collection_time:f.get("collection_time")||null,laboratory:String(f.get("laboratory")||"").trim(),protocol:String(f.get("protocol")||"").trim(),comparable:f.get("lab_comparable")==="on",hours_since_training:optionalNumber(f,"hours_since_training"),fasting:f.get("fasting"),note:String(f.get("lab_note")||""),results};
}
