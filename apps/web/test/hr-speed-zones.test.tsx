import {describe,expect,it} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {HrSpeedZones} from "../components/hr-speed-zones";
import type {HrModel,SpeedModel} from "../lib/models";

const base:HrModel={model_version:"fixture",hr_range_bpm:[120,180],speed_range_kmh:[10,25],
  curve_duration_range_s:[10.8,43516],curve_speed_range_kmh:[12,35],zones:[{
    zone:"Z3",hr_bpm:160,index:4.5,count:8,candidate_speed_kmh:19.75,candidate_duration_s:6000,
    candidate_reason:"DURATION_ABOVE_MAX",duration_min_s:2700,duration_max_s:3600,
    duration_s:3150,speed_kmh:21.42,source:"EXPERT_MIDPOINT",reason:"INDEX_OUTSIDE_DURATION_BOUNDS"}]};
describe("HR-speed diagnostics",()=>{
  it("distinguishes the observation window from current paired support",()=>{
    const freshness:SpeedModel["index_freshness"]={version:"speed-anchor-freshness-v1-per-band-14d",as_of:"2026-10-29",max_age_days:14,
      excluded_bands:["Z3"],last_observation_date:"2026-10-01",last_current_observation_date:null,bands:{
        Z3:{status:"STALE",last_activity_date:"2026-10-01",age_days:28,observed_index:5,observed_count:1,observed_seconds:600}}};
    const props={model:base,indexFreshness:freshness,indexWindow:{start:"2026-09-20",end:"2026-10-29",days:40,last_activity_date:null}};
    const fallback=renderToStaticMarkup(<HrSpeedZones {...props}/>);
    expect(fallback).toContain("Няма актуален подходящ индекс");
    expect(fallback).toContain("през последните 14 дни");
    expect(fallback).not.toContain("използват се експертните ориентири");
    expect(renderToStaticMarkup(<HrSpeedZones {...props} indexWindow={{...props.indexWindow,last_activity_date:"2026-10-28"}}/>))
      .toContain("Последна тренировка с използван индекс: 28.10.2026.");
  });
  it("labels test-curve fallback as a modeled HR relation",()=>{
    const html=renderToStaticMarkup(<HrSpeedZones model={{...base,zones:[{...base.zones[0],source:"TEST_CURVE_CONFLICT_FALLBACK",candidate_reason:"BOUNDARY_PRESERVED"}]}}/>);
    expect(html).toContain("Моделна връзка по тестовата крива");
    expect(html).not.toContain("Среда на експертния диапазон");
  });
  it("labels automatic HRmax zones and links to their settings without relabelling manual zones",()=>{
    const automatic=renderToStaticMarkup(<HrSpeedZones model={base} zoneSource="AUTOMATIC_HRMAX"/>);
    expect(automatic).toContain("Пулсовите граници са автоматични по максималния пулс");
    expect(automatic).toContain('href="/?settings=edit"');
    expect(renderToStaticMarkup(<HrSpeedZones model={base}/>)).not.toContain("Пулсовите граници са автоматични");
  });
  it("shows the rejected estimate separately from the applied result with explicit units",()=>{
    const html=renderToStaticMarkup(<HrSpeedZones model={base}/>);
    for(const text of ["19,75 км/ч","1:40:00","0:52:30","21,42 км/ч","Времето от ТИ е над 1:00:00.","8 тренировки"])
      expect(html).toContain(text);
  });
  it("does not invent a duration for speeds outside the curve or disguise them as missing TI",()=>{
    const html=renderToStaticMarkup(<HrSpeedZones model={{...base,zones:[{...base.zones[0],candidate_duration_s:null,candidate_reason:"SPEED_BELOW_CURVE"}]}}/>);
    expect(html).toContain("под минималната скорост на кривата");
    expect(html).not.toContain("Няма валиден зонален ТИ");
    expect(html).not.toContain("1:40:00");
  });
  it("preserves an in-range candidate while explaining global conflict fallback",()=>{
    const html=renderToStaticMarkup(<HrSpeedZones model={{...base,conflicting_zones:[["Z1","Z2"]],zones:[{...base.zones[0],candidate_duration_s:2700,candidate_reason:"ACCEPTED",reason:"CONFLICTING_ZONE_ANCHORS"}]}}/>);
    expect(html).toContain("Конфликт между зоните Z1–Z2");
    expect(html).toContain("0:45:00");expect(html).toContain("0:52:30");
    expect(html).toContain("В допустимия диапазон.");
    expect(html).toContain("Обща замяна заради конфликт");
  });
});
