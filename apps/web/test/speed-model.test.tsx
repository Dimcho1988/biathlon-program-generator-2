import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {SpeedModelPanel} from "../components/speed-model";
import {clockTime,parseClock,testActivities} from "../lib/speed-tests";
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
  it("keeps submitted prediction units/value and surfaces range errors without losing the form",()=>{
    const html=renderToStaticMarkup(<SpeedModelPanel model={{...model,status:"CALIBRATED",prediction_error:"OUTSIDE_PREDICTION_RANGE"}} canEdit predictionInput="km" predictionValue="5"/>);
    expect(html).toContain('value="km" selected=""');
    expect(html).toContain('value="5"');
    expect(html).toContain("Стойността е извън допустимия обхват");
    expect(html).not.toMatch(/disabled=""[^>]*>Изчисли/);
    expect(html).toContain("Максимални тестове и контролни стартове");
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
  it("rejects missing permissions and invalid windows before fetching data",async()=>{
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue(null);expect((await GET(request())).status).toBe(401);
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue({...access,canViewRecovery:false});expect((await GET(request())).status).toBe(403);
    vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access);
    for(const query of ["&start_s=-1&duration_s=720","&start_s=0","&start_s=0&duration_s=10","&start_s=0&duration_s=Infinity"])
      expect((await GET(request(query))).status).toBe(422);
    expect(getSpeedPreview).not.toHaveBeenCalled();
  });
});
