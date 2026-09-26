// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PlanningRangeCalendar } from "../components/planning-range-calendar";
import { ManagementProfileEditor } from "../components/management-profile-editor";
import { timelineItems, timelineRows } from "../lib/planning-timeline";
import { defaultManagementProfile, defaultPlanningControls, type CycleDirective } from "../lib/training-management";
import type { PlanningCalendarResponse } from "../lib/planning-calendar";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
let root: Root, box: HTMLDivElement;
beforeEach(() => { vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true); box = document.createElement("div"); document.body.append(box); root = createRoot(box); });
afterEach(async () => { await act(async () => root.unmount()); box.remove(); vi.unstubAllGlobals(); });
const mount = async (element: ReactNode) => { await act(async () => root.render(element)); };
const button = (text: string) => [...box.querySelectorAll("button")].find(b => b.textContent === text)!;
const day = (date: string) => box.querySelector<HTMLButtonElement>(`button[aria-label^="${date}"]`)!;
const click = async (b: HTMLElement) => { expect(b).toBeTruthy(); await act(async () => b.click()); };
const select = (label: string) => [...box.querySelectorAll("label")].find(l => l.textContent?.startsWith(label))!.querySelector("select")!;
const choose = async (label: string, value: string) => { await act(async () => { const s = select(label); s.value = value; s.dispatchEvent(new Event("change", { bubbles: true })); }); };
const calendar: PlanningCalendarResponse = { configured: false, calendar: null, context: { schema_version: "planning-context-v1", as_of: "2026-09-23", ready_for_generation: false, generator_status: "NOT_ACTIVE", missing_inputs: [], next_main_race: null, methodology_version: "onflows-canonical-v1", recovery_basis: "LOAD_ONLY", wellness_integration: "DIAGNOSTIC_ONLY" } };
const profile = { ...defaultManagementProfile("2026-09-23"), discipline: "1500 m", planning_controls: defaultPlanningControls("Run") };
const editor = () => <ManagementProfileEditor initialProfile={{ configured: true, profile, revision: 4 }} today="2026-09-23" calendar={calendar}/>;

it("selects across a year boundary with two clicks and keeps the full range", async () => {
  const onSelect = vi.fn();
  await mount(<PlanningRangeCalendar today="2026-09-23" events={[]} initialView="year" onSelect={onSelect}/>);
  expect(box.querySelectorAll(".season-month")).toHaveLength(12);
  await click(day("2026-12-29")); await click(box.querySelector('[aria-label="Следваща година"]')!); await click(day("2027-01-03"));
  await click(button("Добави събитие за периода"));
  expect(onSelect).toHaveBeenCalledWith("2026-12-29", "2027-01-03");
});

it("drags backward across months and releases outside a day without leaving selection active", async () => {
  const onSelect = vi.fn();
  await mount(<PlanningRangeCalendar today="2026-09-23" events={[]} initialView="year" onSelect={onSelect}/>);
  await act(async () => {
    day("2026-10-02").dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, buttons: 1 }));
    day("2026-09-29").dispatchEvent(new MouseEvent("pointerover", { bubbles: true, buttons: 1 }));
    window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true }));
  });
  expect(day("2026-09-30").getAttribute("aria-pressed")).toBe("true");
  await click(button("Добави събитие за периода"));
  expect(onSelect).toHaveBeenCalledWith("2026-09-29", "2026-10-02");
});

it("clips intervals and retains overlapping camps and one-day starts on separate tracks", () => {
  const items = timelineItems([
    { event_id: "camp-0001", event_type: "CAMP", name: "Лагер", start_date: "2026-12-28", end_date: "2027-01-10" },
    { event_id: "race-0001", event_type: "MAIN_RACE", name: "Старт", start_date: "2027-01-04", end_date: "2027-01-04" },
  ], []);
  const rows = timelineRows(items, "2027-01-01", "2027-01-31");
  expect(rows).toHaveLength(2); expect(rows[0][0].left).toBe(0); expect(rows[0][0].width).toBeCloseTo(100 * 10 / 31);
  expect(rows[1][0].width).toBeCloseTo(100 / 31);
});

it("shows automatic periods and accents with no manual events, and reserves recovery after stress", () => {
  const cycle: CycleDirective = { start_date: "2026-12-27", end_date: "2026-12-31", kind: "STRESS", name: "Натоварване", accents: ["Z3"], target_index: 1.3, volume_factor: 1, recovery_days: 7 };
  const automatic = timelineItems([], [], { periodization: { phases: [{ start_date: "2026-10-01", end_date: "2026-10-31", kind: "GENERAL_PREPARATION" }] }, long_term: { weeks: [{ start_date: "2026-10-01", end_date: "2026-10-07", accents: ["Z1", "Z3"], cycle: { kind: "BUILD", name: "Базов мезоцикъл" } }] } });
  expect(automatic.map(i => i.lane)).toEqual(["Периоди", "Акценти"]);
  const recovery = timelineItems([], [cycle]).find(i => i.id === "recovery-0")!;
  expect(recovery.start).toBe("2027-01-01"); expect(recovery.end).toBe("2027-01-07");
});

it("saves a calendar-selected stress block in the existing profile with its expected revision", async () => {
  const fetchMock = vi.fn(async (_url, init) => Response.json({ configured: true, revision: 5, profile: JSON.parse(init.body).profile })); vi.stubGlobal("fetch", fetchMock);
  await mount(editor()); await choose("Какво добавям?", "STRESS"); await click(day("2026-10-01")); await click(day("2026-10-05")); await click(button("Добави избрания период"));
  expect(box.textContent).toContain("Разтоварване след стреса"); expect(fetchMock).not.toHaveBeenCalled();
  await click(button("Запази календара"));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/athlete/management/profile");
  const submitted = JSON.parse(fetchMock.mock.calls[0][1].body);
  expect(submitted.expected_revision).toBe(4); expect(submitted.profile.discipline).toBe("1500 m");
  expect(submitted.profile.planning_controls.cycles[0]).toMatchObject({ kind: "STRESS", start_date: "2026-10-01", end_date: "2026-10-05", recovery_days: 7 });
});

it("rejects an oversized stress range without silently changing its dates", async () => {
  await mount(editor()); await choose("Какво добавям?", "STRESS"); await click(day("2026-10-01")); await click(day("2026-10-10")); await click(button("Добави избрания период"));
  expect(box.querySelector('[role="alert"]')?.textContent).toContain("до 7 дни"); expect(box.querySelectorAll(".management-cycle")).toHaveLength(0);
});

it("keeps calendar edits visible after a failed write and reports when only the profile was saved", async () => {
  const fetchMock = vi.fn(async (url, init) => url.endsWith("/profile") ? Response.json({ configured: true, revision: 5, profile: JSON.parse(init.body).profile }) : { ok: true, url: "https://web.test/planning?planning=calendar-error" }); vi.stubGlobal("fetch", fetchMock);
  await mount(editor()); await choose("Какво добавям?", "STRESS"); await click(day("2026-10-01")); await click(day("2026-10-05")); await click(button("Добави избрания период"));
  await choose("Какво добавям?", "CAMP"); await click(day("2026-11-01")); await click(day("2026-11-05")); await click(button("Добави избрания период"));
  await click(button("Запази календара"));
  expect(fetchMock).toHaveBeenCalledTimes(2); expect(box.textContent).toContain("Профилът и блоковете са записани, но календарните събития не са");
  expect(box.querySelector<HTMLInputElement>('input[name="events_json"]')?.value).toContain("2026-11-01");
  expect(button("Запази календара").disabled).toBe(false);
});

it("retains newly saved events while waiting for refreshed server props", async () => {
  const fetchMock = vi.fn(async (url: string) => ({ ok: url === "/api/athlete/planning-calendar", url: "https://web.test/planning?planning=calendar-saved" })); vi.stubGlobal("fetch", fetchMock);
  await mount(editor()); await click(day("2026-11-01")); await click(day("2026-11-05")); await click(button("Добави избрания период")); await click(button("Запази календара"));
  expect(fetchMock).toHaveBeenCalledTimes(1); expect(box.querySelector<HTMLInputElement>('input[name="events_json"]')?.value).toContain("2026-11-01");
  expect(button("Запази календара").disabled).toBe(true);
  await mount(editor()); expect(box.querySelector<HTMLInputElement>('input[name="events_json"]')?.value).toContain("2026-11-01");
});


it("saves standalone events before a new athlete has completed the planning profile", async () => {
  const fetchMock = vi.fn(async (url: string) => ({ ok: url === "/api/athlete/planning-calendar", url: "https://web.test/planning?planning=calendar-saved" })); vi.stubGlobal("fetch", fetchMock);
  await mount(<ManagementProfileEditor initialProfile={{ configured: false, profile: null, revision: 0 }} today="2026-09-23" calendar={calendar}/>);
  await click(day("2026-11-01")); await click(day("2026-11-05")); await click(button("Добави избрания период")); await click(button("Запази календара"));
  expect(fetchMock).toHaveBeenCalledTimes(1); expect(fetchMock.mock.calls[0][0]).toBe("/api/athlete/planning-calendar");
});

it("distinguishes mesocycle loading focus from conditional support during unloading", () => {
  const plan = {long_term:{weeks:[{start_date:"2026-10-01",end_date:"2026-10-07",accents:["STR"],cycle:{kind:"RECOVERY",name:"Базов мезоцикъл",focus_role:"RECOVERY_SUPPORT",mesocycle_accents:["Z1","Z3"],reason:"Общият товар остава намален."}}]}};
  const item=timelineItems([],[],plan)[0];
  expect(item.label).toBe("Разтоварване · поддържане STR");
  expect(item.detail).toContain("водещи за мезоцикъла: Z1, Z3");
  expect(item.detail).toContain("Общият товар остава намален");
  expect(item.tone).toBe("recovery");
  plan.long_term.weeks[0].accents=[];
  expect(timelineItems([],[],plan)[0].label).toBe("Разтоварване");
});
