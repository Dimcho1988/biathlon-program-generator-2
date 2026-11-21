// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { TrainabilityHistoryView } from "../components/trainability-history";
import { trainabilityFixture } from "../lib/trainability-fixture";

it("selects sessions through the lower cursor and recalculates the selected window", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<TrainabilityHistoryView history={trainabilityFixture}/>));
    const originalPath = container.querySelector("path[data-trend]")!.getAttribute("d");
    await act(async () => (container.querySelector('.index-channels input') as HTMLInputElement).click());
    expect(container.querySelectorAll("path[data-trend]")).toHaveLength(1);
    expect(container.querySelector("path[data-trend]")!.getAttribute("d")).not.toBe(originalPath);
    const slider = container.querySelector('input[type="range"]') as HTMLInputElement;
    const latest = slider.getAttribute("aria-valuetext");
    expect(container.querySelectorAll("path[data-trend]")).toHaveLength(1);
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(slider, "1");
      slider.dispatchEvent(new Event("input", { bubbles: true }));
      slider.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(slider.getAttribute("aria-valuetext")).not.toBe(latest);
    expect(container.querySelector(".index-cursor-values")!.textContent).toContain("Общ среден тренд");
    const select = container.querySelector(".index-trend-controls select") as HTMLSelectElement;
    await act(async () => { select.value = "7"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    expect(container.textContent).toContain("изглаждане 7 дни");
    await act(async () => (container.querySelector('.index-trend-controls input') as HTMLInputElement).click());
    expect(container.querySelectorAll("path[data-trend]")).toHaveLength(0);
  } finally { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); }
});
