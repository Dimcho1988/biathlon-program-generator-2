// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ResponseMonitoring } from "../components/response-monitoring";
import { TrainingObservationSettings } from "../components/training-observation-settings";
import { responseFixture } from "../lib/response-fixture";
import { defaultManagementProfile, parseManagementProfile } from "../lib/training-management";
import { defaultNeuromuscular } from "../lib/training-observations";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
let root: Root, container: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });
async function click(text: string) {
  const b = Array.from(container.querySelectorAll("button")).find(b => b.textContent === text)!;
  await act(async () => b.click());
}
function input(name: string, value: string) { container.querySelector<HTMLInputElement>(`[name="${name}"]`)!.value = value; }

it("opens the selected activity and records real lactate and NMS without inventing RPE", async () => {
  const fetchMock = vi.fn(async () => Response.json({ saved: true, revision: 2 })); vi.stubGlobal("fetch", fetchMock);
  const session = { ...responseFixture.sessions[0], rpe: null, timing: "UNKNOWN", lactate_samples: [] };
  const history = { ...responseFixture, sessions: [session] };
  await act(async () => root.render(<ResponseMonitoring history={history} canReport canEditPlan={false} selectedActivity={session.activity_ref}/>));
  expect(container.querySelector<HTMLDetailsElement>(".response-session")!.open).toBe(true);
  await click("Добави проба");
  input("la_0_value", "4.2"); input("la_0_delay", "60"); input("la_0_high", "4");
  const after = container.querySelector<HTMLSelectElement>('[name="la_0_after"]')!;
  await act(async () => { after.value = "REPETITION"; after.dispatchEvent(new Event("change", { bubbles: true })); });
  input("la_0_repetition", "4");
  await act(async () => container.querySelector<HTMLInputElement>('[name="la_0_confirmed"]')!.click());
  await act(async () => container.querySelector<HTMLInputElement>('[name="nms_record"]')!.click());
  input("nms_repetitions", "4"); input("nms_seconds", "40"); input("nms_speed", "28.5");
  input("nms_planned_repetitions", "5"); input("nms_planned_seconds", "50");
  const form = after.closest("form")!;
  await act(async () => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
  expect(JSON.parse(String(init.body)).payload).toMatchObject({ activity_ref: session.activity_ref, rpe: null,
    lactate_samples: [{ value_mmol: 4.2, after: "REPETITION", repetition: 4, delay_seconds: 60,
      planned_high_mmol: 4, comparison_confirmed: true }],
    neuromuscular: { repetitions: 4, work_seconds: 40, peak_speed_kmh: 28.5, planned_repetitions: 5, planned_work_seconds: 50 } });
});

it("can remove observations on revision without leaving hidden sample values", async () => {
  const fetchMock = vi.fn(async () => Response.json({ saved: true })); vi.stubGlobal("fetch", fetchMock);
  const session = { ...responseFixture.sessions[0], revision: 3, lactate_samples: [{ value_mmol: 3, zone: null,
    after: "REPETITION" as const, repetition: 2, delay_seconds: 30, planned_low_mmol: null, planned_high_mmol: 4,
    comparison_confirmed: true, note: "after warmup" }], neuromuscular: { repetitions: 4, work_seconds: 40,
    peak_speed_kmh: null, planned_repetitions: null, planned_work_seconds: null, note: "" } };
  await act(async () => root.render(<ResponseMonitoring history={{ ...responseFixture, sessions: [session] }} canReport canEditPlan={false}/>));
  await click("Премахни проба 1");
  const nms = container.querySelector<HTMLInputElement>('[name="nms_record"]')!;
  await act(async () => nms.click());
  await act(async () => { nms.closest("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
  expect(JSON.parse(String(init.body)).payload).toMatchObject({ expected_revision: 3, lactate_samples: [], neuromuscular: null });
});

it("accepts personal references outside population values and keeps old profiles optional", async () => {
  const base = { ...defaultManagementProfile("2026-09-28"), discipline: "5000 m" };
  expect(parseManagementProfile(base).lactate_profiles).toEqual([]);
  function Editor() { const [p, setP] = useState(base); return <TrainingObservationSettings profile={p} onChange={setP} today="2026-09-28"/>; }
  await act(async () => root.render(<Editor/>));
  await click("Добави индивидуални ориентири или тест");
  expect(container.querySelector('[aria-label="Z5 лактат до"]')).not.toBeNull();
  const personal = { sport: "Run" as const, source: "MANUAL" as const, assessed_on: "2026-09-28", device: "", protocol: "", note: "",
    stages: [], zone_ranges: { Z3: { low_mmol: 5, high_mmol: 7 } } };
  expect(parseManagementProfile({ ...base, lactate_profiles: [personal] }).lactate_profiles?.[0].zone_ranges.Z3?.high_mmol).toBe(7);
  expect(() => parseManagementProfile({ ...base, lactate_profiles: [{ ...personal, source: "TEST" }] })).toThrow();
  expect(() => parseManagementProfile({ ...base, neuromuscular: { ...defaultNeuromuscular(), work_seconds: 20, recovery_seconds: 30 } })).toThrow();
});
