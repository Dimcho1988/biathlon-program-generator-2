"use client";

import { useState, type ReactNode } from "react";
import { durationHms } from "../lib/duration-format";
import { daySessions, type DraftDay } from "../lib/training-management";

const dayLabel = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString("bg-BG", { weekday: "short", day: "numeric", month: "numeric", timeZone: "UTC" });
const REST_LABELS: Record<string, string> = { REST: "Почивка", SKIPPED: "Пропусната", UNAVAILABLE: "Свободен ден", EXISTING_ACTIVITY: "Изпълнена активност", RACE: "Състезание", REVIEW_REQUIRED: "Ще се уточни" };

export function TrainingPlanWeek({ days, today, renderDay }: { days: DraftDay[]; today: string; renderDay: (day: DraftDay) => ReactNode }) {
  const [selected, setSelected] = useState(today);
  const current = days.find(d => d.date === selected) ?? days.find(d => d.date >= today) ?? days[0];
  if (!current) return null;
  return <div className="management-week-view">
    <div className="management-week-list" role="group" aria-label="Избери ден от програмата">{days.map(day => <button type="button" key={day.date} aria-pressed={day.date === current.date} onClick={() => setSelected(day.date)}>
      <span className="management-week-date">{day.date === today ? "Днес" : dayLabel(day.date)}</span>
      <span>{daySessions(day).length > 1 ? daySessions(day).length+" сесии" : daySessions(day)[0]?.title ?? (day.time_limit_exhausted ? "Изчерпан лимит за време" : REST_LABELS[day.status]) ?? "Ден от програмата"}</span>
      {daySessions(day).length > 0 && <small>{durationHms(daySessions(day).reduce((total, session)=>total+session.total_minutes,0))}</small>}
    </button>)}</div>
    <div className="management-selected-day" aria-live="polite">{renderDay(current)}</div>
  </div>;
}
