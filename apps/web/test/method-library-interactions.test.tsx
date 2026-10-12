// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TrainingMethodLibrary } from "../components/training-method-library";
import type { MethodLibraryResponse } from "../lib/method-library";

const initial: MethodLibraryResponse = { version: "v1", configured: true, revision: 7, preferences: { enabled: true, overrides: {} }, phase: "SPECIAL_PREPARATION", profile: { discipline: "5000 m" }, methods: [
  { id: "RUN01", title: "Пет километра", sports: ["Run"], periods: ["SPECIAL_PREPARATION"], disciplines: [5000], source: "Anderson", guidance: { purpose: "Контрол на ритъма." }, automatic_selection: true,
    prescription: { anchor: { type: "RACE_DISTANCE", distance_m: 5000 }, speed_multiplier: [1, 1.03], work: { unit: "meters", range: [400, 1000] }, repetitions: [4, 8] }, resolution: { status: "READY" } },
  { id: "SKI01", title: "Ски интервали", sports: ["NordicSki"], periods: ["SPECIAL_PREPARATION"], disciplines: [10000], source: "Laursen", guidance: { purpose: "Запазване на техниката." }, prescription: null, resolution: { status: "REFERENCE_ONLY" } },
] };
let root: Root, container: HTMLDivElement;
beforeEach(() => { vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true); container = document.createElement("div"); document.body.append(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });
const mount = async (canEdit = true) => { await act(async () => root.render(<TrainingMethodLibrary initial={initial} athleteName="Дими" canEdit={canEdit}/>)); };
const button = (text: string) => [...container.querySelectorAll("button")].find(b => b.textContent === text)!;
const click = async (element: HTMLElement) => { await act(async () => element.click()); };
const enter = async (element: HTMLInputElement, value: string) => { await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value); element.dispatchEvent(new Event("input", { bubbles: true })); }); };

it("filters by sport and search without touching stored preferences", async () => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); await mount();
  const sport = [...container.querySelectorAll("label")].find(l => l.textContent?.startsWith("Спорт"))!.querySelector("select")!;
  await act(async () => { sport.value = "NordicSki"; sport.dispatchEvent(new Event("change", { bubbles: true })); });
  expect(container.querySelectorAll(".method-card")).toHaveLength(1); expect(container.querySelector(".method-card")?.textContent).toContain("Ски интервали");
  expect(fetchMock).not.toHaveBeenCalled();
});
it("saves numeric percentages as multipliers with CAS and reloads the personal preview", async () => {
  const fetchMock = vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => {
    if (init?.method === "PUT") { const body = JSON.parse(String(init.body)); return Response.json({ configured: true, revision: 8, preferences: body.preferences }); }
    return Response.json({ ...initial, revision: 8, preferences: { enabled: true, overrides: { RUN01: { speed_multiplier: [1.01, 1.03] } } } });
  }); vi.stubGlobal("fetch", fetchMock); await mount();
  await click(button("Настройки за спортиста"));
  await enter(container.querySelector('[aria-label="Интензивност, % от опората от"]')!, "101");
  expect(container.textContent).toContain("Има незаписани промени");
  await click(button("Запази настройките"));
  expect(fetchMock.mock.calls[0][0]).toBe("/api/athlete/management/methods/preferences");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ expected_revision: 7, preferences: { enabled: true, overrides: { RUN01: { speed_multiplier: [1.01, 1.03] } } } });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(container.textContent).toContain("Настройките са записани");
});
it("retains unsaved edits on conflict and requires explicit reload before retry", async () => {
  const fetchMock = vi.fn().mockResolvedValue(Response.json({ error: "Профилът е променен." }, { status: 409 })); vi.stubGlobal("fetch", fetchMock); await mount();
  await click(button("Настройки за спортиста"));
  await enter(container.querySelector('[aria-label="Интензивност, % от опората от"]')!, "101");
  await click(button("Запази настройките"));
  expect((container.querySelector('[aria-label="Интензивност, % от опората от"]') as HTMLInputElement).value).toBe("101");
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("Локалните промени са запазени");
  expect(button("Запази настройките").disabled).toBe(true);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("keeps read-only access free of preference controls", async () => {
  await mount(false);
  expect(container.textContent).toContain("достъп за преглед");
  expect(button("Настройки за спортиста")).toBeUndefined();
  expect(button("Запази настройките")).toBeUndefined();
});
