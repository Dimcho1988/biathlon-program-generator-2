// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IndividualLearningPanel } from "../components/individual-learning-panel";
import { parseIndividualLearningReport } from "../lib/individual-learning";
import examples from "../lib/learning-examples.json";

let root: Root, container: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });

it("validates generated examples and keeps selecting them separate from the real plan", async () => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
  expect(examples.length).toBeGreaterThanOrEqual(3);
  for (const example of examples) expect(parseIndividualLearningReport(example.report)).not.toBeNull();
  const original = JSON.stringify(examples[0].report);
  await act(async () => root.render(<IndividualLearningPanel plan={{ individual_learning: examples[0].report }}/>));
  const panel = container.querySelector<HTMLDetailsElement>('[aria-label="Самообучение на плана"]')!;
  const liveSummary = panel.querySelector("summary")!.textContent;
  expect(panel.open).toBe(false);
  expect(container.querySelector("select")).toBeNull();
  const preview = [...container.querySelectorAll<HTMLDetailsElement>("details")].find(item => item.querySelector("summary")?.textContent === "Учебни примери · не са твоята програма")!;
  await act(async () => { preview.open = true; preview.dispatchEvent(new Event("toggle")); });
  expect(preview.textContent).toContain("Примерни данни · не променят програмата.");
  const select = preview.querySelector("select")!;
  for (const example of examples) {
    await act(async () => { select.value = example.id; select.dispatchEvent(new Event("change", { bubbles: true })); });
    const region = preview.querySelector('[aria-label="Пример на самообучението"]')!;
    expect(region.textContent).toContain(example.report.summary);
    expect(region.querySelectorAll("tbody tr")).toHaveLength(6);
    expect(panel.querySelector("summary")!.textContent).toBe(liveSummary);
  }
  expect(preview.querySelector("form")).toBeNull();
  expect(fetchMock).not.toHaveBeenCalled();
  expect(JSON.stringify(examples[0].report)).toBe(original);
});
