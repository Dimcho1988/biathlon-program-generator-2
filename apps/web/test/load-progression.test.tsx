// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import { PlanningRangeCalendar } from "../components/planning-range-calendar";
import { LoadProgressionSummary } from "../components/load-progression-summary";
import { TrainingReturnEditor } from "../components/training-return-editor";
import { defaultManagementProfile, defaultPlanningControls, parseManagementProfile } from "../lib/training-management";

afterEach(()=>vi.unstubAllGlobals());
it("migrates the old growth ceiling and validates a dated, complete return declaration",()=>{
  const p={...defaultManagementProfile("2026-09-23"),discipline:"1500 m",planning_controls:defaultPlanningControls("Run")};
  const r={mode:"CONFIRMED_BREAK",pause_start:"2026-06-01",resume_date:"2026-09-23",historical_weekly_q:{Z1:600,Z2:180,Z3:75,Z4:30,Z5:0}};
  const parsed=parseManagementProfile({...p,training_return:r,load_progression:{...p.load_progression,upper_volume_annual_percent:10,ceiling_ratio:1.3}});
  expect(parsed.training_return).toEqual(r);
  expect(parsed.load_progression).toMatchObject({upper_volume_annual_percent:0,ceiling_ratio:1});
  for (const patch of [{resume_date:r.pause_start},{pause_start:null},{historical_weekly_q:{Z1:600}},{historical_weekly_q:{...r.historical_weekly_q,Z5:NaN}}])
    expect(()=>parseManagementProfile({...p,training_return:{...r,...patch}})).toThrow();
  expect(()=>parseManagementProfile({...p,training_return:r,load_progression:{...p.load_progression,enabled:false}})).toThrow();
});
it("distinguishes a confirmed break from missing records and explains the return rule",()=>{
  const p={...defaultManagementProfile("2026-09-23"),training_return:{mode:"CONFIRMED_BREAK" as const,pause_start:"2026-06-01",resume_date:"2026-09-23",historical_weekly_q:{Z1:600,Z2:180,Z3:75,Z4:30,Z5:0}}};
  const html=renderToStaticMarkup(<TrainingReturnEditor profile={p} onChange={()=>{}} today="2026-09-23"/>);
  expect(html).toContain("1,3 ×"); expect(html).toContain("50%"); expect(html).toContain("90%");expect(html).toContain('value="10:00:00"');
  const continued=renderToStaticMarkup(<TrainingReturnEditor profile={{...p,training_return:{...p.training_return,mode:"CONTINUED"}}} onChange={()=>{}} today="2026-09-23"/>);
  expect(continued).toContain("Липсващите записи не се броят за почивка");expect(continued).not.toContain("Началната база");
});
it("shows separate history, initial dose and long-break target",()=>{
  const html=renderToStaticMarkup(<LoadProgressionSummary plan={{long_term:{progression:{basis:"STABLE_PREPARATION_REFERENCE",training_return:{status:"RETURNING",pause_start:"2026-06-01",resume_date:"2026-09-23",pause_days:114,nominal_days:78,weeks:12,target_fraction:.9,components:{Z1:{historical_q:600,initial_q:300,target_q:540,completed_weeks:0,source:"DECLARED_HISTORY"}}}}}}}/>);
  for(const text of ["10:00:00","5:00:00","9:00:00","заявена","12 седмици","Следващото стъпало","Обичайният годишен прираст започва след връщането"]) expect(html).toContain(text);
});
it("validates the annual curve and rejects a dose ceiling above eighty percent",()=>{
  const p={...defaultManagementProfile("2026-09-23"),discipline:"1500 m"};
  expect(parseManagementProfile(p).load_progression?.low_volume_annual_percent).toBe(30);
  expect(()=>parseManagementProfile({...p,load_progression:{...p.load_progression,max_dose_fraction:.81}})).toThrow();
  expect(()=>parseManagementProfile({...p,load_progression:{...p.load_progression,upper_volume_annual_percent:31}})).toThrow();
  expect(()=>parseManagementProfile({...p,load_progression:{...p.load_progression,competition_factor:.5}})).toThrow();
});
it("selects a range across months without creating an event until requested",async()=>{
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);
  const div=document.createElement("div"),root=createRoot(div),selected=vi.fn();
  const click=async(label:string)=>{const b=div.querySelector<HTMLButtonElement>(`[aria-label="${label}"]`)!;await act(async()=>b.click());};
  try {
    await act(async()=>root.render(<PlanningRangeCalendar today="2026-09-23" events={[]} onSelect={selected}/>));
    await click("2026-09-29");await click("Следващ месец");await click("2026-10-02");
    expect(selected).not.toHaveBeenCalled();
    const add=[...div.querySelectorAll("button")].find(b=>b.textContent==="Добави събитие за периода")!;
    await act(async()=>add.click());
    expect(selected).toHaveBeenCalledWith("2026-09-29","2026-10-02");
  } finally {await act(async()=>root.unmount());}
});
it("keeps absent observed growth unknown and hides effective load",()=>{
  const html=renderToStaticMarkup(<LoadProgressionSummary plan={{long_term:{progression:{basis:"COMPLETED_CYCLE",components:{Z3:{weekly_q:90,annual_rate_percent:15,observed_cycle_growth_percent:{weekly_q:null,weekly_effective:null}}}}}}}/>);
  expect(html).toContain("завършен мезоцикъл");expect(html).toContain("Измерена промяна в приравнения обем");
  expect(html).not.toContain("Реална промяна E");expect(html).toContain("—");
});
it("shows the operative reference and actual period growth without a competing historical trajectory",()=>{
  const html=renderToStaticMarkup(<LoadProgressionSummary plan={{long_term:{progression:{basis:"STABLE_PREPARATION_REFERENCE",anchor:{created_on:"2026-09-25",windows:[]},target_date:"2027-01-01",components:{Z5:{weekly_q:2,expert_reference_q:25,expert_q_bounds:[5,30],reference_q:5,target_q:5.2,attainable_q:2.08,annual_rate_percent:14,governed_annual_rate_percent:14.7,planned_growth_percent:4,limitation:"BELOW_REFERENCE_BOUND"}}}}}}/>);
  expect(html).toContain("0:02:00");expect(html).toContain("0:05:00");expect(html).not.toContain("0:25:00");
  expect(html).toContain("долната граница");expect(html).toContain("5% за удар над долната");
  expect(html).toContain("2027-01-01");expect(html).toContain("не е предписание за намаляване");
  expect(html).toContain("14,7");expect(html).toContain("0:05:12");expect(html).not.toContain("0:02:05");
  expect(html).toContain("Планиран прираст до датата");expect(html).not.toContain("Измерена база Q");
});
it("validates reference positions and preserves zero as an explicit choice",()=>{
  const p={...defaultManagementProfile("2026-09-25"),discipline:"1500 m"};
  const settings={...p.load_progression,training_level:"HIGH",component_reference_positions:{Z5:0},reference_revision:2};
  expect(parseManagementProfile({...p,load_progression:settings}).load_progression?.component_reference_positions?.Z5).toBe(0);
  expect(()=>parseManagementProfile({...p,load_progression:{...settings,component_reference_positions:{Z5:1.01}}})).toThrow();
});
it("distinguishes lower planning reference from observed history and a mandatory dose",()=>{
  const html=renderToStaticMarkup(<LoadProgressionSummary plan={{long_term:{progression:{basis:"STABLE_PREPARATION_REFERENCE",normative_role:"LOWER_PLANNING_REFERENCE_NOT_POTENTIAL_CEILING",anchor:{created_on:"2026-09-28",windows:[]},components:{Z5:{weekly_q:110/60,reference_q:5,expert_q_bounds:[5,30],limitation:"BELOW_REFERENCE_BOUND"},Z3:{weekly_q:210,reference_q:210,expert_q_bounds:[30,120]}}}}}}/>);
  expect(html).toContain("0:01:50");expect(html).toContain("0:05:00");expect(html).toContain("3:30:00");
  expect(html).toContain("Измерената история не се променя");
  expect(html).toContain("над горния ориентир");expect(html).toContain("7/40, Recovery");
  expect(html).toContain("Разтоварването, тейпърът и непълният микроцикъл");
  expect(html).not.toContain("Архивна версия");
});

it("explains recorded speed history and keeps normative bounds out of the starting target",()=>{
  const html=renderToStaticMarkup(<LoadProgressionSummary plan={{long_term:{progression:{version:"load-progression-v12-recorded-indicator-reference",basis:"STABLE_PREPARATION_REFERENCE",normative_role:"INITIAL_PRIOR_NOT_POTENTIAL_CEILING",history_usable:true,estimated_history_supported:true,components:{Z1:{weekly_q:42,reference_q:42,expert_q_bounds:[240,900]}}}}}}/>);
  expect(html).toContain("валиден пулс или скорост");
  expect(html).toContain("включително под експертните ориентири");
  expect(html).toContain("не увеличават историческата основа");
  expect(html).not.toContain("5% за удар над долната");
});
