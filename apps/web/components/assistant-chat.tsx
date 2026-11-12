"use client";

import { Fragment, useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

type Source = { kind?: string; label?: string; revision?: number | string; as_of?: string };
type Receipt = { status?: string; kind?: string; revision?: number; operation_id?: string; message?: string };
type Reply = { status?: string; conversation_id?: string; operation_id?: string; message?: string; error?: string; sources?: Source[]; receipt?: Receipt; suggestions?: string[]; pending?: boolean; provider_available?: boolean; messages?: unknown; pending_operation?: unknown };
type Message = { id: string; role: "user" | "assistant"; content: string; sources?: Source[]; receipt?: Receipt; pending?: boolean };
type Submission = { operation_id: string; conversation_id: string | null; message: string };
type Props = { athleteAlias: string; actorUserId: string; displayName: string; canEditPlan: boolean; isOwner: boolean; canViewPlan: boolean; canViewRecovery: boolean };

const SCOPE_MESSAGE = "Профилът или достъпът е променен. Заредете актуалния профил, за да продължите.";
const validUuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
const safeHistory = (input: unknown): Message[] => Array.isArray(input) ? input.slice(-24).flatMap((value, index) => {
  if (!value || typeof value !== "object" || !["user", "assistant"].includes(value.role) || typeof value.content !== "string" || !value.content.trim() || value.content.length > 20000) return [];
  return [{ id: `history:${index}`, role: value.role, content: value.content }];
}) : [];
const safePending = (input: unknown, conversation: string | null): Submission | null => {
  if (!input || typeof input !== "object") return null;
  const value = input as Record<string, unknown>;
  if (!validUuid(value.operation_id) || !validUuid(value.conversation_id) || value.conversation_id !== conversation || typeof value.message !== "string" || !value.message.trim() || value.message.length > 4000) return null;
  return { operation_id: value.operation_id, conversation_id: value.conversation_id, message: value.message };
};
const safeSuggestions = (input: unknown) => Array.isArray(input) ? input.filter((value): value is string => typeof value === "string" && value.length <= 300).slice(0, 4) : [];
const receiptLabel = (status?: string) => ({ SUCCEEDED: "Потвърден запис", SAVED: "Потвърден запис", COMMITTED: "Потвърден запис", COMPLETED: "Потвърден запис", CONFLICT: "Необходима е проверка на версията", PENDING: "Обработва се", UNKNOWN: "Резултатът се проверява" })[status?.toUpperCase() ?? ""] ?? "Резултат от операцията";

const APP_LINKS = new Set(["/", "/?settings=edit", "/account", "/activities", "/management", "/management/outlook", "/planning", "/trainability", "/methods", "/speed", "/learning", "/response", "/assistant", "/api/integrations/intervals/connect"]);
const ACCOUNT_SECTIONS = new Set(["role-profile-title", "invites-title", "athletes-title", "create-team-title", "link-athlete-title", "password-guidance"]);
function safeAppLink(href: string) {
  if (APP_LINKS.has(href)) return true;
  const account = href.match(/^\/account#([a-z-]+)$/);
  if (account && ACCOUNT_SECTIONS.has(account[1])) return true;
  return /^\/activities\/[A-Za-z0-9_-]+(?:\/shadow)?$/.test(href);
}

// Model output never becomes HTML or image markup. Links are limited to known
// application routes and require a user click; unsupported syntax stays text.
function inlineText(text: string): ReactNode[] {
  return text.split(/(\[[^\]\n]+\]\([^\s)]+\)|\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\n]+\*)/g).map((part, index) => {
    const link = part.match(/^\[([^\]\n]+)\]\(([^\s)]+)\)$/);
    if (link && safeAppLink(link[2])) return <a key={index} href={link[2]}>{link[1]}</a>;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    if (part.startsWith("*") && part.endsWith("*")) return <em key={index}>{part.slice(1, -1)}</em>;
    return <Fragment key={index}>{part}</Fragment>;
  });
}

function AssistantText({ text }: { text: string }) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) { index++; continue; }
    if (/^\s*```/.test(line)) {
      const content: string[] = []; index++;
      while (index < lines.length && !/^\s*```/.test(lines[index])) content.push(lines[index++]);
      if (index < lines.length) index++;
      blocks.push(<pre key={blocks.length}><code>{content.join("\n")}</code></pre>); continue;
    }
    const heading = line.match(/^\s*#{1,6}\s+(.+)$/);
    if (heading) { blocks.push(<p className="assistant-text-heading" key={blocks.length}>{inlineText(heading[1])}</p>); index++; continue; }
    const bullet = line.match(/^\s*[-*•]\s+(.+)$/);
    const numbered = line.match(/^\s*(\d+)[.)]\s+(.+)$/);
    if (bullet || numbered) {
      const items: ReactNode[] = [];
      const pattern = numbered ? /^\s*\d+[.)]\s+(.+)$/ : /^\s*[-*•]\s+(.+)$/;
      while (index < lines.length) {
        const match = lines[index].match(pattern);
        if (!match) break;
        items.push(<li key={items.length}>{inlineText(match[1])}</li>); index++;
      }
      blocks.push(numbered ? <ol key={blocks.length} start={Number(numbered[1])}>{items}</ol> : <ul key={blocks.length}>{items}</ul>); continue;
    }
    const paragraph = [line]; index++;
    while (index < lines.length && lines[index].trim() && !/^\s*(?:```|#{1,6}\s|[-*•]\s|\d+[.)]\s)/.test(lines[index])) paragraph.push(lines[index++]);
    blocks.push(<p key={blocks.length}>{inlineText(paragraph.join("\n"))}</p>);
  }
  return <>{blocks}</>;
}

export function AssistantChat({ athleteAlias, actorUserId, displayName, canEditPlan, isOwner, canViewPlan, canViewRecovery }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [conversation, setConversation] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [longWait, setLongWait] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const [changed, setChanged] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState<Submission | null>(null);
  const [provider, setProvider] = useState<boolean | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [deleting, setDeleting] = useState(false);
  const active = useRef(true);
  const invalid = useRef(false);
  const inFlight = useRef(false);
  const initialized = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const latestMessage = useRef<HTMLElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const scopeKey = `${actorUserId}:${athleteAlias}`;
  const permissionKey = `${canEditPlan}:${isOwner}:${canViewPlan}:${canViewRecovery}`;
  const initialScope = useRef(`${scopeKey}:${permissionKey}`);
  const headers = useCallback(() => ({ "Content-Type": "application/json", "X-Onflows-Athlete": athleteAlias, "X-Onflows-Actor": actorUserId }), [athleteAlias, actorUserId]);
  const invalidate = useCallback(() => {
    invalid.current = true;
    controller.current?.abort();
    setChanged(true); setMessages([]); setInput(""); setConversation(null); setRetry(null); setError(""); setBusy(false);
  }, []);

  useEffect(() => {
    active.current = true;
    if (initialScope.current !== `${scopeKey}:${permissionKey}`) { invalidate(); return; }
    const channel = typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel("onflows-management-scope");
    window.addEventListener("onflows-scope-changed", invalidate);
    if (channel) {
      channel.onmessage = event => { if (event.data?.changed || event.data?.scope && event.data.scope !== scopeKey) invalidate(); };
      channel.postMessage({ scope: scopeKey });
    }
    const statusController = new AbortController();
    void fetch("/api/athlete/assistant", { cache: "no-store", headers: headers(), signal: statusController.signal }).then(async response => {
      if (!active.current || invalid.current) return;
      if ([401, 403, 412].includes(response.status)) { invalidate(); return; }
      if (!response.ok) return;
      const result = await response.json() as Reply;
      if (!active.current || invalid.current) return;
      if (typeof result.provider_available === "boolean") setProvider(result.provider_available);
      setSuggestions(safeSuggestions(result.suggestions));
      const restoredConversation = validUuid(result.conversation_id) ? result.conversation_id : null;
      const restoredMessages = restoredConversation ? safeHistory(result.messages) : [];
      const pending = safePending(result.pending_operation, restoredConversation);
      setConversation(restoredConversation);
      setMessages(pending ? [...restoredMessages, { id: pending.operation_id, role: "user", content: pending.message }] : restoredMessages);
      if (pending) { setRetry(pending); setError("Предишната заявка още се обработва. Проверете резултата, за да продължите."); }
    }).catch(() => { /* A failed history read does not affect the ordinary forms. */ }).finally(() => {
      if (active.current && !invalid.current) { initialized.current = true; setRestoring(false); }
    });
    return () => { active.current = false; statusController.abort(); controller.current?.abort(); channel?.close(); window.removeEventListener("onflows-scope-changed", invalidate); };
  }, [scopeKey, permissionKey, headers, invalidate]);

  useEffect(() => {
    if (messages.at(-1)?.role === "assistant" && !busy) latestMessage.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    else end.current?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }, [messages, busy]);

  useEffect(() => {
    if (!busy) return;
    const timer = window.setTimeout(() => setLongWait(true), 20_000);
    return () => window.clearTimeout(timer);
  }, [busy]);

  async function send(text: string, previous?: Submission) {
    if (invalid.current || !active.current || !initialized.current || inFlight.current || !text.trim()) return;
    const submission = previous ?? { operation_id: crypto.randomUUID(), conversation_id: conversation, message: text.trim() };
    inFlight.current = true; setBusy(true); setLongWait(false); setError(""); setRetry(null);
    if (!previous) { setMessages(current => [...current, { id: submission.operation_id, role: "user", content: submission.message }]); setInput(""); }
    const requestController = new AbortController(); controller.current = requestController;
    try {
      let response: Response | undefined;
      let result: Reply | undefined;
      if (previous) {
        const query = new URLSearchParams({ operation_id: submission.operation_id, ...(submission.conversation_id ? { conversation_id: submission.conversation_id } : {}) });
        response = await fetch(`/api/athlete/assistant?${query}`, { cache: "no-store", headers: headers(), signal: requestController.signal });
        if (!active.current || invalid.current) return;
        if ([401, 403, 412].includes(response.status)) { invalidate(); return; }
        result = await response.json() as Reply;
        if (response.ok && result.status?.toUpperCase() === "UNKNOWN") { response = undefined; result = undefined; }
      }
      if (!response) {
        response = await fetch("/api/athlete/assistant", { method: "POST", cache: "no-store", headers: headers(), body: JSON.stringify(submission), signal: requestController.signal });
        if (!active.current || invalid.current) return;
        if ([401, 403, 412].includes(response.status)) { invalidate(); return; }
        result = await response.json() as Reply;
      }
      if (!result) throw new Error("Непълен отговор");
      if (!active.current || invalid.current) return;
      if (!response.ok) {
        setError(typeof result.error === "string" ? result.error : "Отговорът не е потвърден. Проверете резултата, преди да въвеждате същото отново.");
        if (![409, 413, 422].includes(response.status)) setRetry(submission);
        return;
      }
      if (typeof result.message !== "string" || !result.message.trim()) throw new Error("Непълен отговор");
      if (result.conversation_id) setConversation(result.conversation_id);
      if (typeof result.provider_available === "boolean") setProvider(result.provider_available);
      setSuggestions(safeSuggestions(result.suggestions));
      const waiting = result.pending === true || result.status?.toUpperCase() === "PENDING";
      if (waiting) { setRetry(submission); setError("Текущата заявка още се обработва. Проверете резултата след малко."); }
      const reply = result;
      setMessages(current => [...current.filter(item => item.id !== `${submission.operation_id}:reply`), { id: `${submission.operation_id}:reply`, role: "assistant", content: reply.message!, sources: Array.isArray(reply.sources) ? reply.sources.slice(0, 10) : [], receipt: reply.receipt, pending: waiting }]);
    } catch {
      if (active.current && !invalid.current) { setError("Връзката прекъсна. Записът може да е извършен. Следващата проверка използва същата заявка."); setRetry(submission); }
    } finally {
      inFlight.current = false;
      if (active.current && !invalid.current) { setBusy(false); field.current?.focus(); }
    }
  }

  async function removeConversation() {
    if (!conversation || busy || deleting || invalid.current) return;
    setDeleting(true); setError("");
    try {
      const response = await fetch("/api/athlete/assistant", { method: "DELETE", headers: headers(), body: JSON.stringify({ conversation_id: conversation }) });
      if (!active.current || invalid.current) return;
      if ([401, 403, 412].includes(response.status)) { invalidate(); return; }
      if (!response.ok) throw new Error("delete");
      setConversation(null); setMessages([]); setRetry(null); setSuggestions([]);
    } catch { if (active.current && !invalid.current) setError("Разговорът не е изтрит. Опитайте отново."); }
    finally { if (active.current) setDeleting(false); }
  }

  const quick = suggestions.length ? suggestions : [
    ...(canViewPlan ? ["Какво тренирам днес?", "Защо е избрана тази доза?", "Как да изпълня тренировката?"] : ["Какъв е текущият ми статус?"]),
    ...(isOwner && canViewRecovery ? ["Искам да попълня дневната си оценка."] : []),
  ];
  const submit = (event: FormEvent) => { event.preventDefault(); void send(input); };

  return <main className="assistant-page">
    <header className="assistant-heading"><div><p className="eyebrow">onFlows · Вашата тренировъчна програма</p><h1>ИИ асистент</h1><p>Попитайте за тренировката, нейното изпълнение или работата с приложението.</p></div><div className="assistant-profile"><span className="assistant-profile-dot" aria-hidden="true"/><div><strong>{displayName}</strong><small>{canEditPlan ? "Достъп за преглед и редакция на плана" : "Достъп за преглед"}</small></div></div></header>
    {changed ? <section className="assistant-scope-notice" role="alert"><h2>Профилът е променен</h2><p>{SCOPE_MESSAGE}</p><button className="action-button" type="button" onClick={() => window.location.reload()}>Зареди актуалния профил</button></section> : <>
      <div className="assistant-layout"><section className="assistant-conversation" aria-label="Разговор с асистента">
        <div className="assistant-conversation-top"><span><span aria-hidden="true">○</span> Разговор за {displayName}</span>{conversation && <button type="button" disabled={busy || restoring || deleting} onClick={() => void removeConversation()}>{deleting ? "Изтриване…" : "Изтрий разговора"}</button>}</div>
        {provider === false && <p className="assistant-provider-notice" role="status">Разговорният модел в момента не е достъпен. Налични са справките от onFlows и помощта за основните действия.</p>}
        <div className="assistant-log" role="log" aria-label="Съобщения" aria-live="polite" aria-relevant="additions text">
          {restoring && <p className="assistant-working" role="status">Зареждам разговора…</p>}
          {!restoring && !messages.length && <div className="assistant-welcome"><span className="assistant-welcome-icon" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M27 15a11 11 0 0 1-11 11H8l-5 4V15a12 12 0 0 1 24 0Z"/><path d="M9 13h14M9 18h9"/></svg></span><h2>С какво да започнем?</h2><p>Попитайте как да изпълните тренировката, обсъдете методиката или поискайте промяна в достъпните ви настройки.</p></div>}
          {messages.map((item, index) => <article key={item.id} ref={index === messages.length - 1 ? latestMessage : undefined} className={`assistant-message assistant-message-${item.role}`}><span className="assistant-message-role">{item.role === "user" ? "Вие" : "onFlows асистент"}</span><div className="assistant-message-content">{item.role === "assistant" ? <AssistantText text={item.content}/> : item.content}</div>
            {item.receipt && !item.pending && <div className="assistant-receipt"><strong>{receiptLabel(item.receipt.status)}</strong>{item.receipt.message && !item.content.includes(item.receipt.message) && <span>{item.receipt.message}</span>}{item.receipt.revision !== undefined && <details><summary>Подробности за записа</summary><small>Версия {item.receipt.revision}</small></details>}</div>}
            {!!item.sources?.length && <details className="assistant-sources"><summary>Основание и версия</summary><ul>{item.sources.map((source, index) => <li key={index}>{source.label ?? source.kind ?? "Запазени данни от onFlows"}{source.revision !== undefined ? ` · версия ${source.revision}` : ""}{source.as_of ? ` · към ${source.as_of}` : ""}</li>)}</ul></details>}
          </article>)}
          {busy && <p className="assistant-working" role="status"><span className="assistant-thinking" aria-hidden="true"/>{longWait ? "Заявката още се обработва. При промяна на програмата изчислението може да отнеме повече време. Резултатът ще се появи тук." : "Обработвам заявката…"}</p>}
          <div ref={end}/>
        </div>
        <div className="assistant-compose"><div className="assistant-suggestions" aria-label="Примерни въпроси">{quick.map(text => <button key={text} type="button" disabled={busy || restoring || !!retry} onClick={() => void send(text)}>{text}</button>)}</div>
          {error && <div className="assistant-error" role="alert"><p>{error}</p>{retry && <button type="button" disabled={busy} onClick={() => void send(retry.message, retry)}>Провери и продължи заявката</button>}</div>}
          <form onSubmit={submit}><label htmlFor="assistant-message">Вашето съобщение</label><div className="assistant-input-row"><textarea id="assistant-message" ref={field} value={input} maxLength={4000} rows={2} disabled={busy || restoring || !!retry} placeholder="Например: Как да изпълня днешната тренировка?" onChange={event => setInput(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (input.trim()) void send(input); } }}/><button className="action-button" type="submit" disabled={busy || restoring || !!retry || !input.trim()}><span>{busy ? "Изчакване…" : "Изпрати"}</span><span aria-hidden="true">↑</span></button></div><p className="assistant-compose-note">Enter изпраща · Shift + Enter добавя нов ред.</p></form>
        </div>
      </section><aside className="assistant-help"><h2>Връзка с вашите данни</h2><p>Асистентът свързва данните от профила с обяснения за тренировката и методиката. Действията следват правата ви за този спортист.</p><div className="assistant-form-links">{canViewPlan && <a href="/management"><span>Седмична програма</span><span aria-hidden="true">↗</span></a>}{canViewRecovery && <a href="/response"><span>Оценки и тегло</span><span aria-hidden="true">↗</span></a>}{canViewPlan && <a href="/planning"><span>Цели и календар</span><span aria-hidden="true">↗</span></a>}{canViewPlan && <a href="/methods"><span>Тренировъчни методи</span><span aria-hidden="true">↗</span></a>}</div><p className="assistant-help-note">След успешна промяна получавате потвърждение какво е записано.</p></aside></div>
    </>}
  </main>;
}
