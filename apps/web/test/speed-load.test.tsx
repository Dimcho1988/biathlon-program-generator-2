// @vitest-environment jsdom
import {beforeEach,afterEach,expect,it,vi} from "vitest";
import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {GET} from "../app/api/athlete/models/speed-load/route";
import {currentAuthorizedAthlete} from "../lib/account-access";
import {getSpeedLoad} from "../lib/api";
import {parseSpeedLoad,type SpeedLoad} from "../lib/speed-load";
import {SpeedLoadSummary} from "../components/speed-load-summary";
vi.mock("../lib/account-access",()=>({currentAuthorizedAthlete:vi.fn()}));
vi.mock("../lib/api",()=>({getSpeedLoad:vi.fn()}));
let root:Root,container:HTMLDivElement;
beforeEach(()=>{vi.resetAllMocks();vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);container=document.createElement("div");document.body.append(container);root=createRoot(container);});
afterEach(async()=>{await act(async()=>root.unmount());container.remove();vi.unstubAllGlobals();});
const fixture=():SpeedLoad=>({schema_version:"speed-load-history-v1",model_version:"independent-speed-load-causal-ti-v1",
  status:"PARTIAL",sport:null,sports:["Run","Ride"],source_generation_id:"g",source_revision:1,start_date:"2026-09-01",end_date:"2026-10-01",
  load_role:"PARALLEL_ESTIMATE_NOT_ADDED_TO_HR",mapping_policy:"SAME_SPORT_PRIOR_40_DAYS_EXCLUDING_CURRENT_DAY",
  recorded_minutes:40,classified_minutes:20,coverage_percent:50,zones:Array.from({length:5},(_,i)=>({zone:`Z${i+1}`,minutes:i===2?20:0,equivalent_minutes:i===2?14:0,effective_load:0,e7_daily:0,e40_daily:0,ratio_7_40:1})),
  daily:[],sport_indices:[{sport:"Ride",reference_hr_bpm:150,comparison_speed_kmh:30,reason:null,
    hr_policy:{version:"sport-hr-reference-cycling-plus7-v1",offset_bpm:7,basis:"COACH_INITIAL_ASSUMPTION",reference_zone_bounds_bpm:[100,120,140,160,180,200],sport_zone_bounds_bpm:[93,113,133,153,173,193],reference_hrmax_bpm:200,sport_hrmax_bpm:193,raw_hr_unchanged:true},
    indices:{GENERAL:{index:2.5,count:1,seconds:600}},mapping:{uses_general_index:true}}],activities:[],warnings:[]});

it("enforces athlete access for the separate ledger, including all-sport requests",async()=>{
  const request=new Request("https://onflows.test/api/athlete/models/speed-load?athlete_alias=someone-else");
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue(null);
  expect((await GET(request)).status).toBe(401);
  const access={userId:"user",actorUserId:"coach",canViewRecovery:false,athleteAlias:"selected-athlete",displayName:"Athlete",isOwner:false,canEditPlan:true};
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access);
  expect((await GET(request)).status).toBe(403);
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue({...access,canViewRecovery:true});
  vi.mocked(getSpeedLoad).mockResolvedValue(fixture());
  const result=await GET(request);
  expect(result.status).toBe(200);expect(result.headers.get("Cache-Control")).toContain("no-store");
  expect(getSpeedLoad).toHaveBeenCalledWith("selected-athlete",undefined);
  expect((await GET(new Request("https://onflows.test/api/athlete/models/speed-load?sport=bad%26query"))).status).toBe(422);
});

it("shows coverage, sport HR conversion and distinct Q without double counting",async()=>{
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:true,json:async()=>fixture()}));
  await act(async()=>root.render(<SpeedLoadSummary generation="g" revision={1}/>));
  await act(async()=>container.querySelector("button")!.click());
  expect(container.textContent).toContain("Покритие: 50%");
  expect(container.textContent).toContain("143");expect(container.textContent).toContain("150");
  expect(container.textContent).toContain("Q, екв. мин");
  expect(container.textContent).toContain("не се добавя към пулсовия товар");
  expect(container.textContent).toContain("Използван е общият индекс");
});

it("rejects impossible coverage and prevents mixing generations",async()=>{
  expect(()=>parseSpeedLoad({...fixture(),classified_minutes:100})).toThrow();
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:true,json:async()=>({...fixture(),source_revision:2})}));
  await act(async()=>root.render(<SpeedLoadSummary generation="g" revision={1}/>));
  await act(async()=>container.querySelector("button")!.click());
  expect(container.querySelector("[role=status]")!.textContent).toContain("Презареди страницата");
  expect(container.textContent).not.toContain("Q, екв. мин");
});
