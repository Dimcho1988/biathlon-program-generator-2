"use client";
import { useEffect, useRef, useState } from "react";
import type { PlanningCalendarEvent } from "../lib/planning-calendar";
import { shortDay, type TimelineItem } from "../lib/planning-timeline";
import { planningWindow } from "../lib/planning-window";
import { PlanningTimeline } from "./planning-timeline";

export function PlanningRangeCalendar({ today, events, onSelect, items = [], initialView = "month", pending = false, hasPlan = false, actionLabel = "Добави събитие за периода" }: {
  today: string; events: PlanningCalendarEvent[]; onSelect: (start: string, end: string) => void;
  items?: TimelineItem[]; initialView?: "month" | "year"; pending?: boolean; hasPlan?: boolean; actionLabel?: string;
}) {
  const [page, setPage] = useState(0), [history, setHistory] = useState(false);
  const [view, setView] = useState(initialView);
  const [start, setStart] = useState<string | null>(null), [end, setEnd] = useState<string | null>(null);
  const drag = useRef<{ start: string; end: string } | null>(null), suppressClick = useRef(false);
  useEffect(() => {
    const release = () => { if (drag.current) suppressClick.current = drag.current.start !== drag.current.end; drag.current = null; };
    const cancel = () => { drag.current = null; suppressClick.current = false; };
    window.addEventListener("pointerup", release); window.addEventListener("pointercancel", cancel);
    return () => { window.removeEventListener("pointerup", release); window.removeEventListener("pointercancel", cancel); };
  }, []);
  const viewWindow = planningWindow(today, view, page, history);
  const selectionValid = !history && !!start && !!end && start >= today && end >= start;
  function choose(day: string) {
    if (history || day < today) return;
    if (suppressClick.current) { suppressClick.current = false; return; }
    if (!start || end || start < today) { setStart(day); setEnd(null); }
    else { setStart(day < start ? day : start); setEnd(day < start ? start : day); }
  }
  function switchHistory(value: boolean) {
    setHistory(value); setPage(0); setStart(null); setEnd(null); drag.current = null;
  }
  return <section className="season-calendar" aria-label="Избор на период в календара">
    <div className="season-toolbar">
      <div className="management-view-switch" aria-label="Планиране или история">
        <button type="button" aria-pressed={!history} onClick={() => switchHistory(false)}>Планиране</button>
        <button type="button" aria-pressed={history} onClick={() => switchHistory(true)}>История</button>
      </div>
      <div className="management-view-switch">{(["month", "year"] as const).map(v => <button type="button" key={v} aria-pressed={view === v} onClick={() => { setView(v); setPage(0); }}>{v === "month" ? "Месец" : "12 месеца"}</button>)}</div>
    </div>
    <p>{history ? "Историята е само за преглед. Нови събития и тренировъчни блокове се планират от днес напред." : "Маркирай период с влачене или избери начална и крайна дата. За един ден избери датата два пъти. След добавяне запази промените."}</p>
    <div className="season-toolbar"><div className="season-navigation">
      <button type="button" className="action-button secondary" disabled={!history && page === 0} onClick={() => setPage(p => p + (history ? 1 : -1))} aria-label={view === "month" ? "Предишен месец" : "Предишен период"}>←</button>
      <strong>{shortDay(viewWindow.start)} – {shortDay(viewWindow.end)}</strong>
      <button type="button" className="action-button secondary" disabled={history && page === 0} onClick={() => setPage(p => p + (history ? -1 : 1))} aria-label={view === "month" ? "Следващ месец" : "Следващ период"}>→</button>
      <button type="button" className="text-action" onClick={() => switchHistory(false)}>Днес</button>
    </div></div>
    <div className={`season-months ${view === "year" ? "season-year" : ""}`}>{viewWindow.months.map(m => {
      const firstDay = new Date(`${m}-01T12:00:00Z`), offset = (firstDay.getUTCDay() + 6) % 7;
      const count = new Date(Date.UTC(firstDay.getUTCFullYear(), firstDay.getUTCMonth() + 1, 0)).getUTCDate();
      return <section className="season-month" key={m} aria-label={m}>
        <h4>{firstDay.toLocaleDateString("bg-BG", { month: "long", year: "numeric", timeZone: "UTC" })}</h4>
        <div className="season-days">{["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"].map(d => <span className="season-weekday" key={d}>{d}</span>)}
          {Array.from({ length: offset }, (_, i) => <span key={`blank-${i}`}/>)}
          {Array.from({ length: count }, (_, i) => {
            const day = `${m}-${String(i + 1).padStart(2, "0")}`;
            if (day < viewWindow.start || day > viewWindow.end) return <span key={day} aria-hidden="true"/>;
            const selected = !history && !!start && start >= today && start <= day && day <= (end ?? start);
            const notes = items.filter(item => item.start <= day && day <= item.end);
            const names = [...new Set([...events.filter(e => e.start_date <= day && day <= e.end_date).map(e => e.name || e.event_type), ...notes.map(n => n.label)])];
            return <button type="button" key={day} aria-label={day + (names.length ? ": " + names.join(", ") : "")} aria-disabled={history} aria-current={day === today ? "date" : undefined} aria-pressed={selected} title={names.join(" · ")} className={`season-day ${notes.some(n => n.lane === "Периоди") ? "has-phase" : ""}`}
              onClick={() => choose(day)} onPointerDown={e => {
                suppressClick.current = false;
                if (!history && day >= today && e.button === 0 && e.pointerType !== "touch") drag.current = { start: day, end: day };
              }} onPointerEnter={e => {
                if (history || day < today || !drag.current || drag.current.start < today || e.buttons !== 1) return;
                drag.current.end = day;
                setStart(day < drag.current.start ? day : drag.current.start); setEnd(day < drag.current.start ? drag.current.start : day);
              }}><span>{i + 1}</span><span className="season-day-marks" aria-hidden="true">{[...new Set(notes.filter(n => n.lane !== "Периоди").map(n => n.tone))].slice(0, 3).map(t => <i key={t} className={`tone-${t}`}/>)}{!notes.length && names.length > 0 && <i className="tone-camp"/>}</span></button>;
          })}
        </div>
      </section>;
    })}</div>
    <div className="season-legend"><span><i className="tone-camp"/>Лагер</span><span><i className="tone-main_race"/>Основен старт</span><span><i className="tone-control_race"/>Контролен старт / тест</span><span><i className="tone-stress"/>Стресов блок</span><span><i className="tone-recovery"/>Разтоварване</span><span><i className="tone-accent"/>Акценти</span></div>
    {!history && <div className="season-toolbar"><p aria-live="polite">{start && start >= today ? end ? `${shortDay(start)} – ${shortDay(end)}` : `Начало: ${shortDay(start)}. Избери край.` : "Няма избран период."}</p><button type="button" className="action-button secondary" disabled={!selectionValid} onClick={() => { if (selectionValid) { onSelect(start!, end!); setStart(null); setEnd(null); } }}>{actionLabel}</button></div>}
    <PlanningTimeline items={items} start={viewWindow.start} end={viewWindow.end} pending={pending} hasPlan={hasPlan}/>
  </section>;
}
