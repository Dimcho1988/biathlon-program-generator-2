import {describe,it,expect,vi} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {BodyObservations} from "../components/body-observations";
import {labPayload,weightPayload,type LabReport,type WeightContext} from "../lib/body-observations";
import {responseFixture} from "../lib/response-fixture";
import {parseResponseHistory} from "../lib/response-monitoring";

vi.mock("next/navigation",()=>({useRouter:()=>({refresh:vi.fn()})}));
const weight:WeightContext={report:null,revision:0,morning_mean_kg:70,morning_count:3,window_days:7,minimum_morning_days:4,morning_ratio:null,morning_change_percent:null,status:"INSUFFICIENT_HISTORY",gap_days:2,session_ratio:null,paired_sessions:0,sessions:[],automatic_weight:0,validated:false};
const report:LabReport={entry_key:"a".repeat(32),revision:1,payload:{sample_id:"a".repeat(32),day:"2026-09-08",collection_time:"08:00",laboratory:"Примерна лаборатория",protocol:"Примерен протокол",comparable:false,hours_since_training:null,fasting:"UNKNOWN",note:"",results:[]},results:[{analyte:"CK",value:220,unit:"U/L",qualifier:"EQ",sample:"SERUM",reference_low:null,reference_high:180,reference_status:"HIGH",baseline_count:0}],testosterone_cortisol_ratio:null,age_days:1,outside_reference:true,automatic_weight:0};

describe("body observation forms and context",()=>{
  it("validates the API extension and rejects a fabricated index across missing data",()=>{
    expect(parseResponseHistory(responseFixture).body_observations_version).toBe("body-observations-v2");
    const bad=structuredClone(responseFixture);
    bad.days[0].body_observations!.weight.morning_ratio=1;
    bad.days[0].body_observations!.weight.morning_count=3;
    expect(()=>parseResponseHistory(bad)).toThrow("Невалидни наблюдения");
    const legacy=structuredClone(responseFixture);
    delete legacy.body_observations_version;delete legacy.lab_reports;
    legacy.days.forEach(d=>delete d.body_observations);
    expect(parseResponseHistory(legacy).days).toHaveLength(14);
  });
  it("keeps blank distinct from measured zero and retains unpaired measurements",()=>{
    const f=new FormData();f.set("before_1","70");f.set("fluid_1","0");f.set("comparable_1","on");
    const p=weightPayload(f,"2026-09-09",2);
    expect(p.morning_kg).toBeNull();expect(p.sessions).toHaveLength(1);
    expect(p.sessions[0]).toMatchObject({before_kg:70,after_kg:null,fluid_l:0,urine_l:null,comparable:true});
    expect(p.expected_revision).toBe(2);
  });
  it("sends only measured lab values and does not replace blanks with zero",()=>{
    const f=new FormData();
    expect(()=>labPayload(f,"2026-09-09","a".repeat(32),0)).toThrow();
    f.set("CRP_value","0");f.set("CRP_unit","mg/L");f.set("CRP_sample","SERUM");f.set("CRP_qualifier","LT");
    const p=labPayload(f,"2026-09-09","a".repeat(32),1);
    expect(p.results).toHaveLength(1);expect(p.results[0]).toMatchObject({analyte:"CRP",value:0,reference_low:null,reference_high:null,qualifier:"LT"});
    expect(p.collection_time).toBeNull();expect(p.expected_revision).toBe(1);
  });
  it("shows missing coverage, dates old findings, and does not call ratios validated stress",()=>{
    const day={...responseFixture.days.at(-1)!,body_observations:{weight,lab_sample_keys:[],context_status:"NO_OBSERVATIONS" as const}};
    const html=renderToStaticMarkup(<BodyObservations day={day} history={{...responseFixture,lab_reports:[report]}} canReport canEditPlan/>);
    for(const text of ["3/7","Влияят гликогенът","отпреди 1 ден","Общата оценка не отменя този сигнал","работни настройки за тестване","Общ тестостерон","Трансферинова сатурация"])expect(html).toContain(text);
    expect(html).not.toContain("NaN");expect(html).toContain('name="before_1"');
  });
  it("hides future lab records and disables another athlete's weight entry",()=>{
    const day={...responseFixture.days[0],body_observations:{weight,lab_sample_keys:[],context_status:"NO_OBSERVATIONS" as const}};
    const html=renderToStaticMarkup(<BodyObservations day={day} history={{...responseFixture,lab_reports:[report]}} canReport={false} canEditPlan={false}/>);
    expect(html).not.toContain("Примерна лаборатория");expect(html).not.toContain("Добави изследване");expect(html).toContain('fieldset disabled=""');
  });
});
