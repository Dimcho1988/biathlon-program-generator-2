import {describe,expect,it} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {HrSpeedZones} from "../components/hr-speed-zones";
import type {HrModel} from "../lib/models";

const base:HrModel={model_version:"fixture",hr_range_bpm:[120,180],speed_range_kmh:[10,25],
  curve_duration_range_s:[10.8,43516],curve_speed_range_kmh:[12,35],zones:[{
    zone:"Z3",hr_bpm:160,index:4.5,count:8,candidate_speed_kmh:19.75,candidate_duration_s:6000,
    candidate_reason:"DURATION_ABOVE_MAX",duration_min_s:1800,duration_max_s:4800,
    duration_s:3300,speed_kmh:21.42,source:"EXPERT_MIDPOINT",reason:"INDEX_OUTSIDE_DURATION_BOUNDS"}]};
describe("HR-speed diagnostics",()=>{
  it("shows the rejected estimate separately from the applied result with explicit units",()=>{
    const html=renderToStaticMarkup(<HrSpeedZones model={base}/>);
    for(const text of ["19,75 км/ч","1 ч 40 мин","55 мин","21,42 км/ч","Времето от ТИ е над 1 ч 20 мин.","8 тренировки"])
      expect(html).toContain(text);
  });
  it("does not invent a duration for speeds outside the curve or disguise them as missing TI",()=>{
    const html=renderToStaticMarkup(<HrSpeedZones model={{...base,zones:[{...base.zones[0],candidate_duration_s:null,candidate_reason:"SPEED_BELOW_CURVE"}]}}/>);
    expect(html).toContain("под минималната скорост на кривата");
    expect(html).not.toContain("Няма валиден зонален ТИ");
    expect(html).not.toContain("1 ч 40 мин");
  });
  it("preserves an in-range candidate while explaining global conflict fallback",()=>{
    const html=renderToStaticMarkup(<HrSpeedZones model={{...base,conflicting_zones:[["Z1","Z2"]],zones:[{...base.zones[0],candidate_duration_s:2700,candidate_reason:"ACCEPTED",reason:"CONFLICTING_ZONE_ANCHORS"}]}}/>);
    expect(html).toContain("Конфликт между зоните Z1–Z2");
    expect(html).toContain("45 мин");expect(html).toContain("55 мин");
    expect(html).toContain("В допустимия диапазон.");
    expect(html).toContain("Обща замяна заради конфликт");
  });
});
