// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import { PlanningRangeCalendar } from "../components/planning-range-calendar";
import { LoadProgressionSummary } from "../components/load-progression-summary";
import { defaultManagementProfile, parseManagementProfile } from "../lib/training-management";

afterEach(()=>vi.unstubAllGlobals());
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
it("keeps absent observed growth unknown and labels Q and E separately",()=>{
  const html=renderToStaticMarkup(<LoadProgressionSummary plan={{long_term:{progression:{basis:"COMPLETED_CYCLE",components:{Z3:{weekly_q:90,annual_rate_percent:15,observed_cycle_growth_percent:{weekly_q:null,weekly_effective:null}}}}}}}/>);
  expect(html).toContain("завършен мезоцикъл");expect(html).toContain("Реална промяна Q");
  expect(html).toContain("Реална промяна E");expect(html).toContain("—");
});
