import { GROUPS, RESPONSE_VERSION, type ResponseHistory, type ResponseDay } from "./response-monitoring";

// Synthetic example only. Real mode never imports or falls back to these values.
const weights={subjective:.5,rpe:.3,physiology:.2};
export const responseFixture: ResponseHistory = {
  schema_version:RESPONSE_VERSION,today:"2026-09-09",timezone:"Europe/Sofia",period_start:"2026-08-27",period_end:"2026-09-09",
  mode:"OBSERVATION_ONLY",automatic_increase:false,changes_recovery:false,weights,revision:null,
  days:Array.from({length:14},(_,i):ResponseDay=>{
    const day=new Date(Date.UTC(2026,7,27+i)).toISOString().slice(0,10);
    const level=[2,2,3,3,4,4,3,3,2,2,2,2,2,2][i];
    const scores={subjective:i===7?null:(level-1)*25,rpe:i===9?null:40+level*7,physiology:i===9?null:35+level*9};
    const groups=GROUPS.map(key=>({key,score:scores[key],weight:weights[key],contribution:scores[key]===null?null:scores[key]!*weights[key]}));
    const total=groups.every(g=>g.score!==null)?groups.reduce((s,g)=>s+g.contribution!,0):null;
    return {day,total,coverage:groups.reduce((s,g)=>s+(g.score===null?0:g.weight*100),0),groups,
      state:level>2?"EXPECTED_ELEVATION":"WITHIN_USUAL",phase:i<8?"BUILD":"RECOVERY",baseline:{median:25,spread:10,count:20},
      deviation:scores.subjective===null?null:(scores.subjective-25)/10,baseline_anchor:"2026-08-26",
      daily_report:i===7?null:{day,observed_at:null,sleep_quality:level,fatigue:level,soreness:level,stress:level,motivation:level,pain_or_illness:false,note:""},daily_revision:i===7?0:1,
      device_metrics:{resting_hr:{value:48+level,unit:"bpm"},hrv:{value:75-level*3,unit:"ms"}},
      physiology:{resting_hr:{raw:48+level,score:scores.physiology,baseline:{median:50,spread:3,count:22}},hrv:{raw:75-level*3,score:scores.physiology,baseline:{median:4.2,spread:.12,count:21}}},
      rpe_sessions:[],block_key:"2026-08-27",automatic_action:"NONE",data_age_days:13-i};
  }),
  sessions:[{activity_ref:"act_"+"1".repeat(32),day:"2026-09-08",name:"Примерна тренировка с ролкови ски",sport:"NordicSki",rpe:5,duration_minutes:75,suggested_duration_minutes:75,timing:"DELAYED",source:"ONFLOWS",provider_rpe:null,revision:1,note:"",srpe_load:375,expected_rpe:4,deviation_score:65,comparable_count:4}],
  blocks:[{kind:"BLOCK",entry_key:"2026-08-27",revision:1,recorded_at:"2026-08-26T08:00:00Z",payload:{phase:"BUILD",start:"2026-08-27",load_end:"2026-09-03",recovery_end:"2026-09-09"},summary:{peak_deviation:5,elevated_days:5,observed_days:13,tracked_days:14,returned_on:"2026-09-05",status:"OBSERVED_RETURN"}}],
  tests:[],
};
