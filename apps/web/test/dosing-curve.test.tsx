// @vitest-environment jsdom
import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {afterEach,beforeEach,expect,it,vi} from "vitest";
import {DosingCurvePanel} from "../components/dosing-curve-panel";
import {parseSpeedModel,type DosingModel} from "../lib/models";

let root:Root,container:HTMLDivElement;
beforeEach(()=>{vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);container=document.createElement("div");document.body.append(container);root=createRoot(container);});
afterEach(async()=>{await act(async()=>root.unmount());container.remove();vi.unstubAllGlobals();});
const dose:DosingModel={model_version:"dosing-index-test-30-70-v1",status:"AVAILABLE",weights:{index:.3,tests:.7},
  weight_basis:"COACH_POLICY_NOT_VALIDATED_RELIABILITY",is_maximal_test:false,use:"METHOD_DOSING",hr_model:null,speed_zones:null,
  points:[{duration_s:60,speed_kmh:20.8,index_speed_kmh:18,test_speed_kmh:22,distance_m:20.8*60/3.6,estimated_hr_bpm:null},
    {duration_s:1200,speed_kmh:15.4,index_speed_kmh:14,test_speed_kmh:16,distance_m:15.4*1200/3.6,estimated_hr_bpm:180}],
  zone_comparison:[{zone:"Z4",hr_bpm:180,duration_s:1200,index_speed_kmh:14,test_speed_kmh:16,speed_kmh:15.4}]};

it("shows all three curves and makes the shared dosing value explicit",async()=>{
  await act(async()=>root.render(<DosingCurvePanel model={dose}/>));
  expect(container.querySelectorAll('polyline')).toHaveLength(3);
  expect(container.textContent).toContain("Обща скорост 15,4 км/ч");
  expect(container.textContent).toContain("30% от скоростта по индексната крива и 70%");
  expect(container.textContent).toContain("избрано експертно правило");
  expect(container.textContent).toContain("Z5");
  expect(container.textContent).toContain("над 15,4");
  const toggle=Array.from(container.querySelectorAll('button')).find(b=>b.textContent?.includes('От индекса'))!;
  await act(async()=>toggle.click());
  expect(toggle.getAttribute('aria-pressed')).toBe('false');
  expect(container.querySelector('[data-curve="index_speed_kmh"]')).toBeNull();
  expect(container.querySelector('[data-curve="test_speed_kmh"]')).not.toBeNull();
  expect(container.querySelector('[data-curve="speed_kmh"]')).not.toBeNull();
});

it("keeps the original points and refuses malformed mixtures at the boundary",()=>{
  const original=[{duration_s:60,speed_kmh:22,distance_m:2200/6,estimated_hr_bpm:null}];
  const model={schema_version:"speed-model-v1",points:original,tests:[],dosing_model:dose};
  expect(parseSpeedModel(model).points).toBe(original);
  const wrong=structuredClone(model);wrong.dosing_model.points[0].speed_kmh=21;
  expect(()=>parseSpeedModel(wrong)).toThrow(/обща крива/);
  const reversed=structuredClone(model);reversed.dosing_model.points.reverse();
  expect(()=>parseSpeedModel(reversed)).toThrow(/обща крива/);
});

it("explains missing support without inventing a third curve",async()=>{
  await act(async()=>root.render(<DosingCurvePanel model={{...dose,status:"UNAVAILABLE",points:[]}}/>));
  expect(container.textContent).toContain("едновременно съпоставим индекс и активен максимален тест");
  expect(container.querySelector('svg')).toBeNull();
});
