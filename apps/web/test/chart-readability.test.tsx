// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MetricChart } from "../components/metric-chart";
import { chartDomain, chartPath, chartTicks, nearestChartPoint, spacedChartTicks } from "../lib/chart-geometry";

let root: Root, container: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("handles flat and absent signals, preserves negative values, and uses readable time ticks", () => {
  expect(chartDomain([null,NaN])).toEqual([0,1]);
  const [low,high] = chartDomain([140,140]); expect(low).toBeLessThan(140); expect(high).toBeGreaterThan(140);
  expect(chartDomain([0,20],true)[0]).toBe(0);
  expect(chartDomain([-5,5],true)[0]).toBeLessThan(-5);
  expect(chartTicks(0,11000,5,true)).toEqual([0,3600,7200,10800]);
  expect(spacedChartTicks([1,2,3,4],v=>v*30,70)).toEqual([1,4]);
});

it("keeps nulls, long pauses, and composition changes as gaps on the true time scale", () => {
  const points = [{x:0,y:120},{x:1,y:121},{x:2,y:null},{x:3,y:123},{x:20,y:124},{x:21,y:125,breakBefore:true}];
  const path = chartPath(points,x=>x*10,y=>y,5);
  expect(path.match(/M/g)).toHaveLength(4); expect(path.match(/L/g)).toHaveLength(1);
  expect(path).toContain("M200.00,124.00"); expect(path).not.toContain("NaN");
  expect(nearestChartPoint(points,19)).toBe(4);
});

const data = [{key:"HR",label:"Пулс",color:"#df4e5b",points:[{x:0,y:0},{x:30,y:null},{x:120,y:140}]}];
it("shows exact selected values and explicit missing observations using the keyboard-accessible cursor", async () => {
  await act(async () => root.render(<MetricChart title="Пулс" unit="bpm" xKind="duration" series={data}/>));
  const slider = container.querySelector('input[type="range"]') as HTMLInputElement;
  expect(container.querySelector('.metric-chart-readout')?.textContent).toContain("140 bpm");
  for (const [index,value] of [[1,"Няма данни"],[0,"0 bpm"]] as const) {
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!.call(slider,String(index));
      slider.dispatchEvent(new Event("input",{bubbles:true}));
    });
    expect(container.querySelector('.metric-chart-readout')?.textContent).toContain(value);
  }
  expect(slider.getAttribute('aria-valuetext')).toBe("0:00:00");
});

it("measures a chart mounted after loading, keeps phone labels at the actual width, and disconnects observers", async () => {
  const disconnect = vi.fn(); let resized: (()=>void) | undefined;
  vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockReturnValue({width:300} as DOMRect);
  vi.stubGlobal('ResizeObserver',class { constructor(callback: ()=>void) { resized=callback; } observe() {} disconnect=disconnect; });
  await act(async () => root.render(<p>Зареждане</p>));
  await act(async () => root.render(<MetricChart title="Пулс" unit="bpm" series={data} xKind="duration"/>));
  expect(container.querySelector('svg')?.getAttribute('viewBox')).toBe("0 0 300 284");
  expect(container.querySelectorAll('.metric-tick')).toHaveLength(3);
  vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockReturnValue({width:640} as DOMRect);
  await act(async () => resized?.());
  expect(container.querySelector('svg')?.getAttribute('viewBox')).toBe("0 0 640 284");
  await act(async () => root.render(<p>Готово</p>));
  expect(disconnect).toHaveBeenCalled();
});

it("keeps the first and last weekly columns separate and inside the plotting area", async () => {
  const series = ["A","B"].map(key=>({key,label:key,color:"#07888d",kind:"bar" as const,points:[{x:0,y:3600},{x:7,y:7200}]}));
  await act(async () => root.render(<MetricChart title="Обем" unit="Време" yKind="duration" zero series={series}/>));
  const bars = [...container.querySelectorAll('rect')].map(rect=>({x:Number(rect.getAttribute('x')),width:Number(rect.getAttribute('width'))}));
  expect(bars[2].x).toBeGreaterThanOrEqual(bars[0].x+bars[0].width);
  expect(bars[3].x).toBeGreaterThanOrEqual(bars[1].x+bars[1].width);
  expect(Math.max(...bars.map(bar=>bar.x+bar.width))).toBeLessThanOrEqual(620);
});
