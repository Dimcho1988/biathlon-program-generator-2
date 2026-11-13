import {renderToStaticMarkup} from "react-dom/server";
import {expect,it} from "vitest";
import {InitialThresholdPanel} from "../components/speed-model";
import {parseSpeedModel,type InitialThresholdEstimate} from "../lib/models";

const value:InitialThresholdEstimate={model_version:"heuristic_v0.1_unvalidated",status:"AVAILABLE",
  use_status:"THRESHOLD_ONLY",reason:null,p_percent:80.5476,v_threshold_estimated_kmh:14.898,
  speed_role:"RAW_MOVING_SPEED",is_estimated:true,calibration_eligible:false,used_for_zones:false,
  inputs:{H:8,v:12,S:4},window:{start:"2026-09-30",end_exclusive:"2026-10-28",days:28}};

it("shows a threshold estimate with raw-speed provenance and no invented zone table",()=>{
  const html=renderToStaticMarkup(<InitialThresholdPanel value={value}/>);
  expect(html).toContain("14,9 км/ч");
  expect(html).toContain("28 пълни дни");
  expect(html).toContain("без корекция за релефа");
  expect(html).not.toContain("<table");
});
it("asks for missing experience without implying zero years",()=>{
  const html=renderToStaticMarkup(<InitialThresholdPanel value={{...value,status:"UNAVAILABLE",use_status:"UNAVAILABLE",reason:"TRAINING_YEARS_REQUIRED",inputs:undefined,p_percent:null,v_threshold_estimated_kmh:null}}/>);
  expect(html).toContain("Добави тренировъчния стаж");
  expect(html).not.toContain("0 г.");
});
it("keeps the fallback hidden when a reliable anchor supersedes it",()=>{
  expect(renderToStaticMarkup(<InitialThresholdPanel value={{...value,use_status:"SUPERSEDED"}}/>)).toBe("");
});
it("rejects provenance that could turn an estimated raw-speed value into calibration evidence",()=>{
  const model={schema_version:"speed-model-v1",points:[],tests:[],initial_threshold:value};
  expect(parseSpeedModel(model).initial_threshold).toEqual(value);
  for(const patch of [{calibration_eligible:true},{speed_role:"FLAT_EQUIVALENT"},{used_for_zones:true},{p_percent:NaN}]){
    expect(()=>parseSpeedModel({...model,initial_threshold:{...value,...patch}})).toThrow(/начална прагова/);
  }
});
