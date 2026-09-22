// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ManagementProfileEditor } from "../components/management-profile-editor";
import { TrainingManagement } from "../components/training-management";
import { defaultManagementProfile, parseDraftRecord, COMPONENTS } from "../lib/training-management";

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
const profile = {...defaultManagementProfile("2026-09-21"), discipline:"5000 m", age_years:30, training_experience_years:10};

it("saves 16 sessions and double-threshold preferences directly from step two", async () => {
  const fetchMock = vi.fn(async (_url, init) => Response.json({configured:true,revision:2,profile:JSON.parse(init.body).profile})); vi.stubGlobal("fetch",fetchMock);
  await mount(<ManagementProfileEditor initialProfile={{configured:true,profile,revision:1}} today="2026-09-21"/>);
  await click(button("2. Дни и обем"));
  const count=input("Максимум сесии за 7 дни"); expect(count.max).toBe("21");
  await enter(count,"16");
  await choose(select("Вид прагова тренировка"),"INTERVALS");
  const double=[...container.querySelectorAll("details")].find(d=>d.querySelector("summary")?.textContent==="Двоен праг · по желание")!;
  await click(double.querySelector<HTMLInputElement>('input[type="checkbox"]')!);
  await click(button("Запази промените"));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const saved=JSON.parse(fetchMock.mock.calls[0][1].body).profile;
  expect(saved.planning_controls.sessions_per_week).toBe(16);
  expect(saved.planning_controls.double_threshold_days).toEqual([0]);
  expect(saved.planning_controls.threshold_method).toBe("INTERVALS");
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

const vector=Object.fromEntries(COMPONENTS.map(z=>[z,95]));
const session=(title:string,minutes:number)=>({title,method_id:title,sport:"Run",zone:"Z1",purpose:"MAINTENANCE",main_work_minutes:minutes,total_minutes:minutes,canonical_effective_load:vector,direct_equivalent_minutes:vector,
  blocks:[{kind:"WORK",label:title,zone:"Z1",duration_min:minutes,target_hr_bpm:null,target_speed_kmh:null,repetition:null,instructions:"Леко и равномерно."}],
  dose_evidence:{capacity_source:"EXPERT_CONTINUOUS_TREF",capacity_minutes:120,target_hr_bpm:null,target_speed_kmh:null,fraction:.3,requested_work_minutes:minutes,prescribed_work_minutes:minutes,limits:[],fallback_reasons:[],model_version:"v5",explanation:"Общ дневен бюджет.",technical_spill_reference:vector}});
const draft=parseDraftRecord({entry_key:"2026-09-22",revision:1,stale:true,payload:{schema_version:"planning-draft-v1",engine_version:"v5",status:"DRAFT",start_date:"2026-09-22",end_date:"2026-09-28",source:{},warnings:[],summary:{planned_minutes:75},
  days:[{date:"2026-09-22",status:"TRAINING",period:"GENERAL_PREPARATION",taper:false,explanation:"Две сесии с общ бюджет.",session:session("Сутрешна работа",45),sessions:[session("Сутрешна работа",45),session("Следобедна работа",30)],readiness_before:vector,readiness_after:vector,rejected_alternatives:[],load_budget:{remaining_weekly_minutes:null,components:Object.fromEntries(COMPONENTS.map(z=>[z,{e7_daily:0,e40_daily:1,index_7_40:1,target_weekly_effective:100,rolling_7d_effective:0,deficit_effective:100}]))}}]}});

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
