// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PlanCheckHistory } from "../components/plan-check-history";
import { ManagementScope } from "../components/management-scope";

let box: HTMLDivElement;
let root: Root;
let request: ReturnType<typeof vi.fn<typeof fetch>>;
const event = (sequence: number, revision = 652) => ({
  id: `00000000-0000-4000-8000-${String(sequence).padStart(12, "0")}`,
  sequence, checked_revision: revision, operation: "CHECK_NO_CHANGE", recorded_at: "2026-10-19T11:00:00+03:00",
  reason: "Проверката приключи. Програмата остава без промяна.",
});
const page = (rows = [event(20)], next_before: number | null = null) => Response.json({ checks: rows, next_before });
const deferred = () => {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>(done => { resolve = done; });
  return { promise, resolve };
};
const render = async (refreshKey = 0, athleteAlias = "athlete-a") => act(async () => {
  root.render(<ManagementScope key={athleteAlias} athleteAlias={athleteAlias} actorUserId="coach"><PlanCheckHistory refreshKey={refreshKey} /></ManagementScope>);
});
const toggle = async (open: boolean) => act(async () => {
  const details = box.querySelector("details")!;
  details.open = open;
  details.dispatchEvent(new Event("toggle"));
});
const button = (label: string) => [...box.querySelectorAll("button")].find(item => item.textContent === label)!;
const click = async (label: string) => act(async () => button(label).click());
const rows = () => [...box.querySelectorAll("li")];

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("BroadcastChannel", undefined);
  request = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", request);
  box = document.createElement("div");
  root = createRoot(box);
});
afterEach(async () => {
  await act(async () => root.unmount());
  vi.unstubAllGlobals();
});

it("loads only on opening and shows human labels without internal identifiers", async () => {
  request.mockResolvedValueOnce(page());
  await render();
  expect(box.querySelector("summary")?.textContent).toBe("Проверки без промяна");
  expect(request).not.toHaveBeenCalled();
  await render(1);
  expect(request).not.toHaveBeenCalled();
  await toggle(true);
  expect(request).toHaveBeenCalledTimes(1);
  expect(request.mock.calls[0][0]).toBe("/api/athlete/management/checks?limit=10");
  const options = request.mock.calls[0][1]!;
  expect(options).toMatchObject({ method: "GET", cache: "no-store" });
  expect(new Headers(options.headers).get("X-Onflows-Athlete")).toBe("athlete-a");
  expect(new Headers(options.headers).get("X-Onflows-Actor")).toBe("coach");
  expect(box.textContent).toContain("Проверка без промяна · версия 652");
  expect(box.textContent).toContain(event(20).reason);
  expect(box.querySelector("time")?.getAttribute("datetime")).toBe(event(20).recorded_at);
  expect(box.textContent).not.toContain("CHECK_NO_CHANGE");
  expect(box.textContent).not.toContain(event(20).id);
  expect(box.textContent).not.toContain("athlete-a");
  expect(box.textContent).not.toContain("coach");
});

it("manually refreshes and fetches the latest first page when reopened", async () => {
  request.mockResolvedValueOnce(page([event(20, 652)], 20))
    .mockResolvedValueOnce(page([event(21, 653)], 21))
    .mockResolvedValueOnce(page([event(22, 654)]));
  await render();
  await toggle(true);
  await click("Обнови проверките");
  expect(rows()).toHaveLength(1);
  expect(box.textContent).toContain("версия 653");
  expect(box.textContent).not.toContain("версия 652");
  await toggle(false);
  await render(1);
  expect(request).toHaveBeenCalledTimes(2);
  await toggle(true);
  expect(request).toHaveBeenCalledTimes(3);
  expect(request.mock.calls.map(call => call[0])).toEqual(Array(3).fill("/api/athlete/management/checks?limit=10"));
  expect(box.textContent).toContain("версия 654");
  expect(box.textContent).not.toContain("версия 653");
});

it("loads older pages by cursor, deduplicates event IDs and resets pagination on refresh", async () => {
  request.mockResolvedValueOnce(page([event(30), event(29)], 29))
    .mockResolvedValueOnce(page([{ ...event(28), id: event(29).id }, event(27)], 27))
    .mockResolvedValueOnce(page([event(31, 653)]));
  await render();
  await toggle(true);
  await click("Още проверки");
  expect(request.mock.calls[1][0]).toBe("/api/athlete/management/checks?limit=10&before=29");
  expect(rows()).toHaveLength(3);
  await click("Обнови проверките");
  expect(request.mock.calls[2][0]).toBe("/api/athlete/management/checks?limit=10");
  expect(rows()).toHaveLength(1);
  expect(box.textContent).toContain("версия 653");
  expect(button("Още проверки")).toBeUndefined();
});

it("rejects an older page outside its requested cursor without losing the prior rows", async () => {
  request.mockResolvedValueOnce(page([event(20)], 20)).mockResolvedValueOnce(page([event(20)], 20))
    .mockResolvedValueOnce(page([event(19)]));
  await render();
  await toggle(true);
  await click("Още проверки");
  expect(rows()).toHaveLength(1);
  expect(box.querySelector('[role="alert"]')?.textContent).toContain("Невалидна следваща страница с проверки.");
  await click("Опитай отново");
  expect(request.mock.calls[2][0]).toBe("/api/athlete/management/checks?limit=10&before=20");
  expect(rows()).toHaveLength(2);
  expect(box.querySelector('[role="alert"]')).toBeNull();
});

it("returns to the latest page when a new receipt arrives after loading older pages", async () => {
  request.mockResolvedValueOnce(page([event(20)], 20)).mockResolvedValueOnce(page([event(19)], 19))
    .mockResolvedValueOnce(page([event(21, 653)]));
  await render();
  await toggle(true);
  await click("Още проверки");
  expect(rows()).toHaveLength(2);
  await render(1);
  expect(request.mock.calls[2][0]).toBe("/api/athlete/management/checks?limit=10");
  expect(rows()).toHaveLength(1);
  expect(box.textContent).toContain("версия 653");
});

it("refreshes for a new explicit receipt while open without creating requests while closed", async () => {
  const previous = deferred();
  request.mockImplementationOnce(() => previous.promise).mockResolvedValueOnce(page([event(21, 653)]));
  await render();
  await toggle(true);
  const previousSignal = request.mock.calls[0][1]?.signal;
  await render(1);
  expect(request).toHaveBeenCalledTimes(2);
  expect(previousSignal?.aborted).toBe(true);
  await act(async () => previous.resolve(page([event(20, 651)])));
  expect(box.textContent).toContain("версия 653");
  expect(box.textContent).not.toContain("версия 651");
  await toggle(false);
  await render(2);
  await render(3);
  expect(request).toHaveBeenCalledTimes(2);
});

it("keeps the previous list after a failed refresh and retries the latest page", async () => {
  request.mockResolvedValueOnce(page()).mockResolvedValueOnce(Response.json({ error: "Проверките временно не са достъпни." }, { status: 503 }))
    .mockResolvedValueOnce(page([event(21, 653)]));
  await render();
  await toggle(true);
  await click("Обнови проверките");
  expect(box.querySelector('[role="alert"]')?.textContent).toContain("Проверките временно не са достъпни.");
  expect(rows()).toHaveLength(1);
  expect(box.textContent).toContain("версия 652");
  await click("Опитай отново");
  expect(request.mock.calls[2][0]).toBe("/api/athlete/management/checks?limit=10");
  expect(box.querySelector('[role="alert"]')).toBeNull();
  expect(box.textContent).toContain("версия 653");
  expect(box.textContent).not.toContain("версия 652");
});

it("retries a failed older page with the same cursor and preserves the loaded rows", async () => {
  request.mockResolvedValueOnce(page([event(20)], 20)).mockRejectedValueOnce(new Error("Временна грешка при зареждане."))
    .mockResolvedValueOnce(page([event(19)]));
  await render();
  await toggle(true);
  await click("Още проверки");
  expect(rows()).toHaveLength(1);
  await click("Опитай отново");
  expect(request.mock.calls[2][0]).toBe("/api/athlete/management/checks?limit=10&before=20");
  expect(rows()).toHaveLength(2);
  expect(button("Още проверки")).toBeUndefined();
});

it("cancels on close and ignores late responses after reopening", async () => {
  const previous = deferred();
  request.mockImplementationOnce(() => previous.promise).mockResolvedValueOnce(page([event(21, 653)]));
  await render();
  await toggle(true);
  const previousSignal = request.mock.calls[0][1]?.signal;
  await toggle(false);
  expect(previousSignal?.aborted).toBe(true);
  await toggle(true);
  await act(async () => previous.resolve(page([event(20, 651)])));
  expect(rows()).toHaveLength(1);
  expect(box.textContent).toContain("версия 653");
  expect(box.textContent).not.toContain("версия 651");
});

it("isolates a changed athlete scope and ignores the previous scope's late response", async () => {
  const oldRequest = deferred(), newRequest = deferred();
  request.mockResolvedValueOnce(page([event(20, 651)]))
    .mockImplementationOnce(() => oldRequest.promise)
    .mockImplementationOnce(() => newRequest.promise);
  await render();
  await toggle(true);
  await click("Обнови проверките");
  const oldSignal = request.mock.calls[1][1]?.signal;
  await render(0, "athlete-b");
  expect(request).toHaveBeenCalledTimes(2);
  expect(oldSignal?.aborted).toBe(true);
  expect(rows()).toHaveLength(0);
  await toggle(true);
  expect(request).toHaveBeenCalledTimes(3);
  expect(new Headers(request.mock.calls[2][1]?.headers).get("X-Onflows-Athlete")).toBe("athlete-b");
  await act(async () => newRequest.resolve(page([event(21, 653)])));
  await act(async () => oldRequest.resolve(page([event(20, 651)])));
  expect(rows()).toHaveLength(1);
  expect(box.textContent).toContain("версия 653");
  expect(box.textContent).not.toContain("версия 651");
});

it("cancels a pending request on unmount", async () => {
  const pending = deferred();
  request.mockImplementationOnce(() => pending.promise);
  await render();
  await toggle(true);
  const signal = request.mock.calls[0][1]?.signal;
  await act(async () => root.unmount());
  expect(signal?.aborted).toBe(true);
  await act(async () => pending.resolve(page()));
  root = createRoot(box);
  expect(box.textContent).toBe("");
});

it("shows an explicit empty state after a successful empty response", async () => {
  request.mockResolvedValueOnce(page([]));
  await render();
  await toggle(true);
  expect(box.textContent).toContain("Няма записани проверки без промяна.");
  expect(rows()).toHaveLength(0);
  expect(button("Още проверки")).toBeUndefined();
});
