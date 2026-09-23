"use client";
import { useEffect, useRef, useState } from "react";
import type { PlanningCalendarEvent } from "../lib/planning-calendar";
import type { TimelineItem } from "../lib/planning-timeline";
import { PlanningTimeline } from "./planning-timeline";

const iso = (d: Date) => d.toISOString().slice(0, 10);
export function PlanningRangeCalendar({ today, events, onSelect, items = [], initialView = "month", pending = false, hasPlan = false, actionLabel = "Добави събитие за периода" }: {
  today: string; events: PlanningCalendarEvent[]; onSelect: (start: string, end: string) => void;
  items?: TimelineItem[]; initialView?: "month" | "year"; pending?: boolean; hasPlan?: boolean; actionLabel?: string;
}) {
  const [month, setMonth] = useState(today.slice(0, 7));
  const [view, setView] = useState(initialView);
  const [start, setStart] = useState<string | null>(null), [end, setEnd] = useState<string | null>(null);
  const drag = useRef<{ start: string; end: string } | null>(null), suppressClick = useRef(false);
  useEffect(() => {
    const release = () => { if (drag.current) suppressClick.current = drag.current.start !== drag.current.end; drag.current = null; };
    const cancel = () => { drag.current = null; suppressClick.current = false; };
    window.addEventListener("pointerup", release); window.addEventListener("pointercancel", cancel);
    return () => { window.removeEventListener("pointerup", release); window.removeEventListener("pointercancel", cancel); };
  }, []);
  const first = new Date(`${month}-01T12:00:00Z`);
  const months = view === "year" ? Array.from({ length: 12 }, (_, i) => `${month.slice(0, 4)}-${String(i + 1).padStart(2, "0")}`) : [month];
  const viewStart = `${months[0]}-01`, viewEnd = iso(new Date(Date.UTC(Number(months.at(-1)!.slice(0, 4)), Number(months.at(-1)!.slice(5)), 0, 12)));
  function move(delta: number) { const d = new Date(first); d.setUTCMonth(d.getUTCMonth() + delta * (view === "year" ? 12 : 1)); setMonth(iso(d).slice(0, 7)); }
  function choose(day: string) {
    if (suppressClick.current) { suppressClick.current = false; return; }
    if (!start || end) { setStart(day); setEnd(null); }
    else { setStart(day < start ? day : start); setEnd(day < start ? start : day); }
  }
  return <section className="season-calendar" aria-label="Избор на период в календара">
    <p>Маркирай период с влачене на мишката или избери начална и крайна дата с две натискания. За един ден избери датата два пъти.</p>
    <div className="season-toolbar"><div className="management-view-switch">{(["month", "year"] as const).map(v => <button type="button" key={v} aria-pressed={view === v} onClick={() => setView(v)}>{v === "month" ? "Месец" : "Година"}</button>)}</div><div className="season-navigation"><button type="button" className="action-button secondary" onClick={() => move(-1)} aria-label={view === "year" ? "Предишна година" : "Предишен месец"}>←</button><strong>{view === "year" ? month.slice(0, 4) : first.toLocaleDateString("bg-BG", { month: "long", year: "numeric", timeZone: "UTC" })}</strong><button type="button" className="action-button secondary" onClick={() => move(1)} aria-label={view === "year" ? "Следваща година" : "Следващ месец"}>→</button><button type="button" className="text-action" onClick={() => setMonth(today.slice(0, 7))}>Днес</button></div></div>
    <div className={`season-months ${view === "year" ? "season-year" : ""}`}>{months.map(m => {
      const firstDay = new Date(`${m}-01T12:00:00Z`), offset = (firstDay.getUTCDay() + 6) % 7;
      const count = new Date(Date.UTC(firstDay.getUTCFullYear(), firstDay.getUTCMonth() + 1, 0)).getUTCDate();
      return <section className="season-month" key={m} aria-label={m}><h4>{firstDay.toLocaleDateString("bg-BG", { month: "long", year: "numeric", timeZone: "UTC" })}</h4><div className="season-days">{["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"].map(d => <span className="season-weekday" key={d}>{d}</span>)}{Array.from({ length: offset }, (_, i) => <span key={`blank-${i}`}/>)}{Array.from({ length: count }, (_, i) => {
        const day = `${m}-${String(i + 1).padStart(2, "0")}`, selected = !!start && start <= day && day <= (end ?? start);
        const notes = items.filter(item => item.start <= day && day <= item.end);
        const names = [...new Set([...events.filter(e => e.start_date <= day && day <= e.end_date).map(e => e.name || e.event_type), ...notes.map(n => n.label)])];
        return <button type="button" key={day} aria-label={day + (names.length ? ": " + names.join(", ") : "")} aria-current={day === today ? "date" : undefined} aria-pressed={selected} title={names.join(" · ")} className={`season-day ${notes.some(n => n.lane === "Периоди") ? "has-phase" : ""}`} onClick={() => choose(day)} onPointerDown={e => { suppressClick.current = false; if (e.button === 0 && e.pointerType !== "touch") drag.current = { start: day, end: day }; }} onPointerEnter={e => {
          if (!drag.current || e.buttons !== 1) return;
          drag.current.end = day;
          setStart(day < drag.current.start ? day : drag.current.start); setEnd(day < drag.current.start ? drag.current.start : day);
        }}><span>{i + 1}</span><span className="season-day-marks" aria-hidden="true">{[...new Set(notes.filter(n => n.lane !== "Периоди").map(n => n.tone))].slice(0, 3).map(t => <i key={t} className={`tone-${t}`}/>)}{!notes.length && names.length > 0 && <i className="tone-camp"/>}</span></button>;
      })}</div></section>;
    })}</div>
    <div className="season-legend"><span><i className="tone-camp"/>Лагер</span><span><i className="tone-main_race"/>Основен старт</span><span><i className="tone-control_race"/>Контролен старт / тест</span><span><i className="tone-stress"/>Стресов блок</span><span><i className="tone-recovery"/>Разтоварване</span><span><i className="tone-accent"/>Акценти</span></div>
    <div className="season-toolbar"><p aria-live="polite">{start ? end ? `${start} – ${end}` : `Начало: ${start}. Избери край.` : "Няма избран период."}</p><button type="button" className="action-button secondary" disabled={!start || !end} onClick={() => { if (start && end) { onSelect(start, end); setStart(null); setEnd(null); } }}>{actionLabel}</button></div>
    <PlanningTimeline items={items} start={viewStart} end={viewEnd} pending={pending} hasPlan={hasPlan}/>
  </section>;
}
