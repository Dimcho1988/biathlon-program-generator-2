// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ResponseMonitoring } from "../components/response-monitoring";
import { responseFixture } from "../lib/response-fixture";
import { parseResponseHistory, type ResponseHistory } from "../lib/response-monitoring";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
let root: Root, container: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });
const history = (): ResponseHistory => ({ ...responseFixture, sessions: [responseFixture.sessions[0]], execution_methods: [
  { id: "method-z1", title: "Равномерна работа", zone: "Z1", sports: ["NordicSki"] },
  { id: "method-z3", title: "Прагови интервали", zone: "Z3", sports: ["NordicSki"] },
] });
async function choose(value: string) {
  const element = container.querySelector<HTMLSelectElement>('[name="executed_method_id"]')!;
  await act(async () => { element.value = value; element.dispatchEvent(new Event("change", { bubbles: true })); });
}

it("requires fresh confirmation after changing an executed method and submits the actual choice", async () => {
  const fetchMock = vi.fn(async () => Response.json({ saved: true, revision: 2 })); vi.stubGlobal("fetch", fetchMock);
  const data = history();
  await act(async () => root.render(<ResponseMonitoring history={data} canReport canEditPlan={false}/>));
  const checkbox = container.querySelector<HTMLInputElement>('[name="method_confirmed"]')!;
  expect(checkbox.disabled).toBe(true);
  await choose("method-z1");
  await act(async () => checkbox.click());
  expect(checkbox.checked).toBe(true);
  await choose("method-z3");
  expect(checkbox.checked).toBe(false);
  await act(async () => checkbox.click());
  const form = checkbox.closest("form")!;
  await act(async () => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
  expect(JSON.parse(String(init.body))).toMatchObject({ kind: "session", payload: {
    activity_ref: data.sessions[0].activity_ref, executed_method_id: "method-z3", method_confirmed: true,
  } });
});

it("keeps method metadata optional for older histories and validates new catalog entries", () => {
  expect(parseResponseHistory(responseFixture).execution_methods).toBeUndefined();
  expect(parseResponseHistory(history()).execution_methods).toHaveLength(2);
  expect(() => parseResponseHistory({ ...history(), execution_methods: [{ id: "bad", title: 5 }] })).toThrow();
});
