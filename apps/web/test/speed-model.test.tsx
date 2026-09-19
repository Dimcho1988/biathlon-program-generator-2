import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {SpeedModelPanel} from "../components/speed-model";
import {clockTime,parseClock,testActivities,parseManualClock,manualClockTime,manualTestPayload} from "../lib/speed-tests";
import {ManualSpeedTestEditor} from "../components/manual-speed-test";
import type {SpeedModel} from "../lib/models";
import {GET} from "../app/api/athlete/models/speed-preview/route";
import {currentAuthorizedAthlete} from "../lib/account-access";
import {getSpeedPreview} from "../lib/api";
vi.mock("next/navigation",()=>({useRouter:()=>({refresh:vi.fn()})}));
vi.mock("../lib/account-access",()=>({currentAuthorizedAthlete:vi.fn()}));
vi.mock("../lib/api",()=>({getSpeedPreview:vi.fn()}));

const ref="act_"+"1".repeat(32);
const model:SpeedModel={schema_version:"speed-model-v1",model_version:"test",sport:"Run",sports:["Run","NordicSki"],
  activities:[{activity_ref:ref,name:"Easy run",day:"2026-09-10",sport:"Run"},
    {activity_ref:"act_"+"2".repeat(32),name:"Ski session",day:"2026-09-12",sport:"NordicSki"},
    {activity_ref:"act_"+"3".repeat(32),name:"Race",day:"2026-09-11",sport:"Run"}],
  status:"REFERENCE_ONLY",tests:[],active_test_count:0,active_test_keys:[],
  points:[{duration_s:10.8,speed_kmh:30,distance_m:90,estimated_hr_bpm:null},{duration_s:43516,speed_kmh:8,distance_m:96702,estimated_hr_bpm:null}],
  volume_weekly_min:{Z1:100},history_days:40,zone_corrections:{Z1:0},correction_applied_fraction:0,critical_speed:{status:"INSUFFICIENT_TESTS",count:0},
  prediction:null,warnings:[],source_generation_id:"fixture",source_revision:1,hr_speed_range_kmh:null};

describe("speed test selection and prediction",()=>{
  it("preserves fractional manual test times without loosening activity window parsing",()=>{
    expect(parseManualClock("0:10,8")).toBe(10.8);
    expect(parseManualClock("2:15.125")).toBe(135.125);
    expect(parseManualClock("1:02:03")).toBe(3723);
    expect(manualClockTime(135.125)).toBe("2:15,125");
    expect(manualClockTime(10.8)).toBe("0:10,8");
    expect(parseClock("0:10,8")).toBeNull();
    for(const bad of ["10.8","0:10,7","1:60","725:17","1:60:00","-1:20","0:NaN"])
      expect(parseManualClock(bad)).toBeNull();
  });
  it("renders manual results as editable flat tests without broken activity links",()=>{
    const manual:SpeedModel["tests"][number]={kind:"SPEED_TEST",entry_key:"manual_123",revision:2,recorded_at:"2026-09-19",payload:{
      source:"MANUAL",test_id:"22222222-2222-4222-8222-222222222222",name:"600 м контролно",day:"2026-09-19",sport:"Run",start_s:0,
      duration_s:135.5,distance_m:600,speed_kmh:600/135.5*3.6,measurement_input:"DISTANCE",flat_terrain:true,
      enabled:true,use_for_cs:true,maximal:true,comparable:true,conditions:"Равна писта",coverage_percent:null}};
    const current={...model,tests:[manual],test_window:{start:"2026-06-21",end:"2026-09-19"}};
    const html=renderToStaticMarkup(<SpeedModelPanel model={current} canEdit/>);
    expect(html).toContain("600 м контролно");expect(html).toContain("2:15,5");expect(html).toContain("Редактирай");
    expect(html).not.toContain("/activities/undefined");expect(html).not.toContain("NaN");
    const form=renderToStaticMarkup(<ManualSpeedTestEditor model={current} test={manual} onReset={()=>{}}/>);
    expect(form).toContain('value="600"');expect(form).toContain('max="2026-09-19"');
    expect(form).toContain("Тестът е проведен на равен терен");
    const readOnly=renderToStaticMarkup(<SpeedModelPanel model={current} canEdit={false}/>);
    expect(readOnly).not.toContain("Редактирай");expect(readOnly).not.toContain("Изключи от модела");
    expect(manualTestPayload(manual,false)).toMatchObject({test_id:manual.payload.test_id,distance_m:600,enabled:false,expected_revision:2});
    expect(manualTestPayload(manual,false)).not.toHaveProperty("activity_ref");
    const bySpeed={...manual,payload:{...manual.payload,measurement_input:"SPEED" as const}};
    expect(manualTestPayload(bySpeed,true)).toHaveProperty("speed_kmh",manual.payload.speed_kmh);
    expect(manualTestPayload(bySpeed,true)).not.toHaveProperty("distance_m");
  });
  it("uses unambiguous elapsed time and rejects invalid seconds",()=>{
    expect(parseClock("12:30")).toBe(750);
    expect(parseClock("1:02:03")).toBe(3723);
    expect(clockTime(3723)).toBe("62:03");
    for(const bad of ["720","12:60","1:60:00","-1:00","1.5:20",""])expect(parseClock(bad)).toBeNull();
  });
  it("filters by exact source sport, searches names/dates and puts recent records first",()=>{
    expect(testActivities(model.activities,"Run").map(a=>a.name)).toEqual(["Race","Easy run"]);
    expect(testActivities(model.activities,"Run","2026-09-10").map(a=>a.name)).toEqual(["Easy run"]);
    expect(testActivities(model.activities,"Run","RACE").map(a=>a.name)).toEqual(["Race"]);
    expect(testActivities(model.activities,"Run","Ski")).toEqual([]);
  });
  it("explains the calibration gate and offers a read-only activity preview before saving",()=>{
    const html=renderToStaticMarkup(<SpeedModelPanel model={model} canEdit/>);
    expect(html).toContain("Индивидуалното изчисление се отключва");
    expect(html).toContain('href="#speed-tests"');
    expect(html).toContain("Търси по име или дата");
    expect(html).not.toContain("Ski session");
    expect(html).not.toContain("Запази тестовия участък");
    expect(html).toMatch(/disabled=""[^>]*>Изчисли/);
  });
  it("labels shared volumes explicitly while accepting older same-sport responses",()=>{
    const shared=renderToStaticMarkup(<SpeedModelPanel model={{...model,volume_scope:"ALL_SPORTS"}} canEdit/>);
    expect(shared).toContain("Донастройка чрез общия обем по зони");
    expect(shared).toContain("от всички спортове за 40 предходни календарни дни, приведено към седмица");
    const legacy=renderToStaticMarkup(<SpeedModelPanel model={model} canEdit/>);
    expect(legacy).toContain("от избрания спорт");
    expect(legacy).not.toContain("от всички спортове");
  });
  it("keeps submitted prediction units/value and surfaces range errors without losing the form",()=>{
    const html=renderToStaticMarkup(<SpeedModelPanel model={{...model,status:"CALIBRATED",prediction_error:"OUTSIDE_PREDICTION_RANGE"}} canEdit predictionInput="km" predictionValue="5"/>);
    expect(html).toContain('value="km" selected=""');
    expect(html).toContain('value="5"');
    expect(html).toContain("Стойността е извън допустимия обхват");
    expect(html).not.toMatch(/disabled=""[^>]*>Изчисли/);
    expect(html).toContain("Максимални тестове и контролни стартове");
  });
  it("labels a curve containing an exploratory record and its predictions as provisional",()=>{
    const trial:SpeedModel={...model,status:"CALIBRATED",active_test_count:1,active_test_keys:["trial"],exploratory_test_count:1,
      tests:[{kind:"SPEED_TEST",entry_key:"trial",revision:1,recorded_at:"2026-09-12",payload:{activity_ref:ref,start_s:0,duration_s:1000,
        speed_kmh:20,day:"2026-09-10",sport:"Run",enabled:true,use_for_cs:false,maximal:false,comparable:true,
        test_mode:"EXPLORATORY",exploratory_confirmed:true,conditions:"Synthetic complex session",coverage_percent:80}}]};
    const html=renderToStaticMarkup(<SpeedModelPanel model={trial} canEdit/>);
    expect(html).toContain("Пробна крива");expect(html).toContain("Пробна прогноза");
    expect(html).toContain("Пробен ·");expect(html).toContain("80");
    expect(html).not.toContain("Индивидуална крива");
    expect(html).toContain("Критичната скорост използва само стандартните максимални тестове");
  });
});

describe("private read-only preview route",()=>{
  const access={userId:"athlete",actorUserId:"athlete",athleteAlias:"ath-test",displayName:"Fixture",isOwner:true,canEditPlan:true,canViewRecovery:true};
  const request=(suffix="")=>new Request(`https://web.test/api/athlete/models/speed-preview?activity_ref=${ref}${suffix}`);
  beforeEach(()=>{vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access);vi.mocked(getSpeedPreview).mockResolvedValue({schema_version:"speed-test-preview-v1",activity_ref:ref,sport:"Run",name:"Fixture",day:"2026-09-12",elapsed_s:1200,status:"READY",source_run_key:null,series:[],selection:null});});
  afterEach(()=>vi.clearAllMocks());
  it("uses only the verified profile and permits authorized readers without writing",async()=>{
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({...access,canEditPlan:false});
    const result=await GET(request("&start_s=120&duration_s=720&athlete_alias=someone-else"));
    expect(result.status).toBe(200);expect(result.headers.get("Cache-Control")).toContain("no-store");
    expect(getSpeedPreview).toHaveBeenCalledWith("ath-test",{activity_ref:ref,start_s:"120",duration_s:"720"});
  });
  it("forwards only an explicit supported calibration mode",async()=>{
    expect((await GET(request("&start_s=0&duration_s=1000&test_mode=EXPLORATORY"))).status).toBe(200);
    expect(getSpeedPreview).toHaveBeenCalledWith("ath-test",{activity_ref:ref,start_s:"0",duration_s:"1000",test_mode:"EXPLORATORY"});
    vi.mocked(getSpeedPreview).mockClear();
    expect((await GET(request("&test_mode=OPEN"))).status).toBe(422);
    expect(getSpeedPreview).not.toHaveBeenCalled();
  });
  it("rejects missing permissions and invalid windows before fetching data",async()=>{
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue(null);expect((await GET(request())).status).toBe(401);
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({...access,canViewRecovery:false});expect((await GET(request())).status).toBe(403);
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access);
    for(const query of ["&start_s=-1&duration_s=720","&start_s=0","&start_s=0&duration_s=10","&start_s=0&duration_s=Infinity"])
      expect((await GET(request(query))).status).toBe(422);
    expect(getSpeedPreview).not.toHaveBeenCalled();
  });
});
