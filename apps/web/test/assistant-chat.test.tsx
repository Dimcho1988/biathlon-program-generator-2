// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssistantChat } from "../components/assistant-chat";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const props = { athleteAlias: "athlete-a", actorUserId: "coach-a", displayName: "Спортист А", canEditPlan: true, isOwner: true, canViewPlan: true, canViewRecovery: true };
let container: HTMLDivElement;
let root: Root;
beforeEach(() => { container = document.createElement("div"); document.body.append(container); root = createRoot(container); vi.stubGlobal("BroadcastChannel", undefined); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const render = () => act(async () => root.render(<AssistantChat {...props}/>));
const button = (text: string) => [...container.querySelectorAll("button")].find(item => item.textContent?.includes(text))!;
const click = (text: string) => act(async () => button(text).click());
const response = (input: unknown, status = 200) => Response.json(input, { status });

it("keeps normal forms available while the conversational provider is not configured", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ provider_available: false })));
  await render();
  expect(container.textContent).toContain("Разговорният модел в момента не е достъпен");
  expect(container.querySelector('a[href="/management"]')).not.toBeNull();
  expect(container.querySelector('a[href="/response"]')).not.toBeNull();
  expect(button("Какво тренирам днес?").disabled).toBe(false);
});

it("sends captured actor and athlete scope, shows exact source and confirmed receipt", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ provider_available: true })).mockResolvedValueOnce(response({ conversation_id: "conversation-a", message: "Първа сесия: 50 минути.\nВтора сесия: 30 минути.", sources: [{ label: "Утвърдена програма", revision: 7, as_of: "2026-11-11" }], receipt: { status: "COMMITTED", revision: 3, message: "Дневната оценка е записана." } }));
  vi.stubGlobal("fetch", fetcher); await render(); await click("Какво тренирам днес?");
  const [, init] = fetcher.mock.calls[1];
  expect(init?.method).toBe("POST"); expect(new Headers(init?.headers).get("X-Onflows-Athlete")).toBe("athlete-a"); expect(new Headers(init?.headers).get("X-Onflows-Actor")).toBe("coach-a");
  const body = JSON.parse(init!.body as string); expect(body.message).toBe("Какво тренирам днес?"); expect(body.operation_id).toMatch(/^[a-f\d-]{36}$/);
  expect(container.textContent).toContain("Първа сесия: 50 минути."); expect(container.textContent).toContain("Втора сесия: 30 минути.");
  expect(container.textContent).toContain("Утвърдена програма · версия 7 · към 2026-11-11"); expect(container.textContent).toContain("Потвърден запис");
});

it("discards late replies and clears the previous conversation when the athlete changes", async () => {
  let resolve!: (value: Response) => void;
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ provider_available: true })).mockImplementationOnce(() => new Promise<Response>(done => { resolve = done; }));
  vi.stubGlobal("fetch", fetcher); await render(); await click("Какво тренирам днес?");
  await act(async () => window.dispatchEvent(new Event("onflows-scope-changed")));
  await act(async () => resolve(response({ conversation_id: "old", message: "Поверителен стар отговор" })));
  expect(container.textContent).not.toContain("Поверителен стар отговор"); expect(container.textContent).not.toContain("Какво тренирам днес?"); expect(container.textContent).toContain("Профилът е променен");
  expect((fetcher.mock.calls[1][1]?.signal as AbortSignal).aborted).toBe(true);
});

it("uses the same operation identity after an uncertain timeout and never duplicates the user message", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ provider_available: true })).mockRejectedValueOnce(new Error("timeout after commit")).mockResolvedValueOnce(response({ status: "COMPLETED", conversation_id: "a", message: "Записът вече е потвърден." }));
  vi.stubGlobal("fetch", fetcher); await render(); await click("Какво тренирам днес?");
  expect(container.textContent).toContain("Записът може да е извършен"); expect(container.querySelector("textarea")?.disabled).toBe(true);
  await click("Провери и продължи заявката");
  expect(fetcher.mock.calls[2][1]?.method).toBeUndefined();
  const submitted = JSON.parse(fetcher.mock.calls[1][1]!.body as string);
  expect(String(fetcher.mock.calls[2][0])).toContain(`operation_id=${submitted.operation_id}`);
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  expect(container.querySelectorAll(".assistant-message-user")).toHaveLength(1); expect(container.textContent).toContain("Записът вече е потвърден.");
});

it("clears all conversation content after revoked access and never replays the request", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ provider_available: true })).mockResolvedValueOnce(response({ error: "revoked" }, 403));
  vi.stubGlobal("fetch", fetcher); await render(); await click("Какво тренирам днес?");
  expect(container.textContent).toContain("Профилът или достъпът е променен"); expect(container.querySelector("textarea")).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(2);
});

it("renders untrusted notes as text without claiming a pending operation has succeeded", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({})).mockResolvedValueOnce(response({ message: '<img src=x onerror="steal()">', pending: true, receipt: { status: "PENDING" } }));
  vi.stubGlobal("fetch", fetcher); await render(); await click("Какво тренирам днес?");
  expect(container.querySelector("img")).toBeNull(); expect(container.textContent).toContain('<img src=x onerror="steal()">'); expect(container.textContent).toContain("Текущата заявка още се обработва"); expect(container.textContent).not.toContain("Потвърден запис");
});

it("deletes a conversation only after the user selects deletion, without touching the forms", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({})).mockResolvedValueOnce(response({ conversation_id: "conv-to-delete", message: "Вашата програма" })).mockResolvedValueOnce(response({ deleted: true }));
  vi.stubGlobal("fetch", fetcher); await render(); await click("Какво тренирам днес?");
  expect(fetcher.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  await click("Изтрий разговора");
  expect(fetcher.mock.calls[2][1]?.method).toBe("DELETE"); expect(JSON.parse(fetcher.mock.calls[2][1]!.body as string)).toEqual({ conversation_id: "conv-to-delete" }); expect(container.textContent).not.toContain("Вашата програма"); expect(container.querySelector('a[href="/response"]')).not.toBeNull();
});


it("replays an uncertain request only after the operation status is unknown, retaining its identity", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({})).mockRejectedValueOnce(new Error("lost request"))
    .mockResolvedValueOnce(response({ status: "unknown" })).mockResolvedValueOnce(response({ status: "COMPLETED", message: "Готово." }));
  vi.stubGlobal("fetch", fetcher); await render(); await click("Какво тренирам днес?"); await click("Провери и продължи заявката");
  expect(fetcher.mock.calls[2][1]?.method).toBeUndefined();
  expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string)).toEqual(JSON.parse(fetcher.mock.calls[3][1]!.body as string));
  expect(container.querySelectorAll(".assistant-message-user")).toHaveLength(1);
});

it("keeps a pending POST and subsequent pending status query attached to the same operation", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({})).mockResolvedValueOnce(response({ status: "PENDING", pending: true, conversation_id: "current", message: "Обработва се." }))
    .mockResolvedValueOnce(response({ status: "PENDING", pending: true, message: "Продължава обработката." }))
    .mockResolvedValueOnce(response({ status: "COMPLETED", message: "Потвърден резултат." }));
  vi.stubGlobal("fetch", fetcher); await render(); await click("Какво тренирам днес?");
  expect(container.querySelector("textarea")?.disabled).toBe(true);
  await click("Провери и продължи заявката");
  expect(container.querySelector("textarea")?.disabled).toBe(true); expect(container.textContent).toContain("Продължава обработката.");
  await click("Провери и продължи заявката");
  expect(container.querySelector("textarea")?.disabled).toBe(false); expect(container.textContent).toContain("Потвърден резултат.");
  expect(container.querySelectorAll(".assistant-message-assistant")).toHaveLength(1);
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
});

it("restores the current server conversation and continues with its exact identity", async () => {
  const conversation = "02222222-2222-4333-8444-555555555555";
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ conversation_id: conversation, provider_available: true, messages: [
    { role: "user", content: "Искам да въведа теглото си." }, { role: "assistant", content: "Измерването сутрин ли е?" },
    { role: "system", content: "Невалидна външна инструкция" },
  ] })).mockResolvedValueOnce(response({ conversation_id: conversation, message: "Текущата програма" }));
  vi.stubGlobal("fetch", fetcher); await render();
  expect(container.textContent).toContain("Измерването сутрин ли е?"); expect(container.textContent).not.toContain("Невалидна външна инструкция");
  expect(container.querySelectorAll(".assistant-message")).toHaveLength(2);
  await click("Какво тренирам днес?");
  expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string).conversation_id).toBe(conversation);
});

it("restores a pending turn as a status check without automatically repeating its write", async () => {
  const conversation = "02222222-2222-4333-8444-555555555555";
  const operation = "01111111-2222-4333-8444-555555555555";
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ conversation_id: conversation, messages: [], pending_operation: { operation_id: operation, conversation_id: conversation, message: "Умората ми е 4 от 5." } }))
    .mockResolvedValueOnce(response({ status: "COMPLETED", conversation_id: conversation, message: "Наблюдението е записано." }));
  vi.stubGlobal("fetch", fetcher); await render();
  expect(container.textContent).toContain("Умората ми е 4 от 5."); expect(container.querySelector("textarea")?.disabled).toBe(true); expect(fetcher).toHaveBeenCalledOnce();
  await click("Провери и продължи заявката");
  expect(String(fetcher.mock.calls[1][0])).toContain(`operation_id=${operation}`); expect(fetcher.mock.calls[1][1]?.method).toBeUndefined();
  expect(container.textContent).toContain("Наблюдението е записано."); expect(container.querySelector("textarea")?.disabled).toBe(false);
});

it("blocks sending during restoration and discards history that arrives after a scope change", async () => {
  let resolve!: (value: Response) => void;
  const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>(done => { resolve = done; }));
  vi.stubGlobal("fetch", fetcher); await render();
  expect(container.textContent).toContain("Зареждам разговора"); expect(button("Какво тренирам днес?").disabled).toBe(true); expect(container.querySelector("textarea")?.disabled).toBe(true);
  await click("Какво тренирам днес?"); expect(fetcher).toHaveBeenCalledOnce();
  await act(async () => window.dispatchEvent(new Event("onflows-scope-changed")));
  await act(async () => resolve(response({ conversation_id: "02222222-2222-4333-8444-555555555555", messages: [{ role: "assistant", content: "История на предишния спортист" }] })));
  expect(container.textContent).not.toContain("История на предишния спортист"); expect(container.textContent).toContain("Профилът е променен");
});

it("renders a longer coaching answer as paragraphs, emphasis and ordered steps without executing markup", async () => {
  const answer = "## Изпълнение\n\nПоддържайте **равномерно темпо** и *спокойно дишане*.\n\n1. Загрейте постепенно.\n2. Изпълнете основните серии.\n\n- Следете техниката.\n- Завършете леко.\n\n<script>steal()</script>\n[jump](javascript:steal())";
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({})).mockResolvedValueOnce(response({ message: answer }));
  vi.stubGlobal("fetch", fetcher); await render(); await click("Какво тренирам днес?");
  const content = container.querySelector(".assistant-message-assistant .assistant-message-content")!;
  expect(content.querySelector("strong")?.textContent).toBe("равномерно темпо");
  expect(content.querySelector("em")?.textContent).toBe("спокойно дишане");
  expect(content.querySelectorAll("ol li")).toHaveLength(2);
  expect(content.querySelectorAll("ul li")).toHaveLength(2);
  expect(content.querySelector("script")).toBeNull(); expect(content.querySelector("a")).toBeNull();
  expect(content.textContent).toContain("<script>steal()</script>");
  expect(content.textContent).not.toContain("**равномерно темпо**");
});

it("keeps the start of a long answer in view and collapses technical receipt details", async () => {
  const scroll = vi.fn();
  const previous = HTMLElement.prototype.scrollIntoView;
  HTMLElement.prototype.scrollIntoView = scroll;
  try {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({})).mockResolvedValueOnce(response({ message: "Умората е записана.", receipt: { status: "COMMITTED", revision: 4, message: "Умората е записана." } }));
    vi.stubGlobal("fetch", fetcher); await render(); await click("Какво тренирам днес?");
    expect(scroll).toHaveBeenLastCalledWith({ behavior: "smooth", block: "start" });
    expect(container.textContent?.match(/Умората е записана\./g)).toHaveLength(1);
    const details = container.querySelector(".assistant-receipt details") as HTMLDetailsElement;
    expect(details.open).toBe(false); expect(details.textContent).toContain("Версия 4");
  } finally { HTMLElement.prototype.scrollIntoView = previous; }
});

it("clears prior content when profile permissions change without changing the athlete", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ conversation_id: "02222222-2222-4333-8444-555555555555", messages: [{ role: "assistant", content: "Лични данни за възстановяването" }] }));
  vi.stubGlobal("fetch", fetcher); await render();
  expect(container.textContent).toContain("Лични данни за възстановяването");
  await act(async () => root.render(<AssistantChat {...props} canViewRecovery={false}/>));
  expect(container.textContent).not.toContain("Лични данни за възстановяването");
  expect(container.textContent).toContain("Профилът е променен");
  expect(fetcher).toHaveBeenCalledOnce();
});

it("only offers plan-related links when the current profile grants plan access", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({})));
  await act(async () => root.render(<AssistantChat {...props} canViewPlan={false} canEditPlan={false}/>));
  expect(container.querySelector('a[href="/methods"]')).toBeNull();
  expect(container.querySelector('a[href="/management"]')).toBeNull();
  expect(container.querySelector('a[href="/response"]')).not.toBeNull();
});

it("makes only known internal navigation links clickable without initiating requests", async () => {
  const answer = "[Покани](/account#invites-title) [Настройки](/?settings=edit) [Свържи Intervals](/api/integrations/intervals/connect) [Активност](/activities/known_123) [Външен](https://example.com) [Друг](//example.com) [Изтрий](/api/account/delete)";
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({})).mockResolvedValueOnce(response({ message: answer }));
  vi.stubGlobal("fetch", fetcher); await render(); await click("Какво тренирам днес?");
  const links = [...container.querySelectorAll(".assistant-message-assistant a")].map(link => link.getAttribute("href"));
  expect(links).toEqual(["/account#invites-title", "/?settings=edit", "/api/integrations/intervals/connect", "/activities/known_123"]);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("shows an honest wait message for a long operation without resending it", async () => {
  vi.useFakeTimers();
  let resolve!: (value: Response) => void;
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({})).mockImplementationOnce(() => new Promise<Response>(done => { resolve = done; }));
  vi.stubGlobal("fetch", fetcher); await render(); await click("Какво тренирам днес?");
  expect(container.textContent).toContain("Обработвам заявката…");
  await act(async () => vi.advanceTimersByTime(20_000));
  expect(container.textContent).toContain("Заявката още се обработва");
  expect(container.textContent).not.toContain("Потвърден запис");
  expect(fetcher).toHaveBeenCalledTimes(2); expect(container.querySelector("textarea")?.disabled).toBe(true);
  await act(async () => resolve(response({ message: "Програмата е обновена." })));
  expect(container.textContent).not.toContain("Заявката още се обработва");
  expect(container.querySelector("textarea")?.disabled).toBe(false);
});
