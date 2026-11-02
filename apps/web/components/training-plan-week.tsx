"use client";

import { Fragment, useId, useState, type CSSProperties, type ReactNode } from "react";
import { durationHms } from "../lib/duration-format";
import { daySessions, type DraftDay } from "../lib/training-management";
import { componentColor, sessionSummary, SPORT_LABELS, workoutTitle } from "../lib/training-visuals";
import { ComponentLegend, SessionAccent, WorkoutProfile } from "./workout-profile";
import { SessionRole } from "./session-role";
import { planDates, type PlanWeekRange } from "../lib/plan-week-view";

const dayLabel = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString("bg-BG", { weekday: "short", day: "numeric", month: "numeric", timeZone: "UTC" });
const REST_LABELS: Record<string, string> = { REST: "Почивка", SKIPPED: "Пропусната", UNAVAILABLE: "Свободен ден", EXISTING_ACTIVITY: "Изпълнена активност", RACE: "Състезание", REVIEW_REQUIRED: "Ще се уточни" };

export function TrainingPlanWeek({ days, today, renderDay, period, forecastDates = [], programmeLabel }: { days: DraftDay[]; today: string; renderDay: (day: DraftDay) => ReactNode; period?: PlanWeekRange; forecastDates?: string[]; programmeLabel?: string }) {
  const [selected, setSelected] = useState<string | null>(today);
  const detailId = useId();
  const dates = period ? planDates(period.start,period.end) : days.map(d => d.date);
  const currentDate = selected === null ? null : dates.includes(selected) ? selected : dates.find(d => d >= today) ?? dates[0];
  if (!dates.length) return null;
  const missingText = (date: string) => date < today ? "Изминал ден · изпълнението е в отчета" : "Няма изчислени задачи · нужно е обновяване";
  const toggle = (date: string) => setSelected(date === currentDate ? null : date);
  const details = (date: string, day?: DraftDay) => date === currentDate && <div id={detailId} className="management-selected-day" role="region" aria-label={`Подробности за ${dayLabel(date)}`}>
    {forecastDates.includes(date) && <p className="management-notice">Прогноза за {dayLabel(date)} · задачите предстои да се уточнят според новото изпълнение и възстановяването.</p>}
    {day ? renderDay(day) : <p>{missingText(date)}.</p>}
  </div>;
  return <div className="management-week-view">
    <ComponentLegend/>
    <div className="management-workout-list" role="group" aria-label="Избери ден от програмата">{dates.map(date => {
      const day = days.find(d => d.date === date);
      const provisional = forecastDates.includes(date);
      if (!day) return <Fragment key={date}><button type="button" className="management-workout-day" aria-pressed={date === currentDate} aria-expanded={date === currentDate} aria-controls={date === currentDate ? detailId : undefined} onClick={() => toggle(date)}><span className="management-workout-date"><strong>{dayLabel(date)}</strong></span><span className="management-workout-rest">{missingText(date)}</span></button>{details(date)}</Fragment>;
      const sessions = daySessions(day);
      return <Fragment key={day.date}><button type="button" className={`management-workout-day${provisional ? " management-forecast-day" : ""}`} aria-pressed={day.date === currentDate} aria-expanded={day.date === currentDate} aria-controls={day.date === currentDate ? detailId : undefined} onClick={() => toggle(day.date)}>
        <span className="management-workout-date"><strong>{day.date === today ? "Днес" : dayLabel(day.date)}</strong>{(provisional || programmeLabel) && <small className="management-day-source">{provisional ? "Прогноза" : programmeLabel}</small>}{sessions.length > 1 && <small>{sessions.length} сесии · {durationHms(sessions.reduce((total, session) => total + session.total_minutes, 0))} общо</small>}</span>
        <span className="management-workout-sessions">{sessions.length ? sessions.map((session, index) => <span className="management-workout-session" key={index} style={{ "--session-color": componentColor(session.zone) } as CSSProperties}>
          <span className="management-workout-meta"><span>{SPORT_LABELS[session.sport] ?? session.sport}{sessions.length > 1 ? ` · ${index + 1}` : ""}</span><strong>{durationHms(session.total_minutes)}</strong><SessionAccent zone={session.zone}/></span>
          <span className="management-workout-shape"><strong>{workoutTitle(session)}</strong><SessionRole session={session} day={day}/><span className="workout-summary">{sessionSummary(session)}</span><WorkoutProfile session={session} compact/></span>
        </span>) : <span className="management-workout-rest">{(day.time_limit_exhausted ? "Изчерпан лимит за време" : REST_LABELS[day.status]) ?? "Ден от програмата"}</span>}</span>
        {!!day.actual_sessions && <small>Вече изпълнени: {day.actual_sessions} · {durationHms(day.actual_minutes ?? 0)}{sessions.length > 0 && `; предложени още: ${sessions.length}`}</small>}
        <span className="management-workout-open">{day.date === currentDate ? "Скрий подробностите" : "Покажи подробности"}</span>
      </button>{details(date, day)}</Fragment>;
    })}</div>
  </div>;
}
