// @vitest-environment jsdom
import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {afterEach,beforeEach,expect,it,vi} from "vitest";
import {SpeedZonesSummary,StandardizedCriticalSpeed} from "../components/speed-zones-summary";
import {RaceDurationSummary} from "../components/race-duration-estimate";
import {parseSpeedModel,type SpeedZoneProfile} from "../lib/models";
import {parseTrainingObservations} from "../lib/training-observations";

let root:Root,container:HTMLDivElement;
beforeEach(()=>{vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);container=document.createElement("div");document.body.append(container);root=createRoot(container);});
afterEach(async()=>{await act(async()=>root.unmount());container.remove();vi.unstubAllGlobals();});
const zones:SpeedZoneProfile={status:"AVAILABLE",zones:Array.from({length:5},(_,i)=>({zone:`Z${i+1}`,low_kmh:i*4,high_kmh:i<4?(i+1)*4:null,source:"CURVE_EXPERT_TIME"}))};

it("loads separate speed exposure lazily and refuses another generation",async()=>{
  const fetchMock=vi.fn(async()=>Response.json({status:"AVAILABLE",source_generation_id:"new",source_revision:2,zones:[],classified_minutes:120}));vi.stubGlobal("fetch",fetchMock);
  await act(async()=>root.render(<SpeedZonesSummary profile={zones} sport="Run" generation="old" revision={1}/>));
  expect(fetchMock).not.toHaveBeenCalled();
  await act(async()=>container.querySelector("button")!.click());
  expect(container.textContent).toContain("Моделът е обновен");
  expect(container.textContent).not.toContain("Класифицирани по надеждна скорост: 120");
  fetchMock.mockResolvedValueOnce(Response.json({status:"AVAILABLE",source_generation_id:"old",source_revision:1,zones:[{zone:"Z1",minutes:120}],classified_minutes:120}));
  await act(async()=>container.querySelector("button")!.click());
  expect(container.textContent).toContain("120 мин");
  expect(container.textContent).toContain("не се добавя повторно");
});

it("labels standardized CS as derived and does not invent lactate for race pace",async()=>{
  await act(async()=>root.render(<><StandardizedCriticalSpeed value={{status:"MODEL_ESTIMATE",speed_kmh:17.24,points:[{duration_s:180,speed_kmh:21.58,evidence:"INTERPOLATED"},{duration_s:720,speed_kmh:18.33,evidence:"INTERPOLATED"}]}}/><RaceDurationSummary value={{source:"SPEED_DURATION",duration_min:13.2,sport:"Run",distance_m:4000,specific_reference:{bands:[{role:"RACE",label:"Състезателно темпо",speed_kmh:18.1,pace_seconds_km:199,lactate_reference:null}]}}}/></>));
  expect(container.textContent).toContain("CS по 3 и 12 мин");
  expect(container.textContent).toContain("не е оценка на грешката");
  expect(container.textContent).toContain("Няма измерена опора при тази скорост");
});

it("accepts speed-only personal lactate tests and rejects unordered axes",()=>{
  const profile={lactate_profiles:[{sport:"Run",source:"TEST",assessed_on:"2026-09-30",protocol:"3 min stages",device:"",note:"",zone_ranges:{},stages:[14,16,18].map((speed_kmh,i)=>({hr_bpm:null,speed_kmh,lactate_mmol:i+1,duration_min:3}))}]};
  expect(parseTrainingObservations(profile).lactate_profiles[0].stages[0].hr_bpm).toBeNull();
  profile.lactate_profiles[0].stages[2].speed_kmh=15;
  expect(()=>parseTrainingObservations(profile)).toThrow(/нарастващ/);
});

it("rejects invalid zone boundaries and malformed standardized CS at the API boundary",()=>{
  const model={schema_version:"speed-model-v1",points:[],tests:[],speed_zones:zones};
  expect(parseSpeedModel(model).speed_zones?.zones).toHaveLength(5);
  const bad=structuredClone(model);bad.speed_zones.zones[2].high_kmh=1;
  expect(()=>parseSpeedModel(bad)).toThrow(/скоростни зони/);
  expect(()=>parseSpeedModel({...model,critical_speed:{standardized:{status:"MODEL_ESTIMATE",speed_kmh:17,points:[]}}})).toThrow(/CS/);
});
