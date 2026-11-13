"use client";

import { useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useManagementFetch } from "./management-scope";
import { isRecord } from "../lib/training-status";
import { type LibraryMethod, type MethodRange, type PersonalMethod } from "../lib/method-library";
import { addProposalDays, parseTrainingProposal, parseTrainingProposalPreview, parseTrainingProposalState, type TrainingProposal, type TrainingProposalPreview, type TrainingProposalState } from "../lib/training-proposals";

const baseUrl = "/api/athlete/management/training-proposals";
const dateLabel = (date: string) => new Date(`${date}T12:00:00Z`).toLocaleDateString("bg-BG", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const rangeText = (range?: MethodRange) => range ? range[0] === range[1] ? String(range[0]) : `${range[0]}–${range[1]}` : "според основния метод";
class ProposalError extends Error { constructor(message: string, readonly status: number) { super(message); } }

function useTrainingProposals(onApplied?: () => void | Promise<void>, onBusyChange?: (busy: boolean) => void) {
  const scopedFetch = useManagementFetch();
  const [state, setState] = useState<TrainingProposalState | null>(null);
  const [preview, setPreview] = useState<TrainingProposalPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const active = useRef(false);
  const operation = useRef("");
  async function request(path: string, input?: unknown) {
    const response = await scopedFetch(`${baseUrl}${path}`, { cache: "no-store", ...(input ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) } : {}) });
    const value: unknown = await response.json();
    if (!response.ok) throw new ProposalError(isRecord(value) && typeof value.error === "string" ? value.error : "Предложението не можа да се обработи.", response.status);
    return value;
  }
  async function run(action: () => Promise<void>) {
    if (active.current) return;
    active.current = true; setBusy(true); onBusyChange?.(true); setError("");
    try { await action(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Възникна грешка. Въведените данни са запазени."); if (caught instanceof ProposalError && caught.status === 409) { setPreview(null); operation.current = ""; } }
    finally { active.current = false; setBusy(false); onBusyChange?.(false); }
  }
  const invalidate = () => { setPreview(null); setNotice(""); operation.current = ""; };
  async function load() { await run(async () => { setState(parseTrainingProposalState(await request(""))); }); }
  async function check(proposal: TrainingProposal) {
    await run(async () => {
      invalidate();
      const result = parseTrainingProposalPreview(await request("/preview", { proposal: parseTrainingProposal(proposal) }));
      setState(result); setPreview(result); operation.current = crypto.randomUUID();
    });
  }
  async function commit(result: TrainingProposalPreview, operationId: string) {
    await request("/apply", { proposal: result.preview.proposal, expected_revision: result.profile_revision, operation_id: operationId });
    setPreview(null); operation.current = "";
    setNotice("Промяната е записана. Програмата се преизчислява с актуалната готовност и натоварване.");
    try { setState(parseTrainingProposalState(await request(""))); await onApplied?.(); }
    catch { setError("Промяната е записана, но изгледът не се обнови. Зареди актуалните данни."); }
  }
  async function apply() { if (preview) await run(() => commit(preview, operation.current || (operation.current = crypto.randomUUID()))); }
  async function remove(proposal: TrainingProposal) {
    await run(async () => {
      invalidate();
      const result = parseTrainingProposalPreview(await request("/preview", { proposal }));
      await commit(result, crypto.randomUUID());
    });
  }
  return { state, preview, busy, error, notice, load, check, apply, remove, invalidate };
}
type ProposalController = ReturnType<typeof useTrainingProposals>;

function ProposalFeedback({ control, disabled }: { control: ProposalController; disabled?: boolean }) {
  const checked = control.preview?.preview;
  return <>
    {control.busy && <p role="status">Обработвам предложението…</p>}
    {control.error && <p className="proposal-error" role="alert">{control.error} Въведените стойности остават във формата.</p>}
    {control.notice && <p className="proposal-notice" role="status">{control.notice} <Link href="/management">Виж програмата →</Link></p>}
    {checked && <section className="proposal-preview" aria-label="Проверено предложение">
      <h3>Предложение за прилагане</h3><p>{checked.summary}</p>
      {(checked.proposal.action === "FIX_STRUCTURE" || checked.proposal.action === "REPEAT_STRUCTURE") && <p>Период за прилагане: <strong>{dateLabel(checked.proposal.target_start)} – {dateLabel(addProposalDays(checked.proposal.target_start, (Date.parse(checked.proposal.source_end) - Date.parse(checked.proposal.source_start)) / 86_400_000))}</strong>.</p>}
      {checked.proposal.action === "PERSONAL_METHOD" && <p><strong>{checked.proposal.method.name}</strong> · отсечка {rangeText(checked.proposal.method.work_range)} {checked.proposal.method.work_unit === "meters" ? "м" : checked.proposal.method.work_unit === "seconds" ? "сек" : ""} · повторения {rangeText(checked.proposal.method.repetitions)}{checked.proposal.method.recovery_seconds ? ` · почивка ${rangeText(checked.proposal.method.recovery_seconds)} сек` : ""}</p>}
      {checked.corrections.length > 0 && <ul>{checked.corrections.map((correction, index) => <li key={index}>{typeof correction === "string" ? correction : isRecord(correction) && typeof (correction.reason ?? correction.message) === "string" ? String(correction.reason ?? correction.message) : "Диапазонът е съобразен с основния метод."}</li>)}</ul>}
      {checked.days.length > 0 && <details><summary>Дни в предложението</summary><ul>{checked.days.map((day, index) => {
        if (!isRecord(day)) return null;
        const date = typeof day.date === "string" ? day.date : typeof day.target_date === "string" ? day.target_date
          : checked.proposal.action === "FIX_STRUCTURE" || checked.proposal.action === "REPEAT_STRUCTURE" ? addProposalDays(checked.proposal.target_start, index) : "";
        const sessions = Array.isArray(day.sessions) ? day.sessions.filter(isRecord) : [];
        const methods = sessions.flatMap(session => Array.isArray(session.method_titles) ? session.method_titles.filter(title => typeof title === "string") : typeof session.title === "string" ? [session.title] : Array.isArray(session.method_ids) ? session.method_ids.filter(id => typeof id === "string") : []);
        return <li key={index}>{date ? dateLabel(date) : `Ден ${index + 1}`}: {methods.length ? methods.join(" · ") : sessions.length ? `${sessions.length} тренировки` : "Почивка"}</li>;
      })}</ul></details>}
      <button className="action-button" type="button" disabled={control.busy || disabled} onClick={() => void control.apply()}>Приложи промяната</button>
    </section>}
  </>;
}
function RangeFields({ label, value, onChange, integer = false, zero = false }: { label: string; value?: MethodRange; onChange: (range: MethodRange) => void; integer?: boolean; zero?: boolean }) {
  return <fieldset className="proposal-range"><legend>{label}</legend>{([0, 1] as const).map(index => <label key={index}>{index ? "До" : "От"}<input aria-label={`${label} ${index ? "до" : "от"}`} type="number" min={zero ? 0 : integer ? 1 : .01} step={integer ? 1 : "any"} required value={value?.[index] ?? ""} onChange={event => { const next: MethodRange = value ? [...value] : [0, 0]; next[index] = event.target.value === "" ? 0 : Number(event.target.value); onChange(next); }}/></label>)}</fieldset>;
}

export function PersonalMethodsPanel({ methods, canEdit, disabled = false, onApplied, onBusyChange }: { methods: LibraryMethod[]; canEdit: boolean; disabled?: boolean; onApplied?: () => void | Promise<void>; onBusyChange?: (busy: boolean) => void }) {
  const control = useTrainingProposals(onApplied, onBusyChange);
  const [opened, setOpened] = useState(false);
  const [method, setMethod] = useState<PersonalMethod | null>(null);
  const bases = methods.filter(item => item.personal_variant_constraints?.eligible === true && !item.personal_variant && !item.id.startsWith("PERSONAL-"));
  const base = bases.find(item => item.id === method?.base_method_id);
  const constraints = base?.personal_variant_constraints;
  function selectBase(baseId: string, current?: PersonalMethod) {
    const item = bases.find(candidate => candidate.id === baseId);
    if (!item) return;
    const ranges = item.personal_variant_constraints;
    setMethod({ id: current?.id ?? `PERSONAL-${crypto.randomUUID()}`, name: current?.name ?? "", base_method_id: item.id,
      ...(ranges?.work_unit ? { work_unit: ranges.work_unit } : {}), ...(ranges?.work_range ? { work_range: ranges.work_range } : {}),
      ...(ranges?.repetitions ? { repetitions: ranges.repetitions } : {}), ...(ranges?.recovery_seconds ? { recovery_seconds: ranges.recovery_seconds } : {}), notes: current?.notes ?? "" });
    control.invalidate();
  }
  function patch(value: Partial<PersonalMethod>) { setMethod(current => current ? { ...current, ...value } : current); control.invalidate(); }
  function edit(value: PersonalMethod) { setMethod(value); control.invalidate(); }
  function submit(event: FormEvent) { event.preventDefault(); if (method) void control.check({ action: "PERSONAL_METHOD", method }); }
  return <details className="training-proposals" onToggle={event => { if (event.currentTarget.open && !opened) { setOpened(true); void control.load(); } }}>
    <summary>Лични варианти на методите</summary>
    <p>Вариантите се използват само за този спортист. Основният метод задава допустимите граници, а алгоритъмът определя дозата за конкретната тренировка.</p>
    {control.state && <ul className="proposal-list">{control.state.payload.personal_methods.map(item => <li key={item.id}><div><strong>{item.name}</strong><small>Основа: {methods.find(baseMethod => baseMethod.id === item.base_method_id)?.title ?? item.base_method_id}</small></div>{canEdit && <div className="proposal-actions"><button type="button" className="action-button secondary" disabled={control.busy || disabled} onClick={() => edit(item)}>Редактирай</button><button type="button" className="action-button secondary" disabled={control.busy || disabled} onClick={() => void control.remove({ action: "REMOVE_PERSONAL_METHOD", method_id: item.id })}>Премахни</button></div>}</li>)}</ul>}
    {control.state && control.state.payload.personal_methods.length === 0 && <p>Все още няма лични варианти.</p>}
    {disabled && canEdit && <p>Запази текущите настройки на методите, преди да приложиш личен вариант.</p>}
    {canEdit && control.state && <>
      {!method && <button className="action-button secondary" type="button" disabled={control.busy || disabled || bases.length === 0} onClick={() => selectBase(bases[0]?.id)}>Добави личен вариант</button>}
      {bases.length === 0 && <p>За текущата основа няма метод с поддържана индивидуална структура.</p>}
      {method && <form onSubmit={submit}><fieldset className="proposal-form" disabled={control.busy || disabled}>
        <label>Име на личния вариант<input required maxLength={160} value={method.name} onChange={event => patch({ name: event.target.value })}/></label>
        <label>Основен метод<select value={method.base_method_id} onChange={event => selectBase(event.target.value, method)}>{!base && <option value={method.base_method_id}>{method.base_method_id}</option>}{bases.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
        {constraints?.work_range && <RangeFields label={`Работна отсечка, ${constraints.work_unit === "meters" ? "метри" : "секунди"}`} value={method.work_range ?? constraints.work_range} onChange={work_range => patch({ work_range, work_unit: constraints.work_unit })}/>}
        {constraints?.repetitions && <RangeFields label="Брой повторения" integer value={method.repetitions ?? constraints.repetitions} onChange={repetitions => patch({ repetitions })}/>}
        {constraints?.recovery_seconds && <RangeFields zero label="Почивка, секунди" value={method.recovery_seconds ?? constraints.recovery_seconds} onChange={recovery_seconds => patch({ recovery_seconds })}/>}
        <label className="proposal-wide">Бележки за изпълнението<textarea rows={2} maxLength={1000} value={method.notes ?? ""} onChange={event => patch({ notes: event.target.value })}/></label>
        <div className="proposal-actions proposal-wide"><button className="action-button" type="submit">Прегледай предложението</button><button className="action-button secondary" type="button" onClick={() => { setMethod(null); control.invalidate(); }}>Затвори редактора</button></div>
      </fieldset></form>}
    </>}
    <ProposalFeedback control={control} disabled={disabled}/>
    {control.error && <button className="action-button secondary" type="button" disabled={control.busy} onClick={() => void control.load()}>Зареди актуалните данни</button>}
  </details>;
}

export function TrainingStructurePanel({ today, defaultStart, canEdit, disabled = false }: { today: string; defaultStart?: string; canEdit: boolean; disabled?: boolean }) {
  const control = useTrainingProposals();
  const [opened, setOpened] = useState(false);
  const [mode, setMode] = useState<"FIX_STRUCTURE" | "REPEAT_STRUCTURE">("FIX_STRUCTURE");
  const [sourceStart, setSourceStart] = useState(defaultStart || today);
  const [length, setLength] = useState(7);
  const [targetStart, setTargetStart] = useState(addProposalDays(defaultStart || today, 7));
  const [name, setName] = useState("");
  const change = (action: () => void) => { action(); control.invalidate(); };
  function submit(event: FormEvent) {
    event.preventDefault();
    void control.check({ action: mode, source_start: sourceStart, source_end: addProposalDays(sourceStart, length - 1), target_start: mode === "FIX_STRUCTURE" ? sourceStart : targetStart, ...(name.trim() ? { name: name.trim() } : {}) });
  }
  return <details className="training-proposals" onToggle={event => { if (event.currentTarget.open && !opened) { setOpened(true); void control.load(); } }}>
    <summary>Запази структурата или повтори блок</summary>
    <p>Запазва се редът на методите и почивните дни. Натоварването се преизчислява според актуалната готовност, капацитета и календара. При необходимост алгоритъмът коригира и структурата.</p>
    {control.state && <ul className="proposal-list">{control.state.payload.training_structure.map(item => <li key={item.id}><div><strong>{item.name || (item.mode === "FIX" ? "Запазена структура" : item.mode === "SESSION" ? "Избран метод за деня" : "Повторен блок")}</strong><small>{dateLabel(item.start_date)} – {dateLabel(addProposalDays(item.start_date, item.days.length - 1))}</small></div>{canEdit && <div className="proposal-actions">
      {item.source && item.mode !== "SESSION" && <button className="action-button secondary" type="button" disabled={control.busy || disabled} onClick={() => change(() => { setMode(item.mode === "FIX" ? "FIX_STRUCTURE" : "REPEAT_STRUCTURE"); setSourceStart(item.source!.start_date); setLength(item.days.length); setTargetStart(item.start_date); setName(item.name); })}>Редактирай</button>}
      <button className="action-button secondary" type="button" disabled={control.busy || disabled} onClick={() => void control.remove({ action: "REMOVE_STRUCTURE", directive_id: item.id })}>Освободи структурата</button>
    </div>}</li>)}</ul>}
    {canEdit && control.state && <form onSubmit={submit}><fieldset className="proposal-form" disabled={control.busy || disabled}>
      <label>Действие<select value={mode} onChange={event => change(() => { setMode(event.target.value as typeof mode); if (event.target.value === "FIX_STRUCTURE") setLength(7); })}><option value="FIX_STRUCTURE">Запази структурата на седмицата</option><option value="REPEAT_STRUCTURE">Повтори блок</option></select></label>
      <label>Начало на изходния период<input required type="date" value={sourceStart} onChange={event => change(() => setSourceStart(event.target.value))}/></label>
      {mode === "REPEAT_STRUCTURE" && <label>Продължителност<select value={length} onChange={event => change(() => setLength(Number(event.target.value)))}><option value={7}>1 седмица · 7 дни</option><option value={14}>2 седмици · 14 дни</option></select></label>}
      {mode === "REPEAT_STRUCTURE" && <label>Повтори от<input required type="date" min={today} value={targetStart} onChange={event => change(() => setTargetStart(event.target.value))}/></label>}
      <label>Име (по желание)<input maxLength={120} value={name} onChange={event => change(() => setName(event.target.value))}/></label>
      <p className="proposal-wide">Изходен период: {sourceStart && addProposalDays(sourceStart, length - 1) ? `${dateLabel(sourceStart)} – ${dateLabel(addProposalDays(sourceStart, length - 1))}` : "избери начална дата"}. Прегледът проверява наличните запазени програми за всеки ден.</p>
      <div className="proposal-wide"><button className="action-button" type="submit">Прегледай предложението</button></div>
    </fieldset></form>}
    <ProposalFeedback control={control} disabled={disabled}/>
    {control.error && <button className="action-button secondary" type="button" disabled={control.busy} onClick={() => void control.load()}>Зареди актуалните данни</button>}
  </details>;
}
