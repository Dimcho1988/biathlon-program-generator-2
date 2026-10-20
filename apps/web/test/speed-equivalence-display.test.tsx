// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import { RunningSpeedUnitControl, PreferredSpeed, setRunningSpeedUnit } from "../components/running-speed-unit";
import { ActivityTimelineChart } from "../components/activity-timeline-chart";
import type { ActivitySeriesPoint } from "../lib/activities";
import { BlockExecutionRange } from "../components/block-execution-range";
import { SpeedEquivalenceComparison } from "../components/speed-equivalence-comparison";
import { MethodCard } from "../components/training-method-library";
import { speedRangeText, speedText, RUNNING_SPEED_UNIT_KEY } from "../lib/speed-format";
import { hydrateSessionSpeedEquivalence, isSpeedEquivalenceComparison, isPlanSpeedComparison, type PlanSpeedComparison as PlanComparison, type SpeedEquivalenceComparison as Comparison } from "../lib/speed-equivalence";
import { PlanSpeedComparison } from "../components/plan-speed-comparison";
import { AnTReferenceEditor } from "../components/ant-reference-editor";
import { methodAnchorLabel, methodHrText, type LibraryMethod } from "../lib/method-library";
import type { SessionBlock } from "../lib/training-management";
import { defaultManagementProfile, parseManagementProfile, parseDraftRecord, parseActivePlanResponse } from "../lib/training-management";

afterEach(() => { setRunningSpeedUnit("pace"); vi.unstubAllGlobals(); });
const comparison: Comparison = {
  version: "speed-equivalence-local-linear-v1", role: "COMPARISON_ONLY", parameters_version: "p1",
  settings: { version: "expert-2026-10-20", origin: "EXPERT_DEFINED", coefficients: { Z1: 1, Z2: 1, Z3: 1, Z4: 1.2, Z5: 1.5 } },
  reference_policy: "CURRENT_INDIVIDUAL_CURVE_RETROSPECTIVE_COMPARISON", recorded_minutes: 30, classified_minutes: 30, coverage_percent: 100,
  zones: Array.from({ length: 5 }, (_, i) => ({ zone: `Z${i + 1}`, status: i === 4 ? "UNAVAILABLE" : "AVAILABLE", reason: i === 4 ? "V10_UNAVAILABLE" : null,
    real_minutes: i === 3 ? 30 : 0, equivalent_minutes: i === 4 ? null : i === 3 ? 26.25 : 0, baseline_equivalent_minutes: i === 4 ? null : i === 3 ? 28.5 : 0,
    normalized_q: i === 4 ? null : i === 3 ? 1.3125 : 0, baseline_normalized_q: i === 4 ? null : i === 3 ? 1.425 : 0,
    tref_minutes: 20, reference_speed_kmh: i === 4 ? null : 20, low_kmh: 18, high_kmh: i === 4 ? null : 20,
    upper_weight: i === 4 ? null : 4 / 3, slope_per_kmh: i === 4 ? null : 1 / 6 })), warnings: [],
};

it("uses reciprocal pace bounds and never applies a percentage to pace", () => {
  expect(speedRangeText([3600 / 210, 18])).toBe("3:20–3:30 мин/км");
  expect(speedText(18 * .95)).toBe("3:31 мин/км");
  expect(speedRangeText([18, 20], "kmh")).toBe("18–20 км/ч");
  expect(speedRangeText([18, 20], "pace", "NordicSki")).toBe("18–20 км/ч");
  for (const range of [[0, 20], [20, 18], [NaN, 20]]) expect(speedRangeText(range)).toBe("—");
  expect(methodAnchorLabel({ type: "V5" })).not.toContain("МАС");
  expect(methodAnchorLabel({ type: "MAS" })).toContain("V(6 мин)");
});

it("shares and persists the setting across method, plan and report displays without changing speeds", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  localStorage.setItem(RUNNING_SPEED_UNIT_KEY, "pace");
  const div = document.createElement("div"), root = createRoot(div);
  const block = { target_speed_range_kmh: [18, 20] } as SessionBlock;
  const method: LibraryMethod = { id: "run-test", title: "Метод", sports: ["Run"], periods: [], disciplines: [], source: "Тест", guidance: {},
    resolution: { status: "READY", target_speed_range_kmh: [18, 20] } };
  const original = JSON.stringify({ block, method });
  try {
    await act(async () => root.render(<><RunningSpeedUnitControl/><BlockExecutionRange block={block} sport="Run"/><MethodCard method={method} canEdit={false} selected={false} onChange={() => {}} onReset={() => {}}/><PreferredSpeed speed={20} sport="Run"/></>));
    expect(div.textContent).toContain("3:00–3:20 мин/км");
    await act(async () => { const select = div.querySelector("select")!; select.value = "kmh"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(localStorage.getItem(RUNNING_SPEED_UNIT_KEY)).toBe("kmh");
    expect(div.textContent).toContain("18–20 км/ч");
    expect(div.textContent).not.toContain("3:00–3:20 мин/км");
    await act(async () => root.render(<PreferredSpeed speed={20} sport="Run"/>));
    expect(div.textContent).toBe("20 км/ч");
    expect(JSON.stringify({ block, method })).toBe(original);
  } finally { await act(async () => root.unmount()); }
});

it("distinguishes equivalent time, dimensionless Q, missing values and comparison-only status", () => {
  expect(isSpeedEquivalenceComparison(comparison)).toBe(true);
  const html = renderToStaticMarkup(<SpeedEquivalenceComparison value={comparison}/>);
  for (const text of ["0:26:15", "0:28:30", "1,313", "1,425", "Няма оценка", "не заменя водещия товар", "не е измерен енергоразход", "не се сумират", "V(10 сек)"]) expect(html).toContain(text);
  expect(isSpeedEquivalenceComparison({ ...comparison, coverage_percent: 101 })).toBe(false);
  expect(isSpeedEquivalenceComparison({ ...comparison, role: "AUTHORITATIVE" })).toBe(false);
  expect(isSpeedEquivalenceComparison({ ...comparison, zones: comparison.zones.map(z => ({ ...z, normalized_q: NaN })) })).toBe(false);
});

it("does not convert old HR targets or missing execution to speed progress", () => {
  const plan: PlanComparison = {version: "plan-speed-v1", role: "COMPARISON_ONLY", authoritative_load_changed: false, start_date: "2026-10-20", end_date: "2026-10-26",
    sports: [{sport: "Run", parameters_version: comparison.parameters_version, planned: comparison,
      actual: {status: "INCOMPLETE", comparison: null, activity_count: 2, available_activity_count: 0, missing_comparison_count: 2, different_version_count: 0, reason: "EXECUTION_COMPARISON_RECOMPUTATION_REQUIRED"},
      targets: comparison.zones.map(z => ({zone: z.zone, unit: "EQUIVALENT_MINUTES_AT_ZONE_REFERENCE", reference_speed_kmh: z.reference_speed_kmh,
        target_equivalent_minutes: null, planned_equivalent_minutes: z.equivalent_minutes, actual_equivalent_minutes: null, remaining_equivalent_minutes: null, excess_equivalent_minutes: null,
        baseline_planned_equivalent_minutes: z.baseline_equivalent_minutes, baseline_actual_equivalent_minutes: null, baseline_remaining_equivalent_minutes: null,
        target_attainment_percent: null, baseline_target_attainment_percent: null, reason: "LEGACY_HR_TARGET_NOT_CONVERTED"}))}]};
  expect(isPlanSpeedComparison(plan)).toBe(true);
  const html = renderToStaticMarkup(<PlanSpeedComparison value={plan}/>);
  expect(html).toContain("няма съгласувано преобразуване");
  expect(html).toContain("Съпоставими активности: 0 от 2");
  expect(html).toContain("Няма съпоставима оценка");
  expect(html).toContain("0:26:15");
  expect(isPlanSpeedComparison({...plan, sports: [{...plan.sports[0], parameters_version: "different"}]})).toBe(false);
});

it("preserves explicit AnT measurements and rejects invalid or duplicated references", () => {
  const reference = {sport: "Run" as const, speed_kmh: 18.5, source: "MEASURED" as const, assessed_on: "2026-10-19", notes: "Лабораторен тест"};
  const profile = {...defaultManagementProfile("2026-10-20"), discipline: "5000 m", ant_references: [reference]};
  expect(parseManagementProfile(profile).ant_references).toEqual([reference]);
  expect(parseManagementProfile({...profile, ant_references: undefined}).ant_references).toBeUndefined();
  expect(() => parseManagementProfile({...profile, ant_references: [reference, reference]})).toThrow(/AnT/);
  expect(() => parseManagementProfile({...profile, ant_references: [{...reference, speed_kmh: 0}]})).toThrow(/AnT/);
  const html = renderToStaticMarkup(<AnTReferenceEditor value={[reference]} onChange={() => {}} today="2026-10-20"/>);
  expect(html).toContain("не презаписва тези данни");
  expect(html).toContain('value="18.5"');
  expect(html).toContain("Лабораторен тест");
});

it("hides competing capacity multiplier inputs for source-standard methods", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const method: LibraryMethod = { id: "source-dose", title: "Източников метод", sports: ["Run"], periods: [], disciplines: [], source: "Тест", guidance: {},
    dosing: {basis: "SOURCE_STANDARD_NO_CAPACITY_MULTIPLIER", applies_to_standard_dose: false},
    prescription: {anchor: {type: "V5"}, work: {unit: "seconds", range: [240, 240]}}, resolution: {status: "READY", standard_work_seconds: 1200} };
  const div = document.createElement("div"), root = createRoot(div);
  try {
    await act(async () => root.render(<MethodCard method={method} canEdit selected onChange={() => {}} onReset={() => {}}/>));
    await act(async () => [...div.querySelectorAll("button")].find(b => b.textContent?.startsWith("Настройки за спортиста"))!.click());
    expect(div.textContent).toContain("Избрана стандартна работа: 0:20:00");
    expect(div.textContent).not.toContain("Общ работен капацитет, % от Tmax");
    expect(div.textContent).toContain("Не се добавя множител от Tmax");
  } finally { await act(async () => root.unmount()); }
});

it("loads actual comparisons only on request and rejects a response for another plan revision", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const response: PlanComparison = {version: "plan-speed-v1", role: "COMPARISON_ONLY", authoritative_load_changed: false,
    start_date: "2026-10-20", end_date: "2026-10-26", sports: [], stored_plan_unchanged: true,
    source_plan: {kind: "DRAFT", revision: 4, start_date: "2026-10-20", fingerprint: "test"}, actual_through: "2026-10-20"};
  const fetcher = vi.fn().mockResolvedValue(Response.json(response)); vi.stubGlobal("fetch", fetcher);
  const div = document.createElement("div"), root = createRoot(div);
  try {
    await act(async () => root.render(<PlanSpeedComparison source={{kind: "DRAFT", revision: 4, start_date: "2026-10-20"}}/>));
    expect(fetcher).not.toHaveBeenCalled();
    await act(async () => div.querySelector("button")!.click());
    expect(fetcher.mock.calls[0][0]).toBe("/api/athlete/management/speed-comparison?kind=DRAFT&revision=4&start_date=2026-10-20");
    expect(div.textContent).toContain("Изпълнение до 2026-10-20");
    fetcher.mockResolvedValue(Response.json({...response, source_plan: {...response.source_plan!, revision: 5}}));
    await act(async () => div.querySelector("button")!.click());
    expect(div.querySelector('[role="alert"]')?.textContent).toContain("Версията на програмата е променена");
  } finally { await act(async () => root.unmount()); }
});

it("preserves source-native HR and measured AnT as their own anchors", () => {
  expect(methodAnchorLabel({type:"ANT",source:"MEASURED"})).toContain("измерена опора");
  expect(methodAnchorLabel({type:"ANT",source:"MEASURED"})).not.toContain("0,95");
  expect(methodAnchorLabel({type:"AnT",source:"MANUAL"})).toContain("ръчно зададена");
  expect(methodAnchorLabel({type:"HRMAX"})).toContain("%HRmax");
  const text=methodHrText({hr_reference_range_bpm:[162,171],hr_role:"PRIMARY_SOURCE_PRESCRIPTION",hr_reference_basis:"PUBLISHED_PERCENT_HRMAX_ON_ATHLETE_SETTING"});
  expect(text).toContain("основен ориентир");expect(text).toContain("Не е процент от MAS");expect(text).not.toContain("Вторичен");
});

it("shows running timeline pace with a defined stop gap and leaves other sports in speed", () => {
  const series=[{elapsed_s:0,speed_kmh:0},{elapsed_s:10,speed_kmh:18}] as ActivitySeriesPoint[];
  const props={title:"Скорост / темпо",series,channels:[{key:"speed_kmh" as const,label:"Скорост",color:"red",unit:"км/ч"}]};
  const running=renderToStaticMarkup(<ActivityTimelineChart {...props} sport="Run"/>);
  expect(running).toContain("3:20 мин/км");expect(running).not.toContain("Infinity");
  const skiing=renderToStaticMarkup(<ActivityTimelineChart {...props} sport="NordicSki"/>);
  expect(skiing).toContain("18 км/ч");expect(skiing).not.toContain("мин/км");
});


it("hydrates compact draft and active session reports losslessly without changing the stored input", () => {
  const shared: Comparison={...comparison,sport:"Run",warnings:["SPEED_TREF_NOT_MIGRATED","NARROW_RANGE_SENSITIVITY_Z4"]};
  const plan: PlanComparison={version:"plan-speed-v1",role:"COMPARISON_ONLY",authoritative_load_changed:false,start_date:"2026-10-20",end_date:"2026-10-26",
    sports:[{sport:"Run",parameters_version:shared.parameters_version,planned:shared,actual:{status:"INCOMPLETE",comparison:null,activity_count:0,available_activity_count:0,missing_comparison_count:0,different_version_count:0,reason:null},
      targets:shared.zones.map(z=>({zone:z.zone,unit:"EQUIVALENT_MINUTES_AT_ZONE_REFERENCE",reference_speed_kmh:z.reference_speed_kmh,target_equivalent_minutes:null,planned_equivalent_minutes:z.equivalent_minutes,actual_equivalent_minutes:null,remaining_equivalent_minutes:null,excess_equivalent_minutes:null,baseline_planned_equivalent_minutes:z.baseline_equivalent_minutes,baseline_actual_equivalent_minutes:null,baseline_remaining_equivalent_minutes:null,target_attainment_percent:null,baseline_target_attainment_percent:null,reason:"LEGACY_HR_TARGET_NOT_CONVERTED"}))}]};
  const compact={storage_format:"SESSION_TOTALS_SHARED_PARAMETERS_V1",parameters_version:shared.parameters_version,sport:"Run",status:"AVAILABLE",recorded_minutes:30,classified_minutes:30,coverage_percent:100,warning_indices:[1,0],
    zone_values:shared.zones.map(z=>[z.real_minutes,z.real_minutes,z.equivalent_minutes,z.baseline_equivalent_minutes,z.normalized_q,z.baseline_normalized_q,z.zone==="Z4"?[21,26.25]:null,null]),
    parts:{WORK:{status:"AVAILABLE",recorded_minutes:30,classified_minutes:30,coverage_percent:100,warning_indices:[0],zones:[{zone:"Z4",real_minutes:30,covered_minutes:30,equivalent_minutes:26.25,baseline_equivalent_minutes:28.5,equivalent_minutes_range:[21,26.25],baseline_equivalent_minutes_range:null}]}}};
  const session={title:"Метод",method_id:"test",sport:"Run",main_work_minutes:30,total_minutes:30,blocks:[],dose_evidence:{explanation:"Доза",limits:[],fallback_reasons:[]},speed_equivalence_comparison:compact};
  const wire={entry_key:"test",revision:2,payload:{schema_version:"planning-draft-v1",status:"DRAFT",engine_version:"test",start_date:"2026-10-20",end_date:"2026-10-26",source:{},warnings:[],speed_equivalence_comparison:plan,
    days:[{date:"2026-10-20",explanation:"Ден",period:"GENERAL_PREPARATION",status:"TRAINING",taper:false,readiness_before:{Z1:95,Z2:95,Z3:95,Z4:95,Z5:95,STR:95},readiness_after:{Z1:95,Z2:95,Z3:95,Z4:95,Z5:95,STR:95},load_budget:{components:{}},rejected_alternatives:[],session,sessions:[session]}]}};
  const before=JSON.stringify(wire), parsed=parseDraftRecord(wire);
  const result=parsed.payload.days[0].sessions![0].speed_equivalence_comparison!;
  expect(result.zones[3]).toMatchObject({real_minutes:30,equivalent_minutes:26.25,baseline_equivalent_minutes:28.5,normalized_q:1.3125,reference_speed_kmh:20,equivalent_minutes_range:[21,26.25]});
  expect(result.settings).toEqual(shared.settings);expect(result.warnings).toEqual(["NARROW_RANGE_SENSITIVITY_Z4","SPEED_TREF_NOT_MIGRATED"]);
  expect(result.parts!.WORK.warnings).toEqual(["NARROW_RANGE_SENSITIVITY_Z4"]);
  expect(parsed.payload.days[0].session?.speed_equivalence_comparison).toEqual(result);
  expect(JSON.stringify(wire)).toBe(before);
  const html=renderToStaticMarkup(<SpeedEquivalenceComparison value={result}/>);
  expect(html).toContain("0:21:00–0:26:15");expect(html).toContain("Основна работа");expect(html).toContain("0:28:30");
  const active=parseActivePlanResponse({history:[],active:{revision:2,stale:false,actionable:true,payload:{schema_version:"active-plan-v2",status:"ACTIVE",mode:"AUTO",reason:"",changes:[],outcomes:[],decisions:{},plan:wire.payload,proposal:wire.payload}}});
  expect(active.active?.payload.plan.days[0].sessions?.[0].speed_equivalence_comparison?.settings).toEqual(shared.settings);
  expect(active.active?.payload.proposal?.days[0].session?.speed_equivalence_comparison?.zones[3].equivalent_minutes).toBe(26.25);
  expect(hydrateSessionSpeedEquivalence(shared)).toBe(shared);
  for(const invalid of [{...compact,parameters_version:"other"},{...compact,sport:"NordicSki"},{...compact,zone_values:compact.zone_values.slice(1)},{...compact,warning_indices:[99]},{...compact,parts:{WORK:{...compact.parts.WORK,warning_indices:[99]}}}])
    expect(()=>hydrateSessionSpeedEquivalence(invalid,plan)).toThrow();
  expect(()=>hydrateSessionSpeedEquivalence(compact)).toThrow();
});
