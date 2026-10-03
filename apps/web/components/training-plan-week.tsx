"use client";

import { useId, useState, type CSSProperties, type ReactNode } from "react";
import { durationHms } from "../lib/duration-format";
import { daySessions, type DraftDay } from "../lib/training-management";
import { componentColor, sessionSummary, SPORT_LABELS } from "../lib/training-visuals";
import { ComponentLegend, SessionAccent, WorkoutProfile } from "./workout-profile";

const dayLabel = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString("bg-BG", { weekday: "short", day: "numeric", month: "numeric", timeZone: "UTC" });
const REST_LABELS: Record<string, string> = { REST: "Почивка", SKIPPED: "Пропусната", UNAVAILABLE: "Свободен ден", EXISTING_ACTIVITY: "Изпълнена активност", RACE: "Състезание", REVIEW_REQUIRED: "Ще се уточни" };

export function TrainingPlanWeek({ days, today, renderDay }: { days: DraftDay[]; today: string; renderDay: (day: DraftDay) => ReactNode }) {
  const [selected, setSelected] = useState(today);
  const detailId = useId();
  const current = days.find(d => d.date === selected) ?? days.find(d => d.date >= today) ?? days[0];
  if (!current) return null;
  return <div className="management-week-view">
    <ComponentLegend/>
    <div className="management-workout-list" role="group" aria-label="Избери ден от програмата">{days.map(day => {
      const sessions = daySessions(day);
      return <button type="button" className="management-workout-day" key={day.date} aria-pressed={day.date === current.date} aria-controls={detailId} onClick={() => setSelected(day.date)}>
        <span className="management-workout-date"><strong>{day.date === today ? "Днес" : dayLabel(day.date)}</strong>{sessions.length > 1 && <small>{sessions.length} сесии · {durationHms(sessions.reduce((total, session) => total + session.total_minutes, 0))} общо</small>}</span>
        <span className="management-workout-sessions">{sessions.length ? sessions.map((session, index) => <span className="management-workout-session" key={index} style={{ "--session-color": componentColor(session.zone) } as CSSProperties}>
          <span className="management-workout-meta"><span>{SPORT_LABELS[session.sport] ?? session.sport}{sessions.length > 1 ? ` · ${index + 1}` : ""}</span><strong>{durationHms(session.total_minutes)}</strong><SessionAccent zone={session.zone}/></span>
          <span className="management-workout-shape"><strong>{session.title}</strong><span className="workout-summary">{sessionSummary(session)}</span><WorkoutProfile session={session} compact/></span>
        </span>) : <span className="management-workout-rest">{(day.time_limit_exhausted ? "Изчерпан лимит за време" : REST_LABELS[day.status]) ?? "Ден от програмата"}</span>}</span>
        <span className="management-workout-open">{day.date === current.date ? "Избран ден · подробности по-долу" : "Покажи подробности"}</span>
      </button>;
    })}</div>
    <div id={detailId} className="management-selected-day" aria-live="polite">{renderDay(current)}</div>
  </div>;
}
