// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ManagementProfileEditor } from "../components/management-profile-editor";
import { TrainingManagement } from "../components/training-management";
import { PlanningProfileForm } from "../components/planning-profile-form";
import { TrainingPlanSummary } from "../components/training-plan-summary";
import { TimeAvailability, TimeLimitNotice } from "../components/planning-time-limit";
import { changeActivePlan, newerActivePlan } from "../lib/active-plan-request";
import type { ActivePlanResponse } from "../lib/training-management";
import type { PlanningCalendarResponse } from "../lib/planning-calendar";
import { defaultManagementProfile, defaultPlanningControls, parseManagementProfile, parseDraftRecord, COMPONENTS } from "../lib/training-management";

vi.mock("next/navigation", () => ({useRouter: () => ({refresh: vi.fn()})}));
let root: Root, container: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => {await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals();});
const mount = async (ui: ReactNode) => {await act(async () => root.render(ui));};
const button = (text: string) => [...container.querySelectorAll("button")].find(b => b.textContent === text)!;
const click = async (element: HTMLElement) => {expect(element).toBeTruthy(); await act(async () => element.click());};
const input = (label: string) => [...container.querySelectorAll("label")].find(l => l.textContent?.startsWith(label))!.querySelector("input")!;
const select = (label: string) => [...container.querySelectorAll("label")].find(l => l.textContent?.startsWith(label))!.querySelector("select")!;
async function enter(element: HTMLInputElement, value: string) {
  await act(async () => {Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!.call(element,value); element.dispatchEvent(new Event("input",{bubbles:true}));});
}
async function choose(element: HTMLSelectElement, value: string) {
  await act(async () => {element.value=value; element.dispatchEvent(new Event("change",{bubbles:true}));});
}

it("defaults to today when the automatic race horizon ends today", async () => {
  const today = "2026-09-21";
  await mount(<TrainingManagement athleteName="Спортист" canEdit={true} today={today}
    initialProfile={{ configured: true, revision: 1, profile: { ...defaultManagementProfile(today), discipline: "4000 m", horizon_mode: "AUTO_CALENDAR" } }}
    initialDrafts={[]} initialOutlook={{ schema_version: "training-outlook-preview-v1", profile_revision: 1,
      generated_at: today+"T09:00:00Z", volume_context: {}, long_term: { weeks: [] }, input_snapshot: { horizon: { effective_end: today } } }}/>
  );
  const start = container.querySelector<HTMLInputElement>('[aria-label="Начална дата на програмата"]')!;
  expect(start.value).toBe(today); expect(start.min).toBe(today); expect(start.max).toBe(today);
});

it("preserves an existing past programme anchor while saving other settings", async () => {
  const fetchMock = vi.fn(async (_url, init) => Response.json({ configured: true, revision: 2, profile: JSON.parse(init.body).profile }));
  vi.stubGlobal("fetch", fetchMock);
  const saved = { ...defaultManagementProfile("2026-09-19"), discipline: "4000 m" };
  await mount(<ManagementProfileEditor today="2026-09-21" initialProfile={{ configured: true, revision: 1, profile: saved }}/>);
  await enter(input("Дисциплина"), "5000 m"); await click(button("Запази промените"));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(JSON.parse(fetchMock.mock.calls[0][1].body).profile.program_start).toBe("2026-09-19");
});
const profile = {...defaultManagementProfile("2026-09-21"), discipline:"5000 m", age_years:30, training_experience_years:10};

it("shows the 60–70 percent building band and exact linear readiness examples", async () => {
  const migrated = parseManagementProfile({ ...profile, building_fraction: .5 });
  await mount(<ManagementProfileEditor initialProfile={{ configured: true, profile: migrated, revision: 1 }} today="2026-09-21"/>);
  await click(button("4. Методи и дозиране"));
  const dose = input("Изграждаща доза Z1–Z5");
  expect(dose.value).toBe("65");
  expect(dose.min).toBe("60");
  expect(dose.max).toBe("70");
  expect(container.textContent).toContain("при 90% готовност дозата е 54–63%");
  expect(container.textContent).toContain("при 50% — 30–35% от Tmax");
  expect(container.textContent).toContain("За Z1–Z5 изграждащата работа започва от 60–70%");
  expect(container.textContent).toContain("Готовността намалява този дял веднъж");
  expect(container.textContent).toContain("общият дял на профила е горна граница, а не начална доза");
  expect(container.textContent).toContain("без да е задължително условие");
  expect(container.textContent).not.toContain("Z5 изисква отделна скорошна максимална опора");
  expect(container.textContent).not.toContain("Процентите за Z1–Z3 не се прилагат");
});

it("saves explicit learning controls without resetting the planning profile", async () => {
  const fetchMock = vi.fn(async (_url, init) => Response.json({ configured: true, revision: 2, profile: JSON.parse(init.body).profile }));
  vi.stubGlobal("fetch", fetchMock);
  const savedProfile = { ...profile, available_minutes: [75, 90, 0, 60, 100, 120, 80], building_fraction: .7 };
  await mount(<ManagementProfileEditor initialProfile={{ configured: true, profile: savedProfile, revision: 1 }} today="2026-09-21"/>);
  await click(button("3. Мезоцикли и акценти"));
  expect(select("Режим на самообучение").value).toBe("SHADOW");
  await choose(select("Режим на самообучение"), "CONTROL");
  await enter(input("Максимална стъпка на обема"), "4");
  await enter(input("Максимална стъпка в зоната"), "1.5");
  const exploration = [...container.querySelectorAll("label")].find(label => label.textContent?.includes("Допускай малки пробни промени"))!.querySelector("input")!;
  await click(exploration);
  await click(button("Запази промените"));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const result = JSON.parse(fetchMock.mock.calls[0][1].body).profile;
  expect(result.individual_learning).toEqual({ mode: "CONTROL", exploration_enabled: false, max_volume_step_percent: 4, max_intensity_step: .015 });
  expect(result.available_minutes).toEqual(savedProfile.available_minutes);
  expect(result.building_fraction).toBe(.7);
  expect(result.load_progression).toEqual(savedProfile.load_progression);
});

it("shows component shortfalls separately from session counts and elapsed duration", async () => {
  const draft=parseDraftRecord({entry_key:"test",revision:1,payload:{schema_version:"planning-draft-v1",engine_version:"v6",status:"DRAFT",start_date:"2026-09-23",end_date:"2026-09-29",days:[],source:{},parameters:{},warnings:[],summary:{planned_minutes:0}}});
  await mount(<TrainingPlanSummary plan={{...draft.payload,allocation:{window_start:"2026-09-23",window_end:"2026-09-29",scheduled_slots:9,weekly_session_limit:13,components:{Z1:{target_effective:700,actual_effective:100,planned_effective:400,unallocated_effective:200}},constraints:[{code:"THRESHOLD_DAY_RESERVED",reason:"Този ден е избран за прагова работа."}],dose_limits:["METHOD_CAPACITY_FRACTION"]}}}/>);
  expect(container.textContent).toContain("Остава непланиран товар: Z1");
  expect(container.textContent).toContain("0 предложени сесии от 9 възможни по дните");
  expect(container.textContent).toContain("Седмичен максимум в профила: 13");
  expect(container.textContent).toContain("Приравнен обем от предложените сесии");
  expect(container.textContent).not.toContain("700100400200");
  expect(container.textContent).toContain("0:00:00");
});

it("shows the full current microcycle including past completed days and labels the next partial one", async () => {
  const draft=parseDraftRecord({entry_key:"test",revision:1,payload:{schema_version:"planning-draft-v1",engine_version:"v32",status:"DRAFT",start_date:"2026-10-09",end_date:"2026-10-15",days:[],source:{},parameters:{},warnings:[],summary:{planned_minutes:463,volume_by_microcycle:[
    {start_date:"2026-10-06",end_date:"2026-10-12",through_date:"2026-10-12",complete_microcycle:true,actual_minutes:545,planned_minutes:302,total_minutes:847},
    {start_date:"2026-10-13",end_date:"2026-10-19",through_date:"2026-10-15",complete_microcycle:false,actual_minutes:0,planned_minutes:161,total_minutes:161}]}}});
  await mount(<TrainingPlanSummary plan={draft.payload}/>);
  expect(container.textContent).toContain("14:07:00");
  expect(container.textContent).toContain("9:05:00 изпълнено + 5:02:00 предложено");
  expect(container.textContent).toContain("непълен микроцикъл");
});

it("saves 16 sessions and double-threshold preferences directly from step two", async () => {
  const fetchMock = vi.fn(async (_url, init) => Response.json({configured:true,revision:2,profile:JSON.parse(init.body).profile})); vi.stubGlobal("fetch",fetchMock);
  const legacyProfile = {...profile, planning_controls:{...defaultPlanningControls(profile.actual_sport),mixed_min_readiness:75}};
  await mount(<ManagementProfileEditor initialProfile={{configured:true,profile:legacyProfile,revision:1}} today="2026-09-21"/>);
  await click(button("2. Дни и обем"));
  const count=input("Максимум сесии за 7 дни"); expect(count.max).toBe("21");
  await enter(count,"16");
  await choose(select("Вид прагова тренировка"),"INTERVALS");
  const double=[...container.querySelectorAll("details")].find(d=>d.querySelector("summary")?.textContent==="Двоен праг · по желание")!;
  await click(double.querySelector<HTMLInputElement>('input[type="checkbox"]')!);
  await enter(input("Дял от Tmax за всяка прагова сесия"), "45");
  await enter(input("Пауза между праговите сесии"), "7");
  await enter(input("Лактатен горен ориентир"), "3.2");
  expect(container.textContent).not.toContain("Минимална готовност за допълващия компонент");
  expect(container.textContent).toContain("готовност 50% дава 30–35%");
  await click(button("Запази промените"));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const saved=JSON.parse(fetchMock.mock.calls[0][1].body).profile;
  expect(saved.planning_controls.sessions_per_week).toBe(16);
  expect(saved.planning_controls.double_threshold_days).toEqual([0]);
  expect(saved.planning_controls.threshold_method).toBe("INTERVALS");
  expect(saved.planning_controls.double_threshold_fraction).toBe(.45);
  expect(saved.planning_controls.double_threshold_gap_hours).toBe(7);
  expect(saved.planning_controls.double_threshold_lactate_ceiling).toBe(3.2);
  expect(saved.planning_controls.mixed_min_readiness).toBe(75);
  expect(container.textContent).toContain("Профилът е запазен");
});

it("preserves a manual horizon and saves it without visiting all steps", async () => {
  const fetchMock = vi.fn(async (_url, init) => Response.json({configured:true,revision:2,profile:JSON.parse(init.body).profile}));vi.stubGlobal("fetch",fetchMock);
  await mount(<ManagementProfileEditor initialProfile={{configured:true,profile,revision:1}} today="2026-09-21"/>);
  await choose(select("Край на подготовката"),"MANUAL");
  await enter(input("Край · задължително"),"2027-02-12");
  await click(button("Запази промените"));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const saved=JSON.parse(fetchMock.mock.calls[0][1].body).profile;
  expect(saved.horizon_mode).toBe("MANUAL");expect(saved.program_end).toBe("2027-02-12");
});

it("keeps the current step and save confirmation after the server refreshes its revision", async () => {
  const fetchMock=vi.fn(async (_url,init)=>Response.json({configured:true,revision:2,profile:JSON.parse(init.body).profile}));vi.stubGlobal("fetch",fetchMock);
  const calendar:PlanningCalendarResponse={configured:false,calendar:null,context:{schema_version:"planning-context-v1",as_of:"2026-09-21",ready_for_generation:false,generator_status:"NOT_ACTIVE",missing_inputs:[],next_main_race:null,methodology_version:"onflows-canonical-v1",recovery_basis:"LOAD_ONLY",wellness_integration:"DIAGNOSTIC_ONLY"}};
  const renderForm=(revision:number,p=profile,athleteAlias="athlete-a")=><PlanningProfileForm athleteAlias={athleteAlias} profile={null} managementProfile={{configured:true,profile:p,revision}} planningCalendar={calendar}/>;
  await mount(renderForm(1));
  await click(button("4. Методи и дозиране"));
  await enter(input("Максимум възстановителна работа, мин"),"25");
  await click(button("Запази промените"));
  const saved=JSON.parse(fetchMock.mock.calls[0][1].body).profile;
  await mount(renderForm(2,saved));
  expect(container.textContent).toContain("стъпка 4 от 4");
  expect(container.textContent).toContain("Профилът е запазен");
  expect(button("Запазено").disabled).toBe(true);
  expect(input("Максимум възстановителна работа, мин").value).toBe("25");
  await mount(renderForm(3,{...saved,recovery_session_cap_min:20}));
  expect(input("Максимум възстановителна работа, мин").value).toBe("20");
  await enter(input("Максимум възстановителна работа, мин"),"22");
  await mount(renderForm(4,{...saved,recovery_session_cap_min:15}));
  expect(input("Максимум възстановителна работа, мин").value).toBe("22");
  await mount(renderForm(1,profile,"athlete-b"));
  expect(container.textContent).toContain("стъпка 1 от 4");
  expect(container.textContent).not.toContain("Профилът е запазен");
});

it("explains the actual daily session limit and restores automatic goals only after explicit editing and saving", async () => {
  const fetchMock=vi.fn(async (_url,init)=>Response.json({configured:true,revision:2,profile:JSON.parse(init.body).profile}));vi.stubGlobal("fetch",fetchMock);
  const p={...profile,component_targets_weekly:{Z1:5},planning_controls:{...defaultPlanningControls(profile.actual_sport),sessions_per_week:13,sessions_by_day:[2,2,1,1,2,1,0]}};
  await mount(<ManagementProfileEditor initialProfile={{configured:true,profile:p,revision:1}} today="2026-09-21"/>);
  expect(container.textContent).toContain("Z1: 0:05:00 приравнено време / 7 дни");
  await click(button("2. Дни и обем"));
  expect(container.textContent).toContain("ограниченията по дни позволяват само 9");
  await click(button("Използвай автоматичните цели"));
  expect(fetchMock).not.toHaveBeenCalled();
  await click(button("Запази промените"));
  const saved=JSON.parse(fetchMock.mock.calls[0][1].body).profile;
  expect(saved.component_targets_weekly).toEqual({});
  expect(saved.planning_controls.sessions_by_day).toEqual(p.planning_controls.sessions_by_day);
  expect(saved.planning_controls.sessions_per_week).toBe(13);
});

const vector=Object.fromEntries(COMPONENTS.map(z=>[z,95]));
const session=(title:string,minutes:number)=>({title,method_id:title,sport:"Run",zone:"Z1",purpose:"MAINTENANCE",main_work_minutes:minutes,total_minutes:minutes,canonical_effective_load:vector,direct_equivalent_minutes:vector,
  blocks:[{kind:"WORK",label:title,zone:"Z1",duration_min:minutes,target_hr_bpm:null,target_speed_kmh:null,repetition:null,instructions:"Леко и равномерно."}],
  dose_evidence:{capacity_source:"EXPERT_CONTINUOUS_TREF",capacity_minutes:120,target_hr_bpm:null,target_speed_kmh:null,fraction:.3,requested_work_minutes:minutes,prescribed_work_minutes:minutes,limits:[],fallback_reasons:[],model_version:"v5",explanation:"Общ дневен бюджет.",technical_spill_reference:vector}});
const draft=parseDraftRecord({entry_key:"2026-09-22",revision:1,stale:true,payload:{schema_version:"planning-draft-v1",engine_version:"v5",status:"DRAFT",start_date:"2026-09-22",end_date:"2026-09-28",source:{},warnings:[],summary:{planned_minutes:75},
  days:[{date:"2026-09-22",status:"TRAINING",period:"GENERAL_PREPARATION",taper:false,explanation:"Две сесии с общ бюджет.",session:session("Сутрешна работа",45),sessions:[session("Сутрешна работа",45),session("Следобедна работа",30)],readiness_before:vector,readiness_after:vector,rejected_alternatives:[],load_budget:{remaining_weekly_minutes:null,components:Object.fromEntries(COMPONENTS.map(z=>[z,{e7_daily:0,e40_daily:1,index_7_40:1,target_weekly_effective:100,rolling_7d_effective:0,deficit_effective:100}]))}}]}});

it("shows a restrictive manual goal outside the collapsed explanations, including frozen v5 plans",async()=>{
  await mount(<TrainingPlanSummary plan={{...draft.payload,input_snapshot:{management_profile:{component_targets_weekly:{Z1:5}}}}}/>);
  const warning=container.querySelector<HTMLElement>('aside[role="status"]')!;
  expect(warning.textContent).toContain("Z1: 0:05:00 приравнено време / 7 дни");
  expect(warning.closest("details")).toBeNull();
  expect(warning.querySelector("a")?.getAttribute("href")).toBe("/planning");
});

it("automatically replaces a stale draft and displays every session and their total", async () => {
  const fetchMock=vi.fn<(url:string,init?:RequestInit)=>Promise<Response>>(async (url)=>Response.json(url.includes("drafts?")?{drafts:[draft]}:{...draft,revision:2,stale:false}));vi.stubGlobal("fetch",fetchMock);
  await mount(<TrainingManagement athleteName="Спортист" canEdit initialProfile={{configured:true,profile,revision:2}} initialDrafts={[draft]} today="2026-09-21"/>);
  await vi.waitFor(()=>expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body??"{}"))).toMatchObject({expected_profile_revision:2,expected_draft_revision:1});
  expect(container.textContent).toContain("Следобедна работа");
  expect(container.textContent).toContain("2 сесии");
  expect(container.textContent).toContain("1:15:00");
  expect(container.textContent).not.toContain("Запазени програми");
  expect(container.textContent).not.toContain("Предишен проект");
});

it("previews race duration without saving and drops the preview when discipline changes", async()=>{
  const fetchMock=vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async ()=>Response.json({source:"SPEED_DURATION",duration_min:4.5}));vi.stubGlobal("fetch",fetchMock);
  await mount(<ManagementProfileEditor initialProfile={{configured:true,profile,revision:1}} today="2026-09-21"/>);
  await click(button("Провери приблизителното време"));
  expect(fetchMock.mock.calls[0][0]).toBe("/api/athlete/management/race-duration");
  expect(container.textContent).toContain("около 0:04:30");
  expect(input("Продължителност на старта").value).toBe("");
  await enter(input("Дисциплина"),"10 km");
  expect(container.textContent).not.toContain("около 0:04:30");
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("labels one hour as a total ceiling, preserves it on save and clears it only by explicit choice", async()=>{
  const fetchMock=vi.fn(async (_url,init)=>Response.json({configured:true,revision:2,profile:JSON.parse(init.body).profile}));vi.stubGlobal("fetch",fetchMock);
  const p={...profile,planning_controls:{...defaultPlanningControls(profile.actual_sport),weekly_target_hours:1}};
  await mount(<ManagementProfileEditor initialProfile={{configured:true,profile:p,revision:1}} today="2026-09-21"/>);
  await click(button("2. Дни и обем"));
  expect(input("Максимум общо време за 7 дни").value).toBe("1");
  expect(container.textContent).toContain("Стойност 1 означава максимум 1 час общо, не добавяне на 1 час");
  await click(button("Премахни седмичния таван"));
  expect(input("Максимум общо време за 7 дни").value).toBe("");
  expect(fetchMock).not.toHaveBeenCalled();
  await click(button("Запази промените"));
  expect(JSON.parse(fetchMock.mock.calls[0][1].body).profile.planning_controls.weekly_target_hours).toBeNull();
});

it("shows a one-hour cap in automatic mode and explains actual time consuming it",async()=>{
  const parameters={availability_mode:"AUTO_HISTORY",planning_controls:{weekly_target_hours:1},time_budget:{start_date:"2026-09-21",end_date:"2026-09-27",period_limit_minutes:60,actual_minutes:75,planned_minutes:0,remaining_minutes:0,actual_excess_minutes:15}};
  await mount(<TrainingPlanSummary plan={{...draft.payload,days:[],summary:{planned_minutes:0},parameters}}/>);
  expect(container.textContent).toContain("Лимит за време1:00:00максимум общо за 7 дни");
  expect(container.textContent).not.toContain("Автоматично");
  expect(container.textContent).toContain("изпълнено 1:15:00; предложено 0:00:00; оставащо време 0:00:00");
  expect(container.textContent).toContain("Изпълненото вече надхвърля лимита");
});

it("shows the tighter time constraint and keeps long-term estimates distinct",async()=>{
  const context={availability_mode:"MANUAL",weekly_time_limit_minutes:600,available_weekly_minutes:210};
  await mount(<><TimeAvailability context={context}/><TimeLimitNotice context={context} forecast/></>);
  expect(container.textContent).toContain("Лимит за време3:30:00");
  expect(container.textContent).toContain("Записан седмичен таван: 10:00:00");
  expect(container.textContent).toContain("еквивалент на компонентните цели преди ограниченията за време");
});

const activeResponse=(revision:number,stale=false):ActivePlanResponse=>({active:{revision,stale,actionable:false,payload:{schema_version:"active-plan-v2",status:"REVIEW_REQUIRED",mode:"AUTO",reason:"За преглед",plan:draft.payload,proposal:{...draft.payload,activation_eligible:true},changes:[],outcomes:[],decisions:{}}},history:[]});

it("disables manual actions during automatic refresh and loads a concurrent newer version without replaying",async()=>{
  let resolvePost!:(response:Response)=>void;
  const fetchMock=vi.fn<(url:string,init?:RequestInit)=>Promise<Response>>((url)=>url.endsWith("/active")?Promise.resolve(Response.json(activeResponse(4))):new Promise(resolve=>{resolvePost=resolve;}));vi.stubGlobal("fetch",fetchMock);
  await mount(<TrainingManagement athleteName="Спортист" canEdit initialProfile={{configured:true,profile,revision:2}} initialDrafts={[]} initialActive={activeResponse(3,true)} today="2026-09-21"/>);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(button("Обновяване…").disabled).toBe(true);
  expect(button("Пауза").disabled).toBe(true);
  expect(button("Утвърди актуалната адаптация").disabled).toBe(true);
  await click(button("Обновяване…"));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async()=>resolvePost(Response.json({error:"Conflict"},{status:409})));
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(container.textContent).toContain("Текущ план · версия 4");
  expect(container.textContent).toContain("Заредена е последната версия");
  expect(container.querySelector('[role="alert"]')).toBeNull();
  expect(button("Утвърди актуалната адаптация").disabled).toBe(false);
});

it("never automatically retries an approval after conflict or replaces a newer response with an older poll",async()=>{
  const fetchMock=vi.fn<(url:string,init?:RequestInit)=>Promise<Response>>(async (_url,init)=>init?.method==="POST"?Response.json({error:"Conflict"},{status:409}):Response.json(activeResponse(8)));vi.stubGlobal("fetch",fetchMock);
  const result=await changeActivePlan("action",{action:"APPROVE",expected_revision:7});
  expect(result.conflict).toBe(true);
  expect(result.value.active?.revision).toBe(8);
  expect(fetchMock.mock.calls.filter(([,init])=>init?.method==="POST")).toHaveLength(1);
  expect(newerActivePlan(result.value,activeResponse(7)).active?.revision).toBe(8);
});


it("compares direct period volume with actual plus planned without counting cascade as coverage", async () => {
  const draft=parseDraftRecord({entry_key:"test",revision:1,payload:{schema_version:"planning-draft-v1",engine_version:"v17",status:"DRAFT",start_date:"2026-09-23",end_date:"2026-09-29",days:[],source:{},parameters:{},warnings:[],summary:{planned_minutes:0}}});
  await mount(<TrainingPlanSummary plan={{...draft.payload,allocation:{scheduled_slots:7,weekly_session_limit:7,components:{Z1:{basis:"DIRECT_Q",target:120,actual:20,planned:70,remaining:30,target_effective:500,actual_effective:100,planned_effective:400,unallocated_effective:0}},constraints:[],dose_limits:[]}}}/>);
  expect(container.textContent).toContain("Остава непланиран товар: Z1");
  const table=container.querySelector("table")!;
  expect(table.textContent).toContain("Изпълнено");
  expect(table.textContent).toContain("Приравнен обем");
  expect(table.textContent).toContain("2:00:00");
  expect(table.textContent).toContain("0:30:00");
  expect(table.textContent).toContain("75.0%");
});

it("keeps separate microcycle shortfalls visible when the whole-period totals cancel", async () => {
  const draft=parseDraftRecord({entry_key:"segments",revision:1,payload:{schema_version:"planning-draft-v1",engine_version:"v30",status:"DRAFT",start_date:"2026-09-23",end_date:"2026-09-29",days:[],source:{},parameters:{volume_governor:"COMPONENT_7_40"},warnings:[],summary:{planned_minutes:0}}});
  await mount(<TrainingPlanSummary plan={{...draft.payload,allocation:{
    status:"LONG_TERM_SEGMENT_OBJECTIVES_WITH_PHYSIOLOGICAL_GATES",scheduled_slots:7,weekly_session_limit:7,
    components:{Z1:{basis:"DIRECT_Q",target:120,actual:100,planned:20,remaining:0,target_effective:600,actual_effective:500,planned_effective:100,unallocated_effective:0}},
    segments:[
      {window_start:"2026-09-23",window_end:"2026-09-25",days:3,components:{Z1:{basis:"DIRECT_Q",target:60,actual:0,planned:20,remaining:40,target_effective:300,actual_effective:0,planned_effective:100,remaining_effective:200}}},
      {window_start:"2026-09-26",window_end:"2026-09-29",days:4,components:{Z1:{basis:"DIRECT_Q",target:60,actual:100,planned:0,remaining:0,target_effective:300,actual_effective:500,planned_effective:0,remaining_effective:0,actual_exceeds_target:true,actual_effective_exceeds_target:true}}},
    ],constraints:[],dose_limits:["ROLLING_Q_AND_7_40_BUDGET"],
  }}}/>);
  expect(container.textContent).toContain("Цели на дългосрочната програма");
  expect(container.textContent).toContain("Остава непланиран товар: Z1");
  const tables=[...container.querySelectorAll("table")];
  expect(tables).toHaveLength(2);
  expect(tables[0].querySelector("caption")?.textContent).toContain("23.09.2026 г. – 25.09.2026 г.");
  expect(tables[1].querySelector("caption")?.textContent).toContain("26.09.2026 г. – 29.09.2026 г.");
  const firstCells=[...tables[0].querySelectorAll("tbody td")].map(cell=>cell.textContent);
  expect(firstCells[4]).toBe("0:40:00");
  expect(firstCells[6]).toBe("3:20:00");
  expect(tables[1].textContent).toContain("Изпълненото е над целта");
  expect(container.textContent).toContain("Остатъкът не се прехвърля между микроцикли");
  expect(container.textContent).not.toContain("LONG_TERM_SEGMENT_OBJECTIVES_WITH_PHYSIOLOGICAL_GATES");
  expect(container.textContent).not.toContain("ROLLING_Q_AND_7_40_BUDGET");
  expect(container.textContent).not.toContain("ограничението по 7/40");
});
