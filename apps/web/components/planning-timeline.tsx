"use client";
import { useState } from "react";
import { shortDay, timelineRows, type TimelineItem } from "../lib/planning-timeline";

export function PlanningTimeline({ items, start, end, pending, hasPlan }: { items: TimelineItem[]; start: string; end: string; pending: boolean; hasPlan: boolean }) {
  const [selected, setSelected] = useState<string | null>(null);
  const current = items.find(i => i.id === selected);
  const span = Date.parse(end) - Date.parse(start) + 86400000;
  const months: string[] = [];
  for (let d = new Date(`${start.slice(0, 7)}-01T12:00:00Z`); d.toISOString().slice(0, 10) <= end; d.setUTCMonth(d.getUTCMonth() + 1)) months.push(d.toISOString().slice(0, 10));
  return <section className="season-timeline" aria-label="Времева линия на подготовката">
    <h3>Подготовката по една времева линия</h3>
    <p className="management-muted">{shortDay(start)} – {shortDay(end)}. Избери цветен отрязък за подробности.</p>
    {pending && <p role="status" className="management-notice">Има незаписани промени. Събитията и „Мои блокове“ показват редакциите; периодите и седмичните акценти са от последния запазен план. Запази, за да се преизчислят.</p>}
    {!hasPlan && <p>Автоматичният план още не е достъпен. Запази профила и провери внесената история; събитията можеш да добавиш сега.</p>}
    <div className="season-timeline-scroll"><div className="season-timeline-track">
      <div className="season-timeline-months">{months.map(m => <span key={m} style={{ left: `${Math.max(0, 100 * (Date.parse(m) - Date.parse(start)) / span)}%` }}>{new Date(`${m}T12:00:00Z`).toLocaleDateString("bg-BG", { month: "short", timeZone: "UTC" })}</span>)}</div>
      {(["Периоди", "Акценти", "Мои блокове", "Събития"] as const).map(lane => {
        const rows = timelineRows(items.filter(i => i.lane === lane), start, end);
        return <div className="season-timeline-lane" key={lane}><strong>{lane}</strong>{!rows.length && <p className="season-timeline-empty">{lane === "Мои блокове" ? "Следва автоматичния план" : "Няма записи в този период"}</p>}{rows.map((row, n) => <div className="season-timeline-row" key={n}>{row.map(i => <button type="button" className={`season-bar tone-${i.tone}`} key={i.id} style={{ left: `${i.left}%`, width: `${i.width}%` }} aria-label={`${i.label} · ${shortDay(i.start)} – ${shortDay(i.end)} · ${i.detail}`} title={`${i.label} · ${shortDay(i.start)} – ${shortDay(i.end)} · ${i.detail}`} onClick={() => setSelected(i.id)}><span>{i.label}</span></button>)}</div>)}</div>;
      })}
    </div></div>
    {current && <p className="season-selection" role="status"><strong>{current.label}</strong> · {shortDay(current.start)} – {shortDay(current.end)}<br/>{current.detail}</p>}
  </section>;
}
