import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { MesocyclePriorities } from "../components/mesocycle-priorities";
import { MicrocycleVolumes } from "../components/microcycle-volumes";

const cycle = { kind: "BUILD", component_indices: { Z4:1.6,Z3:1.5,STR:1.2 },
  component_roles: { Z4:"PRIMARY",Z3:"SECONDARY",STR:"LIGHT_DEVELOPMENT" }, race_component:"Z4" };

it("shows the third component as light development at 1.2 and keeps Q distinct", () => {
  const text = renderToStaticMarkup(<MesocyclePriorities cycle={cycle}/>);
  expect(text).toContain("Леко развитие");
  expect(text).toContain("1,2");
  expect(text).toContain("Приравненият обем Q");
  expect(text).toContain("Състезателна зона");
});

it("does not present loading targets as recovery targets", () => {
  const text = renderToStaticMarkup(<MesocyclePriorities cycle={{...cycle,kind:"RECOVERY",accents:["Z2"]}}/>);
  expect(text).toContain("Допълващо поддържане");
  expect(text).toContain("Z2");
  expect(text).not.toContain("1,6");
  expect(text).not.toContain("Леко развитие");
});

it("marks shock weeks inside the single Q table", () => {
  const text = renderToStaticMarkup(<MicrocycleVolumes weeks={[{start_date:"2026-09-25",end_date:"2026-10-01",accents:["Z4","Z3","STR"],cycle:{...cycle,kind:"STRESS"},volume_budget_minutes:null,components:{Z4:{target_period_q:20}}}]} status="Предложение"/>);
  expect(text).toContain("Ударен · Z4, Z3, STR");
  expect(text).toContain("0:20:00");
  expect(text.match(/<table>/g)).toHaveLength(1);
});
