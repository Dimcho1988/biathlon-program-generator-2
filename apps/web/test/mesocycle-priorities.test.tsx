import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { MesocyclePriorities } from "../components/mesocycle-priorities";
import { MicrocycleVolumes } from "../components/microcycle-volumes";

const cycle = { kind: "BUILD", component_indices: { Z4:1.6,Z3:1.5,STR:1.2 },
  component_roles: { Z4:"PRIMARY",Z3:"SECONDARY",STR:"LIGHT_DEVELOPMENT" }, race_component:"Z4" };

it("shows light development at 1.2 using coach-readable volume terminology", () => {
  const text = renderToStaticMarkup(<MesocyclePriorities cycle={cycle}/>);
  expect(text).toContain("Леко развитие");
  expect(text).toContain("1,2");
  expect(text).toContain("Приравненият обем и дневната готовност");
  expect(text).not.toContain("обем Q");
  expect(text).toContain("Състезателна зона");
});

it("does not present loading targets as recovery targets", () => {
  const text = renderToStaticMarkup(<MesocyclePriorities cycle={{...cycle,kind:"RECOVERY",accents:["Z2"], focus_stage:"GENERAL_COVERAGE", focus_coverage_missing:["Z5"], phase_mesocycle_number:2, phase_mesocycle_count:2}}/>);
  expect(text).toContain("Допълващо поддържане");
  expect(text).toContain("Z2");
  expect(text).not.toContain("1,6");
  expect(text).not.toContain("Леко развитие");
  expect(text).toContain("Поддържането не се брои за развиващ акцент");
  expect(text).not.toContain("няма планирана развиваща роля");
  expect(text).not.toContain("Мезоцикъл 2");
});

it("explains late special race-band priorities without promising extra active roles", () => {
  const text = renderToStaticMarkup(<MesocyclePriorities cycle={{...cycle,
    component_indices:{Z4:1.6}, component_roles:{Z4:"PRIMARY"},
    focus_stage:"SPECIAL_RACE_BAND", race_band:["Z4","Z3","Z5"],
    phase_mesocycle_number:2, phase_mesocycle_count:2,
  }}/>);
  expect(text).toContain("Край на специалната подготовка");
  expect(text).toContain("Мезоцикъл 2 от 2");
  expect(text).toContain("Z4, Z3, Z5");
  expect(text.match(/<li>/g)).toHaveLength(1);
  expect(text).not.toContain("<table");
});

it("keeps missing automatic general roles separate from manual and completed training", () => {
  const text = renderToStaticMarkup(<MesocyclePriorities cycle={{...cycle,
    focus_stage:"GENERAL_COVERAGE", background_development:true, focus_coverage_missing:["Z2","Z5","STR"],
  }}/>);
  expect(text).toContain("няма планирана развиваща роля за");
  expect(text).toContain("Z2, Z5, Сила");
  expect(text).toContain("не отчита ръчно зададените акценти или реално изпълнените тренировки");
  expect(text).toContain("всяка аеробна зона следва своя годишен темп");
  expect(text).toContain("по-малка седмична цел");
  expect(text).not.toContain("по-малък дял от прираста");
});

it("does not present a fallback as a known competition zone or race band", () => {
  const text = renderToStaticMarkup(<MesocyclePriorities cycle={{...cycle,
    race_component:null, focus_stage:"PRECOMPETITION_RACE_BAND", race_band:["Z4","Z3","Z5"],
  }}/>);
  expect(text).toContain("Няма надеждно зададена състезателна продължителност");
  expect(text).toContain("временна схема");
  expect(text).not.toContain("Състезателна зона:");
  expect(text).not.toContain("Z4, Z3, Z5");
  expect(text).not.toContain("Предсъстезателна подготовка:");
});

it("explains edge-zone priorities while preserving taper limits", () => {
  const text = renderToStaticMarkup(<MesocyclePriorities cycle={{...cycle,
    race_component:"Z5", focus_stage:"PRECOMPETITION_RACE_BAND", race_band:["Z5","Z4","Z3"],
    component_indices:{Z5:1.6,Z4:1.5,Z3:1.2}, component_roles:{Z5:"PRIMARY",Z4:"SECONDARY",Z3:"LIGHT_DEVELOPMENT"},
  }}/>);
  expect(text).toContain("Предсъстезателна подготовка");
  expect(text).toContain("Z5, Z4, Z3");
  expect(text).toContain("Тейпърът запазва отделните си ограничения");
  expect(text.match(/<li>/g)).toHaveLength(3);
});

it("marks shock weeks inside the single Q table", () => {
  const text = renderToStaticMarkup(<MicrocycleVolumes weeks={[{start_date:"2026-09-25",end_date:"2026-10-01",accents:["Z4","Z3","STR"],cycle:{...cycle,kind:"STRESS"},volume_budget_minutes:null,components:{Z4:{target_period_q:20}}}]} status="Предложение"/>);
  expect(text).toContain("Ударен · Z4, Z3, STR");
  expect(text).toContain("0:20:00");
  expect(text.match(/<table>/g)).toHaveLength(1);
});
