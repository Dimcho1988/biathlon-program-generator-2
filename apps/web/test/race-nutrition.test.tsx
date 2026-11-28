// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import type { RacePreparation } from "../lib/energy";
const refresh = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
vi.mock("next/link", () => ({ default: ({ children, ...props }: React.ComponentProps<"a">) => <a {...props}>{children}</a> }));
import { RaceNutrition, NutritionReminder } from "../components/race-nutrition";
const base = { kcal:3000, protein_g:112, protein_g_per_kg:1.6, energy_kcal_per_kg:3000/70,
  protein_basis:"SEVEN_DAY_ENERGY_RULE" as const, carbohydrate_g:450, fat_g:83.3, allocation_note:"Базова цел" };
const p: RacePreparation = { event_id:"race", event_name:"Маратон", race_date:"2026-11-25", preview_hash:"a".repeat(64),
  status:"PENDING", revision:0, can_accept:true, reason:null, late:false,
  days:[{day:"2026-11-23", base_targets:base, targets:{...base,kcal:4060,carbohydrate_g:700,fat_g:90.2}, weight_kg:70}] };
afterEach(() => vi.unstubAllGlobals());
it("shows dated before/after amounts and requires an explicit choice", () => {
  const html = renderToStaticMarkup(<RaceNutrition proposals={[p]} canEdit />);
  expect(html).toContain("2026-11-23"); expect(html).toContain("450"); expect(html).toContain("700");
  expect(html).toContain("Приеми за показаните дни"); expect(html).toContain("Запази обичайния режим");
  expect(renderToStaticMarkup(<RaceNutrition proposals={[p]} canEdit={false} />)).not.toContain("<button");
});
it("posts only the reviewed proposal identity after a click", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({saved:true}), {status:200})); vi.stubGlobal("fetch",fetcher);
  const element=document.createElement("div"), root=createRoot(element);
  await act(async()=>root.render(<RaceNutrition proposals={[p]} canEdit />));
  expect(fetcher).not.toHaveBeenCalled();
  await act(async()=>element.querySelector("button")!.click());
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({event_id:"race", preview_hash:p.preview_hash, expected_revision:0, action:"ACCEPTED"});
  expect(element.textContent).toContain("Зареждането е прието"); expect(refresh).toHaveBeenCalled();
  await act(async()=>root.unmount());
});
it("reminder cannot display a late response from a previously selected athlete", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  let finish!: (r:Response)=>void;
  vi.stubGlobal("fetch",vi.fn(()=>new Promise<Response>(resolve=>{finish=resolve;})));
  const element=document.createElement("div"), root=createRoot(element);
  await act(async()=>root.render(<NutritionReminder />));
  await act(async()=>window.dispatchEvent(new Event("onflows-scope-changed")));
  await act(async()=>finish(new Response(JSON.stringify({events:[{event_id:"old",name:"Old athlete event",race_date:"2026-11-25"}]}),{status:200})));
  expect(element.textContent).toBe("");
  await act(async()=>root.unmount());
});
