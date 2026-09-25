// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { microcycleVolume } from "../lib/microcycle-volume";
import { MicrocycleVolumes } from "../components/microcycle-volumes";
import { type PlanningDraft, type DraftDay, type DraftSession } from "../lib/training-management";

const session = (zone: string, minutes: number) => ({ total_minutes: minutes + 10, zone,
  blocks: [{ zone: "Z1", duration_min: 5 }, { zone, duration_min: minutes }, { zone: "NONE", duration_min: 5 }],
  canonical_effective_load: { Z1: 900, Z2: 800 } }) as DraftSession;
const first = session("Z3", 20), second = session("STR", 30);
const plan = { days: [
  { date: "2026-09-29", sessions: [first, second], session: first },
  { date: "2026-09-30", status: "REST", sessions: [], session: null },
  { date: "2026-10-01", status: "REVIEW_REQUIRED", sessions: [], session: null },
  { date: "2026-10-06", sessions: [session("Z2", 60)] },
] } as unknown as PlanningDraft;

it("counts both sessions once, includes warmup/rest and ignores effective spill as clock time", () => {
  const v = microcycleVolume(plan, "2026-09-29", "2026-10-05");
  expect(v.total).toBe(70);
  expect(v.components).toEqual({ Z1: 10, Z2: 0, Z3: 20, Z4: 0, Z5: 0, STR: 30 });
  expect(v.unallocated).toBe(10);
  expect(Object.values(v.components).reduce((a,b) => a+b,0)+v.unallocated).toBe(v.total);
  expect(v.coveredDays).toBe(2);
  expect(v.complete).toBe(false);
});

it("distinguishes unknown future time from a complete rest period", () => {
  expect(microcycleVolume(plan, "2026-10-07", "2026-10-13").available).toBe(false);
  expect(microcycleVolume(plan, "2026-10-01", "2026-10-01").available).toBe(false);
  const rest = microcycleVolume(plan, "2026-09-30", "2026-09-30");
  expect(rest.available && rest.complete).toBe(true);
  expect(rest.total).toBe(0);
});

it("does not report inconsistent block totals as valid component time", () => {
  const bad = {days:[{ date: "2026-09-29", sessions: [{ ...first, total_minutes: 1 }] } as DraftDay]} as PlanningDraft;
  expect(microcycleVolume(bad,"2026-09-29","2026-09-29").available).toBe(false);
});

it("switches between exact period targets and proposal time, showing missing future days", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const box = document.createElement("div"), root = createRoot(box);
  const weeks = ["2026-09-29", "2026-10-07"].map(start => ({ start_date: start,
    end_date: start === "2026-09-29" ? "2026-10-05" : "2026-10-13", accents: ["Z3"],
    volume_budget_minutes: 420, components: {Z3:{target_period_q:75.9, target_period_effective:294.9}} }));
  try {
    await act(async () => root.render(<MicrocycleVolumes weeks={weeks} sessions={plan} status="Предложение за преглед"/>));
    expect(box.textContent).toContain("1:15:54");
    expect(box.textContent).not.toContain("294");
    expect(box.querySelectorAll("table")).toHaveLength(1);
    expect(box.textContent).not.toContain("7:00:00");
    await act(async () => [...box.querySelectorAll("button")].find(b=>b.textContent==="Време от съставените сесии")!.click());
    expect(box.textContent).toContain("Предложение за преглед");
    expect(box.textContent).toContain("1:10:00");
    expect(box.textContent).toContain("0:20:00");
    expect(box.textContent).toContain("2 / 7 дни · частичен обем");
    expect(box.textContent).toContain("Няма съставени сесии");
    expect(box.textContent).not.toContain("7:00:00");
  } finally { await act(async () => root.unmount()); vi.unstubAllGlobals(); }
});
