"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { PlanningCycleEditor } from "./planning-cycle-editor";
import { timelineItems, CYCLE_LABELS } from "../lib/planning-timeline";
import type { ManagementProfile, PlanProjection, CycleDirective } from "../lib/training-management";
import { PlanningRangeCalendar } from "./planning-range-calendar";
import {
  PLANNING_EVENT_TYPES,
  parsePlanningCalendarInput,
  type PlanningCalendarEvent,
  type PlanningCalendarResponse,
  type PlanningEventType,
} from "../lib/planning-calendar";

const eventTypeLabels: Record<PlanningEventType, string> = {
  MAIN_RACE: "Основно състезание",
  CONTROL_RACE: "Контролно състезание",
  CAMP: "Лагер",
  TEST: "Тест",
  UNAVAILABLE: "Недостъпен период",
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
  today, profile, onProfileChange, onSaveProfile, profileDirty = false, plan, disabled = false,
}: {
  response: PlanningCalendarResponse;
  today?: string;
  profile?: ManagementProfile; onProfileChange?: (p: ManagementProfile) => void;
  onSaveProfile?: () => Promise<boolean>; profileDirty?: boolean; plan?: PlanProjection | null; disabled?: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [kind, setKind] = useState<string>("CAMP");
  const [observedCalendar, setObservedCalendar] = useState(JSON.stringify(response.calendar?.events ?? []));
  const [savedEvents, setSavedEvents] = useState(response.calendar?.events ?? []);
  const [events, setEvents] = useState<PlanningCalendarEvent[]>(
    response.calendar?.events ?? [],
  );
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


  const eventsDirty = JSON.stringify(canonicalEvents) !== JSON.stringify(savedEvents);
  const incoming = response.calendar?.events ?? [];
  if (!busy && JSON.stringify(incoming) !== observedCalendar) {
    setObservedCalendar(JSON.stringify(incoming));
    if (!eventsDirty) { setEvents(incoming); setSavedEvents(incoming); }
  }
  const items = timelineItems(events, profile?.planning_controls?.cycles ?? [], plan);
  function addRange(start_date: string, end_date: string) {
    setError(""); setNotice("");
    if (PLANNING_EVENT_TYPES.includes(kind as PlanningEventType)) {
      if (events.length >= 100) { setError("Календарът допуска до 100 събития."); return; }
      setEvents(current => [...current, { ...emptyEvent(), event_type: kind as PlanningEventType, name: eventTypeLabels[kind as PlanningEventType], start_date, end_date }]);
    } else if (profile?.planning_controls && onProfileChange) {
      const c = profile.planning_controls, type = kind as CycleDirective["kind"];
      if (c.cycles.length >= 52) { setError("Допускат се до 52 тренировъчни блока."); return; }
      if (Date.parse(end_date) - Date.parse(start_date) >= (type === "STRESS" ? 7 : 42) * 86400000) {
        setError(type === "STRESS" ? "За стресов микроцикъл избери до 7 дни. След него се добавя разтоварване." : "Избери блок до 42 дни."); return;
      }
      onProfileChange({ ...profile, planning_controls: { ...c, cycles: [...c.cycles, {
        start_date, end_date, name: CYCLE_LABELS[type], kind: type,
        accents: c.accents.length ? [...c.accents] : ["Z2", "Z3"],
        target_index: type === "RECOVERY" ? .8 : type === "STRESS" ? Math.max(1.1, c.accent_index) : type === "MAINTAIN" ? 1 : c.accent_index,
        volume_factor: 1, recovery_days: 7,
      }] } });
    }
  }
  async function saveCalendar(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); setBusy(true); setError(""); setNotice("");
    let savedProfile = false;
    try {
      parsePlanningCalendarInput({ schema_version: "planning-calendar-v1", events: canonicalEvents });
      if (profileDirty && onSaveProfile) {
        if (!await onSaveProfile()) { setError("Профилът и блоковете не са записани. Провери съобщението в профила по-горе; въведеното е запазено във формата."); return; }
        savedProfile = true;
      }
      if (eventsDirty) {
        const body = new FormData(); body.set("schema_version", "planning-calendar-v1"); body.set("events_json", JSON.stringify(canonicalEvents));
        const result = await fetch("/api/athlete/planning-calendar", { method: "POST", body });
        if (!result.ok || new URL(result.url, window.location.origin).searchParams.get("planning") !== "calendar-saved") throw new Error("Събитията не бяха записани. Провери достъпа и датите и опитай отново.");
        setSavedEvents(canonicalEvents);
      }
      setNotice("Календарът и блоковете са запазени. Обновяваме автоматичните периоди и акценти."); router.refresh();
    } catch (caught) { setError((savedProfile ? "Профилът и блоковете са записани, но календарните събития не са. " : "") + (caught instanceof Error ? caught.message : "Записът не завърши. Въведеното остава във формата.")); }
    finally { setBusy(false); }
  }

  return <>
    <section className="planning-calendar-card" aria-labelledby="planning-calendar-title">
      <div className="accent-editor-heading">
        <div>
          <p className="eyebrow">Цялата подготовка на едно място</p>
          <h2 id="planning-calendar-title">Календар на подготовката</h2>
          <p className="muted">
            Маркирай лагер, старт или тренировъчен блок. Цветните отрязъци показват ръчните акценти и плана от запазените настройки.
          </p>
        </div>
        <span className={`configuration-badge ${response.configured ? "configured" : ""}`}>
          {response.configured ? `${response.calendar?.events.length ?? 0} запазени` : "Незаписан"}
        </span>
      </div>
      <fieldset disabled={busy || disabled}>
      <label className="season-kind">Какво добавям?<select value={kind} onChange={e => setKind(e.target.value)}><optgroup label="Събития">{PLANNING_EVENT_TYPES.map(k => <option key={k} value={k}>{eventTypeLabels[k]}</option>)}</optgroup>{profile?.planning_controls && onProfileChange && <optgroup label="Натоварване и акценти">{Object.entries(CYCLE_LABELS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}</optgroup>}</select></label>
      {today && <PlanningRangeCalendar today={today} events={events} items={items} initialView="year" pending={eventsDirty || profileDirty} hasPlan={!!plan} onSelect={addRange} actionLabel="Добави избрания период"/>}
      <form className="planning-calendar-form" action="/api/athlete/planning-calendar" method="post" onSubmit={saveCalendar}>
        <input type="hidden" name="schema_version" value="planning-calendar-v1" />
        <input type="hidden" name="events_json" value={JSON.stringify(canonicalEvents)} />
        <div className="planning-calendar-events">
          {events.length === 0 && <p className="calendar-empty">
            Все още няма събития. Можеш да продължиш без тях; подготовката няма да бъде насочена към конкретен основен старт.
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
        {profile && onProfileChange && <PlanningCycleEditor profile={profile} onChange={onProfileChange}/>}
        <div className="calendar-actions">
          <button
            className="action-button secondary"
            type="button"
            disabled={events.length >= 100}
            onClick={() => setEvents((current) => [...current, emptyEvent()])}
          >Добави събитие</button>
          <button className="action-button" type="submit" disabled={!eventsDirty && !profileDirty}>{busy ? "Запазване…" : "Запази календара"}</button>
        </div>
      </form>
      </fieldset>
      {error && <p className="management-error" role="alert">{error}</p>}
      {notice && !eventsDirty && !profileDirty && <p className="management-notice" role="status">{notice}</p>}
    </section>
  </>;
}
