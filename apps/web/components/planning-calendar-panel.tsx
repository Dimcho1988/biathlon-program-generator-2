"use client";

import { useState } from "react";
import {
  PLANNING_EVENT_TYPES,
  type PlanningCalendarEvent,
  type PlanningCalendarResponse,
  type PlanningEventType,
  type PlanningMissingInput,
} from "../lib/planning-calendar";

const eventTypeLabels: Record<PlanningEventType, string> = {
  MAIN_RACE: "Основно състезание",
  CONTROL_RACE: "Контролно състезание",
  CAMP: "Лагер",
  TEST: "Тест",
  UNAVAILABLE: "Недостъпен период",
};

const missingInputLabels: Record<PlanningMissingInput, string> = {
  PLANNING_PROFILE: "Профил за планиране",
  MESOCYCLE_ACCENTS: "Мезоциклични акценти",
  FUTURE_MAIN_RACE: "Реално бъдещо основно състезание",
  TRAINING_SNAPSHOT: "Актуален тренировъчен snapshot",
};

const emptyEvent = (): PlanningCalendarEvent => ({
  event_id: crypto.randomUUID(),
  event_type: "MAIN_RACE",
  name: "",
  start_date: "",
  end_date: "",
});

export function PlanningCalendarPanel({
  response,
}: {
  response: PlanningCalendarResponse;
}) {
  const [events, setEvents] = useState<PlanningCalendarEvent[]>(
    response.calendar?.events ?? [],
  );
  const context = response.context;
  const updateEvent = <Field extends keyof Omit<PlanningCalendarEvent, "event_id">>(
    eventId: string,
    field: Field,
    value: PlanningCalendarEvent[Field],
  ) => setEvents((current) => current.map((event) =>
    event.event_id === eventId
      ? { ...event, [field]: value }
      : event));

  const canonicalEvents = [...events].sort((left, right) =>
    left.start_date.localeCompare(right.start_date)
    || left.end_date.localeCompare(right.end_date)
    || left.event_type.localeCompare(right.event_type)
    || left.event_id.localeCompare(right.event_id));

  return <>
    <section className="planning-readiness-card" aria-labelledby="planning-readiness-title">
      <div className="accent-editor-heading">
        <div>
          <p className="eyebrow">Planning context · {context.schema_version}</p>
          <h2 id="planning-readiness-title">Готовност за генериране</h2>
        </div>
        <span className={`configuration-badge ${context.ready_for_generation ? "configured" : ""}`}>
          {context.ready_for_generation ? "Входовете са готови" : "Има липсващи входове"}
        </span>
      </div>
      {context.missing_inputs.length > 0
        ? <div className="readiness-missing">
          <p>Преди активиране на генератора са нужни:</p>
          <ul>{context.missing_inputs.map((item) => <li key={item}>{missingInputLabels[item]}</li>)}</ul>
        </div>
        : <p className="readiness-ok">
          Всички задължителни входове са налични. Това още не създава тренировъчна програма.
        </p>}
      <dl className="readiness-metadata">
        <div><dt>Генератор</dt><dd>Неактивен</dd></div>
        <div><dt>Методика</dt><dd>{context.methodology_version}</dd></div>
        <div><dt>Recovery основа</dt><dd>Само тренировъчно натоварване</dd></div>
        <div><dt>Wellness</dt><dd>Само диагностика</dd></div>
      </dl>
      {context.next_main_race && <p className="next-main-race">
        Следващ основен старт: <strong>{context.next_main_race.name}</strong>
        {" · "}{context.next_main_race.start_date}
      </p>}
      <p className="methodology-note">
        onFlows не създава виртуално състезание. Готовността означава само, че
        входовете са комплектовани; генераторът остава изключен до отделно одобрение.
      </p>
    </section>

    <section className="planning-calendar-card" aria-labelledby="planning-calendar-title">
      <div className="accent-editor-heading">
        <div>
          <p className="eyebrow">Индивидуален календар · planning-calendar-v1</p>
          <h2 id="planning-calendar-title">Ключови периоди и състезания</h2>
          <p className="muted">
            Събитията принадлежат само на активния спортист и не променят текущия анализ.
          </p>
        </div>
        <span className={`configuration-badge ${response.configured ? "configured" : ""}`}>
          {response.configured ? `${response.calendar?.events.length ?? 0} запазени` : "Незаписан"}
        </span>
      </div>
      <form className="planning-calendar-form" action="/api/athlete/planning-calendar" method="post">
        <input type="hidden" name="schema_version" value="planning-calendar-v1" />
        <input type="hidden" name="events_json" value={JSON.stringify(canonicalEvents)} />
        <div className="planning-calendar-events">
          {events.length === 0 && <p className="calendar-empty">
            Няма добавени събития. Добавете поне едно реално бъдещо основно състезание,
            за да се изпълни календарната проверка.
          </p>}
          {events.map((event) => <fieldset className="planning-calendar-event" key={event.event_id}>
            <legend>{eventTypeLabels[event.event_type]}</legend>
            <div className="planning-grid calendar-columns">
              <label>
                <span>Тип</span>
                <select
                  value={event.event_type}
                  onChange={(change) => updateEvent(
                    event.event_id,
                    "event_type",
                    change.target.value as PlanningEventType,
                  )}
                >
                  {PLANNING_EVENT_TYPES.map((eventType) => <option key={eventType} value={eventType}>
                    {eventTypeLabels[eventType]}
                  </option>)}
                </select>
              </label>
              <label>
                <span>Име</span>
                <input
                  value={event.name}
                  maxLength={120}
                  onChange={(change) => updateEvent(event.event_id, "name", change.target.value)}
                  required
                />
              </label>
              <label>
                <span>От</span>
                <input
                  type="date"
                  value={event.start_date}
                  onChange={(change) => updateEvent(event.event_id, "start_date", change.target.value)}
                  required
                />
              </label>
              <label>
                <span>До</span>
                <input
                  type="date"
                  min={event.start_date || undefined}
                  value={event.end_date}
                  onChange={(change) => updateEvent(event.event_id, "end_date", change.target.value)}
                  required
                />
              </label>
              <button
                className="text-action calendar-remove"
                type="button"
                onClick={() => setEvents((current) => current.filter(
                  (candidate) => candidate.event_id !== event.event_id,
                ))}
              >Премахни</button>
            </div>
          </fieldset>)}
        </div>
        <div className="calendar-actions">
          <button
            className="action-button secondary"
            type="button"
            disabled={events.length >= 100}
            onClick={() => setEvents((current) => [...current, emptyEvent()])}
          >Добави събитие</button>
          <button className="action-button" type="submit">Запази календара</button>
        </div>
      </form>
    </section>
  </>;
}
