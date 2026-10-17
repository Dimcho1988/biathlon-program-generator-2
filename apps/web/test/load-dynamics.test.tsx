// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LoadDynamics } from "../components/load-dynamics";
import { LoadSourceChart } from "../components/load-source-chart";
import { loadHistoryFixture } from "../lib/fixture";
import { loadComparison, speedEquivalentWindow } from "../lib/load-dynamics";
import { shiftDate } from "../lib/dashboard-periods";
import type { LoadHistory } from "../lib/load-history";
import type { SpeedLoad } from "../lib/speed-load";
import { clearSpeedLoadClientCache } from "../lib/speed-load-client";

const history = loadHistoryFixture;
const fixture = (source = history): SpeedLoad => ({
  schema_version: "speed-load-history-v1", model_version: "independent-speed-load-causal-ti-v1",
  status: "PARTIAL", sport: null, sports: ["Run", "Ride"], source_generation_id: "g", source_revision: 1,
  start_date: source.period_start, end_date: source.period_end,
  load_role: "PARALLEL_ESTIMATE_NOT_ADDED_TO_HR", mapping_policy: "SAME_SPORT_PRIOR_40_DAYS_EXCLUDING_CURRENT_DAY",
  recorded_minutes: 400, classified_minutes: 300, coverage_percent: 75,
  zones: Array.from({ length: 5 }, (_, i) => ({ zone: `Z${i + 1}`, minutes: 60, equivalent_minutes: 40 * (i + 1),
    effective_load: 4000, e7_daily: 100, e40_daily: 80, ratio_7_40: 1.2 })),
  daily: source.daily.map(row => ({ date: row.date, zone: row.zone, equivalent_minutes: Number(row.zone[1]),
    effective_load: 100, ratio_7_40: 1.2 })),
  sport_indices: [], activities: [], warnings: [],
});

// Synthetic dates preserve the fixture's relative workout days and values.
const historyEndingAt = (end: string, days = 40): LoadHistory => {
  const offset = Math.round((Date.parse(end) - Date.parse(history.period_end)) / 86_400_000);
  const start = shiftDate(end, 1 - days);
  return { ...history, period_start: start, period_end: end,
    daily: history.daily.map(row => ({ ...row, date: shiftDate(row.date, offset) })).filter(row => row.date >= start),
    activities: history.activities.map(row => ({ ...row, date: shiftDate(row.date, offset) })).filter(row => row.date >= start),
  };
};

const requestedDates = (url: string) => new URL(url, "https://onflows.test").searchParams;

let root: Root, container: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); clearSpeedLoadClientCache(); vi.unstubAllGlobals(); vi.useRealTimers(); });
const render = async (athleteId = "A", generation = "g", revision = 1, source: LoadHistory | null = history, cacheScope?: string) => {
  await act(async () => root.render(<LoadDynamics athleteId={athleteId} generation={generation} revision={revision} history={source} cacheScope={cacheScope}/>));
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
  expect(requestedDates(fetcher.mock.calls[0][0]).get("as_of")).toBe(history.period_end);
  expect(requestedDates(fetcher.mock.calls[0][0]).has("period_start")).toBe(false);
  expect(requestedDates(fetcher.mock.calls[0][0]).has("period_end")).toBe(false);
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

it("compares yesterday's HR snapshot at its analysis date even when today is later", async () => {
  vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(new Date("2026-10-07T09:00:00Z"));
  const older = historyEndingAt("2026-10-06", 90);
  const fetcher = vi.fn().mockImplementation(async (url: string) => {
    const dates = requestedDates(url);
    const end = dates.get("as_of") ?? "2026-10-07";
    return { ok: true, json: async () => ({ ...fixture(older),
      start_date: shiftDate(end, -39), end_date: end,
    }) };
  });
  vi.stubGlobal("fetch", fetcher);
  await render("A", "g", 1, older);
  expect(fetcher).not.toHaveBeenCalled();
  await click("Сравнение");
  const dates = requestedDates(fetcher.mock.calls[0][0]);
  expect(dates.get("as_of")).toBe("2026-10-06");
  expect(dates.has("period_start")).toBe(false);
  expect(dates.has("period_end")).toBe(false);
  expect(container.textContent).not.toContain("Двата отчета са към различни дати");
  expect(container.querySelector('[aria-label="Сравнение на показателите по зони"]')).not.toBeNull();
  expect(container.querySelectorAll('path[data-source="hr"]')).toHaveLength(10);
  expect(container.querySelectorAll('path[data-source="speed"]')).toHaveLength(10);
});

it("refreshes at the same snapshot date and bypasses its cached validator", async () => {
  const older = historyEndingAt("2026-10-06");
  const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify(fixture(older)), {
    headers: { ETag: `"${"a".repeat(64)}"` },
  }));
  vi.stubGlobal("fetch", fetcher);
  await render("A", "g", 1, older, "account-athlete-dated-refresh");
  await click("Сравнение"); await click("Обнови скоростния отчет");
  expect(fetcher).toHaveBeenCalledTimes(2);
  for (const [url] of fetcher.mock.calls) {
    expect(requestedDates(url).get("as_of")).toBe("2026-10-06");
    expect(requestedDates(url).has("period_start")).toBe(false);
    expect(requestedDates(url).has("period_end")).toBe(false);
  }
  expect(fetcher.mock.calls[1][1].headers).toBeUndefined();
  expect(container.querySelector('[aria-label="Сравнение на показателите по зони"]')).not.toBeNull();
});

it.each(["start", "end"])("resets the view and aborts its pending request when HR period %s changes without a new revision", async boundary => {
  const older = historyEndingAt("2026-10-06");
  const changed = boundary === "end" ? historyEndingAt("2026-10-07") : historyEndingAt("2026-10-06", 5);
  let resolve!: (value: unknown) => void;
  const fetcher = vi.fn().mockReturnValueOnce(new Promise(done => { resolve = done; }))
    .mockResolvedValue({ ok: true, json: async () => fixture(changed) });
  vi.stubGlobal("fetch", fetcher);
  await render("A", "g", 1, older); await click("Сравнение");
  const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
  await render("A", "g", 1, changed);
  expect(signal.aborted).toBe(true);
  expect(container.querySelector('button[aria-pressed="true"]')!.textContent).toBe("По пулс");
  await act(async () => resolve({ ok: true, json: async () => fixture(older) }));
  expect(container.textContent).not.toContain("Скоростно покритие");
  await click("Сравнение");
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(requestedDates(fetcher.mock.calls[1][0]).get("as_of")).toBe(changed.period_end);
  expect(requestedDates(fetcher.mock.calls[1][0]).has("period_start")).toBe(false);
  expect(requestedDates(fetcher.mock.calls[1][0]).has("period_end")).toBe(false);
  expect(container.querySelector('[aria-label="Сравнение на показателите по зони"]')).not.toBeNull();
});

it("does not override the backend's short speed history with an explicit period", async () => {
  const short = historyEndingAt("2026-10-06", 5);
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => fixture(short) });
  vi.stubGlobal("fetch", fetcher);
  await render("A", "g", 1, short); await click("Сравнение");
  const dates = requestedDates(fetcher.mock.calls[0][0]);
  expect(dates.get("as_of")).toBe("2026-10-06");
  expect(dates.has("period_start")).toBe(false);
  expect(dates.has("period_end")).toBe(false);
  expect(container.textContent).toContain("5/7 и 5/40 календарни дни");
  expect(container.querySelector('[aria-label="Сравнение на показателите по зони"]')).not.toBeNull();
});

it("keeps standalone speed analysis available when the HR history is absent", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => fixture() });
  vi.stubGlobal("fetch", fetcher);
  await render("A", "g", 1, null); await click("По скорост");
  expect(requestedDates(fetcher.mock.calls[0][0]).has("period_start")).toBe(false);
  expect(requestedDates(fetcher.mock.calls[0][0]).has("period_end")).toBe(false);
  expect(requestedDates(fetcher.mock.calls[0][0]).has("as_of")).toBe(false);
  expect(container.querySelector('[aria-label="Показатели по скоростни зони"]')).not.toBeNull();
  await click("Сравнение");
  expect(container.textContent).toContain("За сравнение е нужна и пулсова история");
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

it("does not present unavailable speed load as zero or accept a response at another analysis date", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...fixture(), status: "UNAVAILABLE", classified_minutes: 0, coverage_percent: 0,
    zones: fixture().zones.map(zone => ({ ...zone, ratio_7_40: null })) }) });
  vi.stubGlobal("fetch", fetcher);
  await render(); await click("По скорост");
  expect(container.textContent).toContain("Липсващите стойности не означават нулево натоварване");
  expect(container.querySelector('[aria-label="Показатели по скоростни зони"]')).toBeNull();
  fetcher.mockResolvedValue({ ok: true, json: async () => ({ ...fixture(), end_date: "2026-06-21" }) });
  await click("Обнови скоростния отчет"); await click("Сравнение");
  expect(container.textContent).toContain("Скоростният отчет не съответства");
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
  expect(loadComparison(history, { ...speed, end_date: shiftDate(history.period_end, 1) }).error)
    .toContain("Двата отчета са към различни дати");
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
  const coordinates = [...path.matchAll(/[ML]([\d.]+),/g)].map(match => Number(match[1]));
  expect(coordinates[1] - coordinates[0]).toBeGreaterThan(0);
  expect(coordinates[2] - coordinates[1]).toBeCloseTo(2 * (coordinates[1] - coordinates[0]));
});
