// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PersonalMethodsPanel, TrainingStructurePanel } from "../components/training-proposals";
import type { LibraryMethod } from "../lib/method-library";
import { parseMethodPreferences } from "../lib/method-library";
import { parseTrainingStructures } from "../lib/training-proposals";

const state = { revision: 7, profile_revision: 7, active_plan_revision: 4, payload: { personal_methods: [], training_structure: [] } };
const methods: LibraryMethod[] = [{ id: "RUN01", title: "Интервали", source: "Централен каталог", sports: ["Run"], periods: [], disciplines: [], guidance: {}, personal_variant_constraints: { eligible: true, work_unit: "seconds", work_range: [30, 120], repetitions: [4, 8], recovery_seconds: [0, 60] } }];
let root: Root, container: HTMLDivElement;
beforeEach(() => { vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true); container = document.createElement("div"); document.body.append(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });
const button = (label: string) => [...container.querySelectorAll("button")].find(item => item.textContent === label)!;
const click = async (item: HTMLElement) => { await act(async () => item.click()); };
const enter = async (item: HTMLInputElement, value: string) => { await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(item, value); item.dispatchEvent(new Event("input", { bubbles: true })); }); };
const select = async (item: HTMLSelectElement, value: string) => { await act(async () => { item.value = value; item.dispatchEvent(new Event("change", { bubbles: true })); }); };
async function open() { await act(async () => { const panel = container.querySelector("details")!; panel.open = true; panel.dispatchEvent(new Event("toggle")); }); }
const input = (label: string) => [...container.querySelectorAll("label")].find(item => item.textContent?.startsWith(label))!.querySelector("input")!;

it("keeps personal method discussion read-only and applies the normalized variant once", async () => {
  let normalized: unknown;
  const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/preview")) { const requested = JSON.parse(String(init?.body)).proposal; normalized = { ...requested, method: { ...requested.method, work_range: [60, 90], recovery_seconds: [0, 0] } }; return Response.json({ ...state, preview: { proposal: normalized, summary: "Личният вариант е допустим.", corrections: [{ reason: "Работната отсечка е ограничена до 60–90 секунди." }], days: [] } }); }
    return Response.json(state);
  }); vi.stubGlobal("fetch", fetchMock);
  await act(async () => root.render(<PersonalMethodsPanel methods={methods} canEdit/>));
  expect(fetchMock).not.toHaveBeenCalled(); await open(); await click(button("Добави личен вариант"));
  await enter(input("Име на личния вариант"), "Моят вариант"); await click(button("Прегледай предложението"));
  expect(container.textContent).toContain("60–90 секунди"); expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/apply"))).toHaveLength(0);
  await click(button("Приложи промяната"));
  const applied = fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/apply")); expect(applied).toHaveLength(1);
  expect(JSON.parse(applied[0][1].body)).toEqual({ proposal: normalized, expected_revision: 7, operation_id: expect.any(String) });
  expect(container.textContent).toContain("Промяната е записана"); expect(button("Приложи промяната")).toBeUndefined();
});
it.each([
  { reason: "CENTRAL_DECLARED_VARIANT", field: "repetitions", requested: [8, 8], applied: [4, 4], text: "Брой повторения: 8 → 4.", explanation: "един от допустимите варианти на основния метод" },
  { reason: "CENTRAL_METHOD_BOUNDS", field: "work_range", requested: [240, 300], applied: [120, 120], text: "Работна отсечка: 240–300 → 120 сек.", explanation: "допустимите граници на основния метод" },
  { reason: "FUTURE_CORRECTION_RULE", field: "recovery_seconds", requested: [90, 90], applied: [60, 60], text: "Почивка: 90 → 60 сек.", explanation: "правилата на основния метод" },
])("explains $reason and shows the exact correction before applying", async ({ reason, field, requested, applied, text, explanation }) => {
  const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
    if (!url.endsWith("/preview")) return Response.json(state);
    const proposal = JSON.parse(String(init?.body)).proposal;
    expect(proposal.method[field]).toEqual(requested);
    return Response.json({ ...state, preview: { proposal: { ...proposal, method: { ...proposal.method, [field]: applied } }, summary: "Предложението е коригирано.", corrections: [{ reason, field, requested, applied }], days: [] } });
  }); vi.stubGlobal("fetch", fetchMock);
  await act(async () => root.render(<PersonalMethodsPanel methods={methods} canEdit/>)); await open(); await click(button("Добави личен вариант"));
  await enter(input("Име на личния вариант"), "Лични интервали");
  const label = field === "repetitions" ? "Брой повторения" : field === "work_range" ? "Работна отсечка, секунди" : "Почивка, секунди";
  await enter(container.querySelector(`[aria-label="${label} от"]`)!, String(requested[0]));
  await enter(container.querySelector(`[aria-label="${label} до"]`)!, String(requested[1]));
  await click(button("Прегледай предложението"));
  const correction = container.querySelector(".proposal-preview li")?.textContent;
  expect(correction).toContain(text); expect(correction).toContain(explanation); expect(container.textContent).not.toContain(reason);
  expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/apply"))).toHaveLength(0);
});
it("previews a 14-day source across stored weeks and invalidates the preview after edits", async () => {
  const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => url.endsWith("/preview")
    ? Response.json({ ...state, preview: { proposal: { ...JSON.parse(String(init?.body)).proposal, source_revisions: [3, 4], expected_plan_revision: 4 }, summary: "Две седмици с нова доза.", corrections: [], days: [] } }) : Response.json(state)); vi.stubGlobal("fetch", fetchMock);
  await act(async () => root.render(<TrainingStructurePanel today="2026-11-16" defaultStart="2026-11-02" canEdit/>)); await open();
  const selects = container.querySelectorAll("select"); await select(selects[0], "REPEAT_STRUCTURE"); await select(container.querySelectorAll("select")[1], "14");
  await enter(input("Повтори от"), "2026-11-16"); await click(button("Прегледай предложението"));
  const payload = JSON.parse(fetchMock.mock.calls.find(([url]) => String(url).endsWith("/preview"))![1].body);
  expect(payload.proposal).toEqual({ action: "REPEAT_STRUCTURE", source_start: "2026-11-02", source_end: "2026-11-15", target_start: "2026-11-16" });
  expect(button("Приложи промяната")).toBeDefined(); await enter(input("Повтори от"), "2026-11-23"); expect(button("Приложи промяната")).toBeUndefined();
});
it("retains entered values on version conflicts and requires a fresh preview", async () => {
  const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => url.endsWith("/preview")
    ? Response.json({ ...state, preview: { proposal: JSON.parse(String(init?.body)).proposal, summary: "Запази седмицата.", corrections: [], days: [] } })
    : url.endsWith("/apply") ? Response.json({ error: "Профилът е променен. Прегледай актуалните данни." }, { status: 409 }) : Response.json(state)); vi.stubGlobal("fetch", fetchMock);
  await act(async () => root.render(<TrainingStructurePanel today="2026-11-16" canEdit/>)); await open();
  await enter(input("Име (по желание)"), "Технически блок"); await click(button("Прегледай предложението")); await click(button("Приложи промяната"));
  expect(input("Име (по желание)").value).toBe("Технически блок"); expect(container.querySelector('[role="alert"]')?.textContent).toContain("Профилът е променен");
  expect(button("Приложи промяната")).toBeUndefined(); expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/apply"))).toHaveLength(1);
});
it("shows a failed preview message once and retains the entered block", async () => {
  const message = "Управлението временно не е достъпно. Въведените стойности остават във формата.";
  const fetchMock = vi.fn().mockImplementation(async (url: string) => url.endsWith("/preview") ? Response.json({ error: message }, { status: 503 }) : Response.json(state)); vi.stubGlobal("fetch", fetchMock);
  await act(async () => root.render(<TrainingStructurePanel today="2026-11-16" canEdit/>)); await open();
  await enter(input("Име (по желание)"), "Блок за повторение"); await click(button("Прегледай предложението"));
  expect(container.querySelector('[role="alert"]')?.textContent).toBe(message);
  expect(input("Име (по желание)").value).toBe("Блок за повторение");
  expect(input("Начало на изходния период").value).toBe("2026-11-16");
  expect(button("Приложи промяната")).toBeUndefined(); expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/apply"))).toHaveLength(0);
});
it("shows athlete variants without edit actions for read-only access", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ ...state, payload: { ...state.payload, personal_methods: [{ id: "PERSONAL-one", name: "Индивидуални интервали", base_method_id: "RUN01", work_unit: null, work_range: null, repetitions: null, recovery_seconds: null, notes: null }] } })));
  await act(async () => root.render(<PersonalMethodsPanel methods={methods} canEdit={false}/>)); await open();
  expect(container.textContent).toContain("Индивидуални интервали"); expect(container.querySelector("form")).toBeNull(); expect(container.querySelector("button")).toBeNull();
});
it("preserves personal methods in preference updates and accepts canonical structure method identifiers", () => {
  expect(parseMethodPreferences({ enabled: true, overrides: {}, personal_methods: [{ id: "PERSONAL-one", name: "Вариант", base_method_id: "RUN01", recovery_seconds: [0, 0] }] }).personal_methods?.[0].recovery_seconds).toEqual([0, 0]);
  expect(parseTrainingStructures([{ id: "block-1", name: "Сесия", mode: "SESSION", start_date: "2026-11-16", days: [{ offset: 0, sessions: [{ slot: 1, method_ids: ["RUN:BASE"], sport: "Run" }] }] }])).toHaveLength(1);
});
