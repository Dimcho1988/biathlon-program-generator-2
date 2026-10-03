// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { TrainingPlanOverview } from "../components/training-plan-overview";
import { LoadMeasureExplanation } from "../components/load-measure-explanation";
import { COMPONENTS, type PlanProjection } from "../lib/training-management";

it("compares the same complete historical period without converting future volume into clock time",()=>{
  const range={start_date:"2026-09-05",end_date:"2026-09-11"};
  const plan: PlanProjection={history_comparison:[{...range,actual_minutes:1140}],component_history:[{...range,complete:true,components:Object.fromEntries(COMPONENTS.map(z=>[z,{weekly_q:z==="Z1"?500:50}]))}]};
  const html=renderToStaticMarkup(<LoadMeasureExplanation plan={plan}/>);
  expect(html).toContain("19:00:00 продължителност");expect(html).toContain("12:30:00 приравнен обем");
  expect(html).toContain("няма постоянен коефициент");
  const incomplete={...plan,component_history:[{...range,complete:false,components:{Z1:{weekly_q:750}}}]};
  expect(renderToStaticMarkup(<LoadMeasureExplanation plan={incomplete}/>)).not.toContain("Пример от твоята история");
});

it("switches the chart and single microcycle table together to dimensionless 7/40 goals",async()=>{
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);
  const box=document.createElement("div"),root=createRoot(box);
  const plan:PlanProjection={long_term:{schema_version:"training-outlook-v1",weeks:[{
    start_date:"2026-09-26",end_date:"2026-09-28",days:3,accents:["Z3"],phases:["GENERAL_PREPARATION"],
    components:{Z3:{target_period_q:75,target_index_7_40:1.65},Z5:{target_period_q:null,target_index_7_40:null}},
  }]}};
  try {
    await act(async()=>root.render(<TrainingPlanOverview plan={plan} outcomes={[]} today="2026-09-26"/>));
    expect(box.querySelector('svg')?.getAttribute('aria-label')).toBe("Приравнен обем по микроцикли");
    expect(box.textContent).toContain("1:15:00");
    const button=[...box.querySelectorAll("button")].find(b=>b.textContent==="Цели 7/40")!;
    await act(async()=>button.click());
    expect(box.querySelector('svg')?.getAttribute('aria-label')).toBe("Цели 7/40 по микроцикли");
    const table=box.querySelector('section[aria-label="Обем по микроцикли"]')!;
    expect(table.querySelectorAll('table')).toHaveLength(1);
    expect(table.textContent).toContain("1,65");expect(table.textContent).toContain("—");
    expect(table.textContent).not.toContain("1:15:00");
    expect(table.textContent).not.toContain("Общо приравнен обем");
    expect(table.textContent).not.toContain("Обща продължителност");
    expect(box.textContent).toContain("не означава 20%");
    await act(async()=>[...box.querySelectorAll("button")].find(b=>b.textContent==="Приравнен обем")!.click());
    expect(table.textContent).toContain("1:15:00");
  } finally { await act(async()=>root.unmount());vi.unstubAllGlobals(); }
});
