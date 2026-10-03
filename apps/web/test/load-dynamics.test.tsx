// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LoadDynamics } from "../components/load-dynamics";
import { LoadSourceChart } from "../components/load-source-chart";
import { loadHistoryFixture } from "../lib/fixture";
import { loadComparison, speedEquivalentWindow } from "../lib/load-dynamics";
import type { SpeedLoad } from "../lib/speed-load";

const history = loadHistoryFixture;
const fixture = (): SpeedLoad => ({
  schema_version: "speed-load-history-v1", model_version: "independent-speed-load-causal-ti-v1",
  status: "PARTIAL", sport: null, sports: ["Run", "Ride"], source_generation_id: "g", source_revision: 1,
  start_date: history.period_start, end_date: history.period_end,
  load_role: "PARALLEL_ESTIMATE_NOT_ADDED_TO_HR", mapping_policy: "SAME_SPORT_PRIOR_40_DAYS_EXCLUDING_CURRENT_DAY",
  recorded_minutes: 400, classified_minutes: 300, coverage_percent: 75,
  zones: Array.from({ length: 5 }, (_, i) => ({ zone: `Z${i + 1}`, minutes: 60, equivalent_minutes: 40 * (i + 1),
    effective_load: 4000, e7_daily: 100, e40_daily: 80, ratio_7_40: 1.2 })),
  daily: history.daily.map(row => ({ date: row.date, zone: row.zone, equivalent_minutes: Number(row.zone[1]),
    effective_load: 100, ratio_7_40: 1.2 })),
  sport_indices: [], activities: [], warnings: [],
});

let root: Root, container: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });
const render = async (athleteId = "A", generation = "g", revision = 1) => {
  await act(async () => root.render(<LoadDynamics athleteId={athleteId} generation={generation} revision={revision} history={history}/>));
};
const click = async (label: string) => {
  const button = [...container.querySelectorAll("button")].find(node => node.textContent === label);
  expect(button, label).toBeTruthy();
  await act(async () => button!.click());
};

it("switches cards and both charts between real independent sources, fetching only on demand", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => fixture() });
  vi.stubGlobal("fetch", fetcher);
  await render();
  expect(fetcher).not.toHaveBeenCalled();
  expect(container.textContent).toContain("Реално → приравнено → ефективно");
  await click("По скорост");
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(container.textContent).toContain("Скоростно покритие: 75%");
  const z1 = container.querySelector('[aria-label="Показатели по скоростни зони"] [aria-label="Z1"]')!;
  expect(z1.textContent).toContain("Q · 7 дни0:07:00");
  expect(z1.textContent).toContain("E7 · на ден100");
  expect(container.querySelectorAll('path[data-source="speed"]')).toHaveLength(10);
  expect(container.textContent).not.toContain("Реално → приравнено → ефективно");
  await click("Сравнение");
  expect(container.querySelectorAll('path[data-source="hr"]')).toHaveLength(10);
  expect(container.querySelectorAll('path[data-source="speed"][stroke-dasharray]')).toHaveLength(10);
  expect(container.querySelector('[aria-label="Сравнение на показателите по зони"]')).not.toBeNull();
  await click("По пулс");
  expect(container.textContent).toContain("Реално → приравнено → ефективно");
  await click("По скорост");
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("deduplicates in-flight switches and allows returning to HR while the speed request is running", async () => {
  let resolve!: (value: unknown) => void;
  const fetcher = vi.fn().mockReturnValue(new Promise(done => { resolve = done; }));
  vi.stubGlobal("fetch", fetcher);
  await render(); await click("По скорост"); await click("Сравнение");
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(container.textContent).toContain("Изчисляване на Q");
  await click("По пулс");
  expect(container.textContent).toContain("Реално → приравнено → ефективно");
  await act(async () => resolve({ ok: true, json: async () => fixture() }));
  expect(container.querySelectorAll('path[data-source="speed"]')).toHaveLength(0);
  await click("Сравнение");
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("recovers from an API failure and refuses a response from another generation", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce({ ok: false })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ ...fixture(), source_generation_id: "other" }) })
    .mockResolvedValue({ ok: true, json: async () => fixture() });
  vi.stubGlobal("fetch", fetcher);
  await render(); await click("По скорост");
  expect(container.textContent).toContain("временно не е достъпен");
  await click("Опитай отново");
  expect(container.textContent).toContain("Презареди страницата");
  expect(container.querySelector('[aria-label="Показатели по скоростни зони"]')).toBeNull();
  await click("Опитай отново");
  expect(container.querySelector('[aria-label="Показатели по скоростни зони"]')).not.toBeNull();
});

it("resets cached views on profile or revision changes and aborts outstanding requests", async () => {
  let resolve!: (value: unknown) => void;
  const fetcher = vi.fn().mockReturnValue(new Promise(done => { resolve = done; }));
  vi.stubGlobal("fetch", fetcher);
  await render(); await click("По скорост");
  const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
  await render("B", "new", 2);
  expect(signal.aborted).toBe(true);
  await act(async () => resolve({ ok: true, json: async () => fixture() }));
  expect(container.textContent).not.toContain("Скоростно покритие");
  expect(container.querySelector('button[aria-pressed="true"]')!.textContent).toBe("По пулс");
  fetcher.mockResolvedValue({ ok: true, json: async () => ({ ...fixture(), source_generation_id: "new", source_revision: 2 }) });
  await click("По скорост");
  expect(container.textContent).toContain("Скоростно покритие: 75%");
  await render("B", "new", 3);
  expect(container.textContent).not.toContain("Скоростно покритие");
});

it("does not present unavailable speed load as zero or compare different analysis dates", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...fixture(), status: "UNAVAILABLE", classified_minutes: 0, coverage_percent: 0,
    zones: fixture().zones.map(zone => ({ ...zone, ratio_7_40: null })) }) });
  vi.stubGlobal("fetch", fetcher);
  await render(); await click("По скорост");
  expect(container.textContent).toContain("Липсващите стойности не означават нулево натоварване");
  expect(container.querySelector('[aria-label="Показатели по скоростни зони"]')).toBeNull();
  fetcher.mockResolvedValue({ ok: true, json: async () => ({ ...fixture(), end_date: "2026-06-21" }) });
  await click("Обнови скоростния отчет"); await click("Сравнение");
  expect(container.textContent).toContain("Двата отчета са към различни дати");
  expect(container.querySelectorAll('path[data-source]')).toHaveLength(0);
});

it("aggregates Q over inclusive calendar days and exposes incomplete windows without substituting E", () => {
  const speed = fixture();
  expect(speedEquivalentWindow(speed, 7).totals!.Z1).toBe(7);
  expect(speedEquivalentWindow(speed, 40).totals!.Z1).toBe(40);
  expect(speedEquivalentWindow(speed, 40).weekly!.Z1).toBe(7);
  const broken = { ...speed, daily: speed.daily.filter(row => !(row.date === speed.end_date && row.zone === "Z1")) };
  expect(speedEquivalentWindow(broken, 7).totals).toBeNull();
  const duplicate = { ...speed, daily: [...speed.daily, speed.daily.at(-1)!] };
  expect(speedEquivalentWindow(duplicate, 7).totals).toBeNull();
  const aligned = loadComparison({ ...history, period_start: "2026-05-01" }, speed);
  expect(aligned.start).toBe(speed.start_date);
  expect(aligned.hrLong!.days).toBe(40);
});

it("uses true calendar spacing and breaks plotted lines across missing dates", async () => {
  await act(async () => root.render(<LoadSourceChart metric="ratio" start="2026-06-01" end="2026-06-05" series={[{ source: "speed", rows: [
    { date: "2026-05-31", zone: "Z1", ratio: 99, effective: 1 },
    { date: "2026-06-01", zone: "Z1", ratio: 1, effective: 1 },
    { date: "2026-06-02", zone: "Z1", ratio: 1, effective: 1 },
    { date: "2026-06-04", zone: "Z1", ratio: 1, effective: 1 },
    { date: "2026-06-05", zone: "Z1", ratio: null, effective: null },
  ] }]}/>));
  const path = container.querySelector('path[data-zone="Z1"]')!.getAttribute("d")!;
  expect(path.match(/M/g)).toHaveLength(2);
  expect(path.match(/L/g)).toHaveLength(1);
  expect(path).toContain("M48,"); expect(path).toContain("L262,"); expect(path).toContain("M690,");
});
