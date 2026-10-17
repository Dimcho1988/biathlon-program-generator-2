// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { InstallApp } from "../components/install-app";

let root: Root, container: HTMLDivElement, standalone: boolean;
const listeners = new Set<() => void>();
beforeEach(() => {
  standalone = false; listeners.clear();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("navigator", { userAgent: "Mozilla/5.0 Windows Chrome/150.0 Safari/537.36", platform: "Win32", maxTouchPoints: 0 });
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation(() => ({
    get matches() { return standalone; },
    addEventListener: (_event: string, callback: () => void) => listeners.add(callback),
    removeEventListener: (_event: string, callback: () => void) => listeners.delete(callback),
  })));
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });
const render = () => act(async () => root.render(<InstallApp/>));
const click = () => act(async () => container.querySelector("button")!.click());
function nativePrompt(outcome: "accepted" | "dismissed" = "accepted") {
  const event = new Event("beforeinstallprompt", { cancelable: true });
  const prompt = vi.fn().mockResolvedValue(undefined);
  Object.assign(event, { prompt, userChoice: Promise.resolve({ outcome, platform: "web" }) });
  return { event, prompt };
}

it("uses manual browser instructions when no native prompt is available, without promising offline access", async () => {
  await render();
  expect(container.querySelector("button")!.textContent).toContain("Добави onFlows");
  await click();
  expect(container.querySelector("[hidden]")).toBeNull();
  expect(container.textContent).toContain("Инсталиране на onFlows");
  expect(container.textContent).toContain("се нуждае от интернет");
  await click(); expect(container.querySelector("[hidden]")).not.toBeNull();
});

it("shows the native prompt only after an explicit click and consumes each event once", async () => {
  await render(); const { event, prompt } = nativePrompt();
  await act(async () => { window.dispatchEvent(event); });
  expect(event.defaultPrevented).toBe(true); expect(prompt).not.toHaveBeenCalled();
  expect(container.querySelector("button")!.textContent).toBe("Инсталирай onFlows");
  await click(); expect(prompt).toHaveBeenCalledTimes(1);
  expect(container.textContent).toContain("Потвърждението е прието");
  expect(container.querySelector("section")).not.toBeNull(); // Accepted is not an installation proof.
  await click(); expect(prompt).toHaveBeenCalledTimes(1);
  await act(async () => { window.dispatchEvent(new Event("appinstalled")); });
  expect(container.textContent).toBe("");
});

it("offers manual installation after dismissal or a rejected native prompt", async () => {
  await render(); const dismissed = nativePrompt("dismissed");
  await act(async () => { window.dispatchEvent(dismissed.event); });
  await click(); expect(container.textContent).toContain("по-късно от менюто");
  const rejected = nativePrompt(); rejected.prompt.mockRejectedValue(new Error("Unavailable"));
  await act(async () => { window.dispatchEvent(rejected.event); });
  await click(); expect(container.textContent).toContain("Инсталирането не започна");
  expect(container.querySelector("button")!.disabled).toBe(false);
});

it("prevents duplicate clicks from prompting while the browser choice is pending", async () => {
  let choose!: (choice: { outcome: "dismissed"; platform: string }) => void;
  const native = nativePrompt();
  Object.assign(native.event, { userChoice: new Promise(resolve => { choose = resolve; }) });
  await render(); await act(async () => { window.dispatchEvent(native.event); });
  await click(); expect(container.querySelector("button")!.disabled).toBe(true);
  await click(); expect(native.prompt).toHaveBeenCalledTimes(1);
  await act(async () => { choose({ outcome: "dismissed", platform: "web" }); });
  expect(container.querySelector("button")!.disabled).toBe(false);
});

it.each([
  ["Mozilla/5.0 iPhone Safari/604.1", "iPhone", 1, "Споделяне → Добави към началния екран"],
  ["Mozilla/5.0 Macintosh Safari/605.1.15", "MacIntel", 5, "Споделяне → Добави към началния екран"],
  ["Mozilla/5.0 Macintosh Safari/605.1.15", "MacIntel", 0, "Файл → Добави към Dock"],
  ["Mozilla/5.0 Windows Firefox/150.0", "Win32", 0, "Firefox за Windows"],
  ["Unknown browser", "Unknown", 0, "На компютър отвори onFlows в Chrome или Edge"],
])("provides suitable manual instructions for %s", async (userAgent, device, maxTouchPoints, expected) => {
  vi.stubGlobal("navigator", { userAgent, platform: device, maxTouchPoints });
  await render(); await click(); expect(container.textContent).toContain(expected);
});

it("hides install actions in standalone mode, including the legacy iOS signal", async () => {
  standalone = true; await render(); expect(container.textContent).toBe("");
  standalone = false;
  await act(async () => { for (const listener of listeners) listener(); });
  expect(container.querySelector("button")).not.toBeNull();
  vi.stubGlobal("navigator", { userAgent: "iPhone", platform: "iPhone", maxTouchPoints: 1, standalone: true });
  await act(async () => { for (const listener of listeners) listener(); });
  expect(container.textContent).toBe("");
});

it("cleans up browser listeners after unmount", async () => {
  const remove = vi.spyOn(window, "removeEventListener");
  await render(); await act(async () => root.render(null));
  expect(remove.mock.calls.some(([event]) => event === "beforeinstallprompt")).toBe(true);
  expect(remove.mock.calls.some(([event]) => event === "appinstalled")).toBe(true);
  expect(listeners.size).toBe(0); remove.mockRestore();
});

it("keeps manual help usable when the browser lacks display-mode detection", async () => {
  vi.stubGlobal("matchMedia", undefined);
  await render(); await click();
  expect(container.textContent).toContain("се нуждае от интернет");
  expect(container.textContent).toContain("отделни сесии");
});
