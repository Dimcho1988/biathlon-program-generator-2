import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { SessionMethodGuidance } from "../components/session-method-guidance";
import { TrainingPlanSummary } from "../components/training-plan-summary";
import { planDates } from "../lib/plan-week-view";
import { defaultManagementProfile, defaultPlanningControls, parseManagementProfile, type DraftSession, type PlanningDraft } from "../lib/training-management";

const session = {
  method_id: "coach-threshold", title: "Прагови повторения", sport: "Run", zone: "Z3", blocks: [],
  dose_evidence: { effort_anchor: { type: "HR_ZONE_POSITION", zone: "Z3", target_hr_bpm: 170 } },
  q_accounting: { basis: "PLANNED_ESTIMATE_NOT_MEASURED_LOAD", unknown_metabolic_minutes: 0, rows: [
    { kind: "WORK", zone: "Z3", duration_min: 3, coefficient: .8, equivalent_min: 2.4, source: "SPEED" },
    { kind: "WORK", zone: "Z3", duration_min: 3, coefficient: .8, equivalent_min: 2.4, source: "SPEED" },
    { kind: "RECOVERY", zone: "Z1", duration_min: 2, coefficient: .5, equivalent_min: 1, source: "HEART_RATE" },
  ] },
} as unknown as DraftSession;

it("shows the frozen native anchor without exposing Q calculation diagnostics or changing their payload", () => {
  const saved = structuredClone(session);
  const html = renderToStaticMarkup(<SessionMethodGuidance session={session}/>);
  expect(html).toContain("Работна опора:");
  expect(html).toContain("170");
  expect(html).not.toContain("Как е изчислен Q?");
  expect(html).not.toContain("Прогнозен Q");
  expect(html).not.toContain("PLANNED_ESTIMATE_NOT_MEASURED_LOAD");
  expect(session).toEqual(saved);
});

it("retains every combined parent anchor and never renames native work as MAS", () => {
  const combined = structuredClone(session);
  combined.dose_evidence.effort_anchor = { type: "COMBINED", parts: [
    { zone: "Z3", method_id: "threshold", anchor: { type: "HR_ZONE_POSITION", zone: "Z3", target_hr_bpm: 170 } },
    { zone: "Z4", method_id: "source", anchor: { type: "V5", duration_seconds: 300, speed_kmh: 20 } },
  ] };
  const html = renderToStaticMarkup(<SessionMethodGuidance session={combined}/>);
  expect(html).toContain("Индивидуална зонова опора");
  expect(html).toContain("V(5 мин)");
  expect(html).not.toContain("MAS");
});

it("renders the same complete frozen guidance when repeated parent cards use local references", () => {
  const parent = { id: "parent", title: "Parent method", guidance: { execution: "Complete frozen instructions", recovery: "Complete frozen recovery" } };
  const raw = { ...structuredClone(session), method_library: { id: "COMBO-LOCAL", components: [parent] },
    blocks: [{ parent_method_card: parent }, { parent_method_card: parent }] } as unknown as DraftSession;
  const compact = structuredClone(raw);
  compact.blocks = compact.blocks.map(block => {
    const rest = { ...block };
    delete rest.parent_method_card;
    return { ...rest, parent_method_card_ref: "method_library.components[0]" };
  });
  expect(renderToStaticMarkup(<SessionMethodGuidance session={compact}/>)).toBe(renderToStaticMarkup(<SessionMethodGuidance session={raw}/>));
});

it("does not clamp an overshoot to 100% or transfer it to an underfilled component", () => {
  const plan = { start_date: "2026-10-26", end_date: "2026-11-01",
    days: planDates("2026-10-26", "2026-11-01").map(date => ({date, status: "REST", sessions: []})),
    parameters: {}, summary: {}, allocation: {
    components: {
      Z3: { basis: "DIRECT_Q", target: 100, actual: 0, planned: 120, remaining: 0, coverage_status: "ABOVE_RANGE", within_tolerance: false },
      Z4: { basis: "DIRECT_Q", target: 100, actual: 0, planned: 80, remaining: 20, coverage_status: "BELOW_RANGE", within_tolerance: false },
    }, constraints: [], dose_limits: [], acceptance: { lower_percent: 90, upper_percent: 110, target_percent: 100, within_tolerance: false },
  } } as unknown as PlanningDraft;
  plan.days[0].status = "PLANNED";
  plan.days[0].sessions = [{direct_equivalent_minutes: {Z3: 120, Z4: 80}}] as unknown as DraftSession[];
  const html = renderToStaticMarkup(<TrainingPlanSummary plan={plan}/>);
  expect(html).toContain("120.0% от целта");
  expect(html).toContain("80.0% от целта");
  expect(html).toContain("Целим 100%, с допустими 90–110% за всеки компонент");
  expect(html).not.toContain("ABOVE_RANGE");
  expect(html).not.toContain("BELOW_RANGE");
  plan.days[0].sessions[0].direct_equivalent_minutes.Z3 = 95;
  plan.days[0].sessions[0].direct_equivalent_minutes.Z4 = 95;
  const accepted = renderToStaticMarkup(<TrainingPlanSummary plan={plan}/>);
  expect(accepted.match(/95\.0% от целта/g)).toHaveLength(2);
  expect(accepted).not.toContain("120.0% от целта");
  expect(accepted).not.toContain("80.0% от целта");
});

it("accepts automatic strength frequency while retaining saved explicit limits", () => {
  const profile = defaultManagementProfile("2026-10-26");
  profile.discipline = "5000 m";
  profile.planning_controls = defaultPlanningControls("Run");
  expect(profile.planning_controls.max_strength_sessions).toBeNull();
  expect(parseManagementProfile(profile).planning_controls?.max_strength_sessions).toBeNull();
  profile.planning_controls.max_strength_sessions = 2;
  expect(parseManagementProfile(profile).planning_controls?.max_strength_sessions).toBe(2);
  profile.planning_controls.max_strength_sessions = 4;
  expect(parseManagementProfile(profile).planning_controls?.max_strength_sessions).toBe(4);
});
