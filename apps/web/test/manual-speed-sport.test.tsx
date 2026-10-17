// @vitest-environment jsdom
import {act} from "react";
import {createRoot} from "react-dom/client";
import {renderToStaticMarkup} from "react-dom/server";
import {afterEach,expect,it,vi} from "vitest";
import {ManualSpeedTestEditor} from "../components/manual-speed-test";
import {SpeedModelPanel} from "../components/speed-model";
import {saveModel,type SpeedModel,type SpeedTest} from "../lib/models";

const router=vi.hoisted(()=>({refresh:vi.fn(),push:vi.fn()}));
vi.mock("next/navigation",()=>({useRouter:()=>router}));
vi.mock("../lib/models",async original=>({...await original<typeof import("../lib/models")>(),saveModel:vi.fn()}));
afterEach(()=>{vi.clearAllMocks();vi.unstubAllGlobals();});

const test:SpeedTest={kind:"SPEED_TEST",entry_key:"manual_123",revision:3,recorded_at:"2026-09-28",payload:{
  source:"MANUAL",test_id:"22222222-2222-4222-8222-222222222222",name:"800 м",day:"2026-09-28",sport:"Hike",
  start_s:0,duration_s:125,distance_m:800,speed_kmh:23.04,measurement_input:"DISTANCE",flat_terrain:true,
  maximal:true,comparable:true,conditions:"Писта",enabled:true,use_for_cs:true,coverage_percent:null}};
const model:SpeedModel={schema_version:"speed-model-v1",model_version:"test",sport:"Hike",sports:["Hike","Run"],
  test_window:{start:"2026-07-03",end:"2026-09-30"},tests:[test],activities:[],status:"UNAVAILABLE",
  points:[],warnings:[],active_test_keys:[],active_test_count:0,volume_weekly_min:{},zone_corrections:{},
  history_days:40,critical_speed:{status:"INSUFFICIENT_TESTS",count:0},correction_applied_fraction:0,
  prediction:null,source_generation_id:null,source_revision:null,hr_speed_range_kmh:null};

it("moves a manual result to its selected sport while preserving its identity and revision",async()=>{
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);
  vi.mocked(saveModel).mockResolvedValue({saved:true,entry_key:test.entry_key,revision:4});
  const div=document.createElement("div"),root=createRoot(div);
  try{
    await act(async()=>root.render(<ManualSpeedTestEditor model={model} test={test} onReset={()=>{}}/>));
    const select=[...div.querySelectorAll("label")].find(l=>l.textContent?.startsWith("Спорт на теста"))!.querySelector("select")!;
    expect(select.value).toBe("Hike");
    await act(async()=>{select.value="Run";select.dispatchEvent(new Event("change",{bubbles:true}));});
    await act(async()=>div.querySelector("form")!.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true})));
    expect(saveModel).toHaveBeenCalledWith("speed-test-manual",expect.objectContaining({
      test_id:test.payload.test_id,sport:"Run",duration_s:125,distance_m:800,expected_revision:3}));
    expect(router.push).toHaveBeenCalledWith("/speed?sport=Run#speed-tests");
    expect(router.refresh).toHaveBeenCalled();
  }finally{await act(async()=>root.unmount());}
});

it("makes tests stored under another sport discoverable and explains stale indices",()=>{
  const html=renderToStaticMarkup(<SpeedModelPanel model={{...model,sport:"Run",index_admission:{activities:2,excluded:0,refresh_required:2}}} canEdit/>);
  expect(html).toContain("Тестове за други спортове");
  expect(html).toContain('href="/speed?sport=Hike#speed-tests"');
  expect(html).toContain("Има активности за преизчисляване");
  expect(html).toContain("Обнови данните");
});
