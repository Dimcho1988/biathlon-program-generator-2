// @vitest-environment jsdom
import {act} from "react";
import {createRoot, type Root} from "react-dom/client";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {PreparedViewRetry} from "../components/prepared-view-retry";

const {refresh, router} = vi.hoisted(() => {const refresh=vi.fn();return {refresh,router:{refresh}};});
vi.mock("next/navigation", () => ({useRouter: () => router}));
let root: Root, container: HTMLDivElement;
beforeEach(() => {
  vi.useFakeTimers();vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);refresh.mockClear();
  container=document.createElement("div");document.body.append(container);root=createRoot(container);
});
afterEach(async () => {
  await act(async()=>root.unmount());container.remove();vi.clearAllTimers();vi.useRealTimers();vi.unstubAllGlobals();
});

it("provides a visible retry, bounds automatic polling and lets the user restart reads", async () => {
  await act(async()=>root.render(<PreparedViewRetry/>));
  expect(container.querySelector("button")?.textContent).toBe("Провери отново");
  await act(async()=>vi.advanceTimersByTimeAsync(120_000));
  expect(refresh).toHaveBeenCalledTimes(7);
  expect(container.textContent).toContain("Подготовката отнема повече време от обичайното");
  await act(async()=>vi.advanceTimersByTimeAsync(120_000));
  expect(refresh).toHaveBeenCalledTimes(7);
  await act(async()=>container.querySelector("button")!.click());
  expect(refresh).toHaveBeenCalledTimes(8);
  expect(container.textContent).not.toContain("Подготовката отнема повече време от обичайното");
  await act(async()=>vi.advanceTimersByTimeAsync(5000));
  expect(refresh).toHaveBeenCalledTimes(9);
});

it("does not reload hidden pages and cancels scheduled reads on unmount", async () => {
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  await act(async()=>root.render(<PreparedViewRetry/>));
  await act(async()=>vi.advanceTimersByTimeAsync(50_000));
  expect(refresh).not.toHaveBeenCalled();
  vi.restoreAllMocks();
  await act(async()=>vi.advanceTimersByTimeAsync(20_000));
  expect(refresh).toHaveBeenCalledTimes(1);
  await act(async()=>root.render(null));
  await act(async()=>vi.advanceTimersByTimeAsync(120_000));
  expect(refresh).toHaveBeenCalledTimes(1);
});
