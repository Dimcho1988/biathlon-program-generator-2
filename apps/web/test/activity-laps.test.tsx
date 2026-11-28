// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ActivityLaps, lapDuration } from "../components/activity-laps";
import { activityDetailFixture, activitySeriesFixture } from "../lib/activities";

let root: Root, container: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });

const activity = {...activityDetailFixture, sport:"NordicSki", elapsed_time_min: 1, device_laps_status: "available", intervals: [
  {source:"device", start_time_s:0, end_time_s:30, timer_time_s:29.9, elapsed_time_s:30, distance_m:0, average_hr_bpm:140, average_hrmod_bpm:152, average_speed_kmh:0, average_vflat_kmh:0},
  {source:"device", start_time_s:30, end_time_s:60, elapsed_time_s:30, distance_m:200, average_hr_bpm:150, average_hrmod_bpm:null, average_speed_kmh:24, average_vflat_kmh:null},
  {source:"intervals", name:"Усилие", start_time_s:10, end_time_s:45, elapsed_time_s:35, average_hr_bpm:145},
]};
const series = {...activitySeriesFixture, series:Array.from({length:61}, (_,i) => ({timestamp:null,elapsed_s:i,hr_bpm:140+i/2,hrmod_bpm:150+i/2,speed_kmh:18,vflat_kmh:20,altitude_m:null,grade_pct:null,quality_flags:[]}))};

it("defaults to original device laps, preserves zero values and shows unavailable models as missing", async () => {
  await act(async () => root.render(<ActivityLaps activity={activity} series={series}/>));
  const rows = container.querySelectorAll("tbody tr");
  expect(rows.length).toBe(2);
  expect(rows[0].children[1].textContent).toBe("0:00:29,9");
  expect(rows[0].children[2].textContent).toBe("0");
  expect(rows[0].children[5].textContent).toContain("0");
  expect(rows[1].children[4].textContent).toBe("—");
  expect(container.textContent).not.toContain("Обиколките още не са изтеглени");
});

it("selects an original lap, zooms both charts to the same time range and resets on source change", async () => {
  await act(async () => root.render(<ActivityLaps activity={activity} series={series}/>));
  await act(async () => (container.querySelector('button[aria-label="Обиколка 2"]') as HTMLButtonElement).click());
  expect(container.querySelector(".lap-selection h3")?.textContent).toBe("Обиколка 2");
  const charts = [...container.querySelectorAll(".canonical-chart")];
  charts.forEach(chart => {
    const labels = [...chart.querySelectorAll("svg .chart-label")].map(el=>el.textContent);
    expect(labels).toContain("0:00:30"); expect(labels).toContain("0:01:00");
    expect(labels).not.toContain("0:00:00");
  });
  await act(async () => (container.querySelectorAll(".lap-source-controls button")[1] as HTMLButtonElement).click());
  expect(container.querySelectorAll("tbody tr").length).toBe(1);
  expect(container.querySelector(".lap-selection")).toBeNull();
  expect(container.textContent).toContain("Усилие");
});

it("keeps legacy interval summaries visible and explains how to obtain original laps", async () => {
  await act(async () => root.render(<ActivityLaps activity={{...activityDetailFixture, intervals:[{name:"Work",elapsed_time_s:120,average_hr_bpm:150}]}} series={null}/>));
  expect(container.querySelectorAll("tbody tr").length).toBe(1);
  expect(container.textContent).toContain("Обиколките още не са изтеглени");
  expect(container.textContent).toContain("150");
});

it("rounds fractional watch durations without inventing a second at minute boundaries", () => {
  expect(lapDuration(59.96)).toBe("0:01:00");
  expect(lapDuration(384.7)).toBe("0:06:24,7");
  expect(lapDuration(0)).toBe("0:00:00");
  expect(lapDuration(null)).toBe("—");
});
