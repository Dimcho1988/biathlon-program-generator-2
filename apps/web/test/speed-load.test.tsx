// @vitest-environment jsdom
import {beforeEach,afterEach,expect,it,vi} from "vitest";
import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {GET} from "../app/api/athlete/models/speed-load/route";
import {currentAuthorizedAthlete} from "../lib/account-access";
import {getSpeedLoad} from "../lib/api";
import {parseSpeedLoad,type SpeedLoad} from "../lib/speed-load";
import {SpeedWorkReport} from "../components/speed-work-report";
import {CompletedWorkSection} from "../components/completed-work-section";
import {completedWorkFixture} from "../lib/fixture";
import {SpeedLoadSummary} from "../components/speed-load-summary";
import {speedLoadCacheScope} from "../lib/speed-load-scope";
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

it("accepts the adjacent Tmax speed-load version while retaining old reports",()=>{
  expect(parseSpeedLoad(fixture()).model_version).toBe("independent-speed-load-causal-ti-v1");
  const updated={...fixture(),model_version:"independent-speed-load-causal-ti-v2-adjacent-tmax",component_load_version:"component-load-adjacent-tmax-v1"};
  expect(parseSpeedLoad(updated).component_load_version).toBe("component-load-adjacent-tmax-v1");
  expect(()=>parseSpeedLoad({...updated,model_version:"unknown-load-model"})).toThrow();
});

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
  expect(getSpeedLoad).toHaveBeenCalledWith("selected-athlete",undefined,undefined,undefined);
  expect((await GET(new Request("https://onflows.test/api/athlete/models/speed-load?sport=bad%26query"))).status).toBe(422);
});

it("shows coverage, sport HR conversion and distinct Q without double counting",async()=>{
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:true,json:async()=>fixture()}));
  await act(async()=>root.render(<SpeedLoadSummary generation="g" revision={1}/>));
  await act(async()=>container.querySelector("button")!.click());
  expect(container.textContent).toContain("Покритие: 50%");
  expect(container.textContent).toContain("143");expect(container.textContent).toContain("150");
  expect(container.textContent).toContain("Q · ч:мм:сс");
  expect(container.textContent).toContain("не се добавя към пулсовия товар");
  expect(container.textContent).toContain("Използван е общият индекс");
});

it("rejects impossible coverage and prevents mixing generations",async()=>{
  expect(()=>parseSpeedLoad({...fixture(),classified_minutes:100})).toThrow();
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:true,json:async()=>({...fixture(),source_revision:2})}));
  await act(async()=>root.render(<SpeedLoadSummary generation="g" revision={1}/>));
  await act(async()=>container.querySelector("button")!.click());
  expect(container.querySelector("[role=status]")!.textContent).toContain("Презареди страницата");
  expect(container.textContent).not.toContain("Q · ч:мм:сс");
});

it("forwards validated dates and never accepts an athlete from query parameters", async()=>{
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue({userId:"user",actorUserId:"coach",canViewRecovery:true,athleteAlias:"selected-athlete",displayName:"Athlete",isOwner:false,canEditPlan:true});
  vi.mocked(getSpeedLoad).mockResolvedValue(fixture());
  const base="https://onflows.test/api/athlete/models/speed-load?";
  expect((await GET(new Request(base+"period_start=2026-09-01&period_end=2026-10-01&athlete_alias=wrong"))).status).toBe(200);
  expect(getSpeedLoad).toHaveBeenCalledWith("selected-athlete",undefined,"2026-09-01","2026-10-01");
  for(const query of ["period_start=2026-09-01","period_start=2026-02-30&period_end=2026-10-01","period_start=2026-10-01&period_end=2026-09-01"])
    expect((await GET(new Request(base+query))).status).toBe(422);
});

it("forwards a snapshot analysis date independently of a custom report period", async () => {
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue({userId:"user",actorUserId:"coach",canViewRecovery:true,athleteAlias:"selected-athlete",displayName:"Athlete",isOwner:false,canEditPlan:true});
  vi.mocked(getSpeedLoad).mockResolvedValue(fixture());
  const base = "https://onflows.test/api/athlete/models/speed-load?";
  expect((await GET(new Request(base + "as_of=2026-10-01&athlete_alias=wrong"))).status).toBe(200);
  expect(getSpeedLoad).toHaveBeenCalledWith("selected-athlete", undefined, undefined, undefined, "2026-10-01");
  for (const query of ["as_of=", "as_of=2026-02-30", "as_of=2026-10-01&period_start=2026-09-01", "as_of=2026-10-01&period_end=2026-10-01"])
    expect((await GET(new Request(base + query))).status).toBe(422);
  expect(getSpeedLoad).toHaveBeenCalledTimes(1);
});

it("switches the completed report to speed for the exact selected period and preserves the choice in its form",async()=>{
  const report={...completedWorkFixture,period_start:"2026-09-01",period_end:"2026-10-01"};
  const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>fixture()});vi.stubGlobal("fetch",fetcher);
  await act(async()=>root.render(<CompletedWorkSection report={report} selectable allowSpeed generation="g" revision={1}/>));
  expect(fetcher).not.toHaveBeenCalled();
  await act(async()=>[...container.querySelectorAll("button")].find(b=>b.textContent==="По скорост")!.click());
  expect(fetcher.mock.calls[0][0]).toBe("/api/athlete/models/speed-load?period_start=2026-09-01&period_end=2026-10-01");
  expect(container.textContent).toContain("Натоварване по скоростни зони");
  expect(container.textContent).toContain("Скоростно покритие: 50%");
  expect(container.textContent).not.toContain("Натоварване по пулсови зони");
  expect((container.querySelector('[name="report_source"]') as HTMLInputElement).value).toBe("speed");
  await act(async()=>[...container.querySelectorAll("button")].find(b=>b.textContent==="По пулс")!.click());
  expect(container.textContent).toContain("Натоварване по пулсови зони");
  expect(container.querySelector('[aria-label="Отчет по скорост"]')!.parentElement!.hidden).toBe(true);
  await act(async()=>[...container.querySelectorAll("button")].find(b=>b.textContent==="По скорост")!.click());
  expect(container.querySelector('[aria-label="Отчет по скорост"]')!.parentElement!.hidden).toBe(false);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it.each(["date", "generation"])("rejects a speed report with a different %s",async(reason)=>{
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:true,json:async()=>({...fixture(),...(reason==="date"?{start_date:"2026-09-02"}:{source_revision:2})})}));
  await act(async()=>root.render(<SpeedWorkReport start="2026-09-01" end="2026-10-01" generation="g" revision={1} totalDuration={40}/>));
  expect(container.textContent).not.toContain("Натоварване по скоростни зони");
  expect(container.querySelector("[role=status]")!.textContent).toContain(reason==="date"?"не съответства":"Презаредете");
});


it("does not offer a speed report to a shared profile without recovery access",async()=>{
  const fetcher=vi.fn();vi.stubGlobal("fetch",fetcher);
  await act(async()=>root.render(<CompletedWorkSection report={completedWorkFixture} selectable initialSource="speed" allowSpeed={false}/>));
  expect(container.textContent).not.toContain("По скорост");
  expect(container.textContent).toContain("Натоварване по пулсови зони");
  expect(fetcher).not.toHaveBeenCalled();
});

it("keeps an in-flight speed report when switching to HR and invalidates it for a new period", async()=>{
  let resolve!:(value:unknown)=>void;
  const fetcher=vi.fn().mockImplementation(()=>new Promise(done=>{resolve=done;}));
  vi.stubGlobal("fetch",fetcher);
  const report={...completedWorkFixture,period_start:"2026-09-01",period_end:"2026-10-01"};
  const click=async(label:string)=>act(async()=>[...container.querySelectorAll("button")].find(b=>b.textContent===label)!.click());
  await act(async()=>root.render(<CompletedWorkSection report={report} selectable allowSpeed generation="g" revision={1}/>));
  await click("По скорост");
  const signal=fetcher.mock.calls[0][1].signal as AbortSignal;
  await click("По пулс"); await click("По скорост");
  expect(signal.aborted).toBe(false); expect(fetcher).toHaveBeenCalledTimes(1);
  await act(async()=>resolve({ok:true,json:async()=>fixture()}));
  expect(container.textContent).toContain("Скоростно покритие: 50%");
  await act(async()=>root.render(<CompletedWorkSection report={{...report,period_start:"2026-09-02"}} selectable allowSpeed generation="g" revision={1}/>));
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(signal.aborted).toBe(true);
  expect(container.textContent).not.toContain("Скоростно покритие: 50%");
});

it("conditionally revalidates exact private results after fresh authorization, including same-generation settings changes",async()=>{
  const access={userId:"user",actorUserId:"coach",canViewRecovery:true,athleteAlias:"selected-athlete",displayName:"Athlete",isOwner:false,canEditPlan:true};
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access);
  vi.mocked(getSpeedLoad).mockResolvedValue(fixture());
  const url=`https://onflows.test/api/athlete/models/speed-load?cache_scope=${speedLoadCacheScope(access)}`;
  const first=await GET(new Request(url)),etag=first.headers.get("ETag")!;
  expect(etag).toMatch(/^"[a-f0-9]{64}"$/);
  const same=await GET(new Request(url,{headers:{"If-None-Match":etag}}));
  expect(same.status).toBe(304);expect(await same.text()).toBe("");
  expect(getSpeedLoad).toHaveBeenCalledTimes(2);
  const changed={...fixture(),sport_indices:fixture().sport_indices.map(row=>({...row,reference_hr_bpm:155}))};
  vi.mocked(getSpeedLoad).mockResolvedValue(changed);
  const refreshed=await GET(new Request(url,{headers:{"If-None-Match":etag}}));
  expect(refreshed.status).toBe(200);expect(refreshed.headers.get("ETag")).not.toBe(etag);
  expect(await refreshed.json()).toEqual(changed);
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue({...access,canViewRecovery:false});
  expect((await GET(new Request(url,{headers:{"If-None-Match":etag}}))).status).toBe(403);
  expect(getSpeedLoad).toHaveBeenCalledTimes(3);
});

it("rejects an in-flight selected-athlete/account race and scopes validators to each authorized account",async()=>{
  const first={userId:"user",actorUserId:"coach",canViewRecovery:true,athleteAlias:"selected-athlete",displayName:"Athlete",isOwner:false,canEditPlan:true};
  const second={...first,actorUserId:"other-account"};
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue(second);
  vi.mocked(getSpeedLoad).mockResolvedValue(fixture());
  const stale=`https://onflows.test/api/athlete/models/speed-load?cache_scope=${speedLoadCacheScope(first)}`;
  expect((await GET(new Request(stale))).status).toBe(409);
  expect(getSpeedLoad).not.toHaveBeenCalled();
  const current=await GET(new Request("https://onflows.test/api/athlete/models/speed-load"));
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue(first);
  const other=await GET(new Request("https://onflows.test/api/athlete/models/speed-load",{headers:{"If-None-Match":current.headers.get("ETag")!}}));
  expect(other.status).toBe(200);
  expect(other.headers.get("ETag")).not.toBe(current.headers.get("ETag"));
});
