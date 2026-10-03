"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { PlanningCycleEditor } from "./planning-cycle-editor";
import { timelineItems, CYCLE_LABELS } from "../lib/planning-timeline";
import { parseManagementOutlook, type ManagementOutlook, type ManagementProfile, type PlanProjection, type CycleDirective } from "../lib/training-management";
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
  today, profile, onProfileChange, onSaveProfile, profileDirty = false, plan, disabled = false, onOutlookChange, onPendingChange,
}: {
  response: PlanningCalendarResponse;
  today?: string;
  profile?: ManagementProfile; onProfileChange?: (p: ManagementProfile) => void;
  onSaveProfile?: () => Promise<boolean>; profileDirty?: boolean; plan?: PlanProjection | null; disabled?: boolean;
  onOutlookChange?: (plan: ManagementOutlook | null) => void; onPendingChange?: (pending: boolean) => void;
}) {
  const formId = useId();
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [displayPlan, setDisplayPlan] = useState(plan), [observedPlan, setObservedPlan] = useState(plan);
  if (plan !== observedPlan) { setObservedPlan(plan); setDisplayPlan(plan); }
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
  const pending = eventsDirty || profileDirty || needsRefresh || busy;
  useEffect(() => { onPendingChange?.(pending); }, [pending, onPendingChange]);
  const items = timelineItems(events, profile?.planning_controls?.cycles ?? [], pending ? null : displayPlan);
  function addRange(start_date: string, end_date: string) {
    setError(""); setNotice("");
    if (today && start_date < today) { setError("Нови събития и блокове се планират от днес напред."); return; }
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
      if (today && canonicalEvents.some(event => event.start_date < today &&
          !savedEvents.some(saved => saved.event_id === event.event_id && saved.start_date === event.start_date))) {
        throw new Error("Ново събитие започва най-рано днес. Запазената история остава непроменена.");
      }
      if (profileDirty && onSaveProfile) {
        if (!await onSaveProfile()) { setError("Профилът и блоковете не са записани. Провери съобщението в профила по-горе; въведеното е запазено във формата."); return; }
        if (!mounted.current) return;
        savedProfile = true;
      }
      if (eventsDirty) {
        const body = new FormData(); body.set("schema_version", "planning-calendar-v1"); body.set("events_json", JSON.stringify(canonicalEvents));
        const result = await fetch("/api/athlete/planning-calendar", { method: "POST", headers: { Accept: "application/json" }, body });
        if (!result.ok || !result.headers.get("content-type")?.includes("application/json") || !(await result.json()).saved) throw new Error("Събитията не бяха записани. Провери достъпа и датите и опитай отново.");
        if (!mounted.current) return;
        setSavedEvents(canonicalEvents);
      }
      if (profile?.discipline.trim()) {
        setNeedsRefresh(true);
        try {
          const result = await fetch("/api/athlete/management/outlook", { cache: "no-store", signal: AbortSignal.timeout(80_000) });
          if (!result.ok) throw new Error();
          const updated = parseManagementOutlook(await result.json());
          if (!updated) throw new Error();
          if (!mounted.current) return;
          setDisplayPlan(updated); onOutlookChange?.(updated); setNeedsRefresh(false);
          setNotice("Запазено. Периодите и акцентите са обновени. Седмичната програма ще провери промените при отваряне.");
        } catch {
          setError("Промените са запазени, но периодите не успяха да се обновят. Натисни „Обнови периодите“ — не е необходимо да въвеждаш събитията отново.");
        }
      } else {
        setNotice("Календарът е запазен. Попълни профила, за да получиш автоматична периодизация.");
      }
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
        <span className={`configuration-badge ${!eventsDirty && savedEvents.length ? "configured" : ""}`}>
          {eventsDirty || profileDirty ? "Незаписани промени" : `${savedEvents.length} запазени събития`}
        </span>
      </div>
      {error && <p className="management-error" role="alert">{error}</p>}
      {notice && !eventsDirty && !profileDirty && <p className="management-notice" role="status">{notice}</p>}
      <fieldset disabled={busy || disabled}>
      <div className="calendar-save-status" role="status">
        <p>{busy ? "Запазваме и обновяваме периодите…" : eventsDirty || profileDirty ? "Има незаписани промени. Запази ги, за да изчислим периодите спрямо новите стартове и настройки." : needsRefresh ? "Календарът е запазен. Периодите очакват обновяване." : "Периодите следват запазените стартове и настройки."}</p>
        <button form={formId} className="action-button" type="submit" disabled={!eventsDirty && !profileDirty && !needsRefresh}>{busy ? "Обновяване…" : needsRefresh && !eventsDirty && !profileDirty ? "Обнови периодите" : "Запази и обнови периодите"}</button>
      </div>
      <label className="season-kind">Какво добавям?<select value={kind} onChange={e => setKind(e.target.value)}><optgroup label="Събития">{PLANNING_EVENT_TYPES.map(k => <option key={k} value={k}>{eventTypeLabels[k]}</option>)}</optgroup>{profile?.planning_controls && onProfileChange && <optgroup label="Натоварване и акценти">{Object.entries(CYCLE_LABELS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}</optgroup>}</select></label>
      {today && <PlanningRangeCalendar today={today} events={events} items={items} initialView="year" pending={pending} hasPlan={!!displayPlan} onSelect={addRange} actionLabel="Добави избрания период"/>}
      <form id={formId} className="planning-calendar-form" action="/api/athlete/planning-calendar" method="post" onSubmit={saveCalendar}>
        <input type="hidden" name="schema_version" value="planning-calendar-v1" />
        <input type="hidden" name="events_json" value={JSON.stringify(canonicalEvents)} />
        <div className="planning-calendar-events">
          {events.length === 0 && <p className="calendar-empty">
            Все още няма събития. Можеш да продължиш без тях; подготовката няма да бъде насочена към конкретен основен старт.
          </p>}
          {events.map((event) => <fieldset className="planning-calendar-event" key={event.event_id} disabled={!!today && !!event.end_date && event.end_date < today && savedEvents.some(saved => saved.event_id === event.event_id)}>
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
                  min={savedEvents.find(saved => saved.event_id === event.event_id)?.start_date === event.start_date && today && event.start_date < today ? event.start_date : today}
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
          <button className="action-button" type="submit" disabled={!eventsDirty && !profileDirty && !needsRefresh}>{busy ? "Обновяване…" : needsRefresh && !eventsDirty && !profileDirty ? "Обнови периодите" : "Запази и обнови периодите"}</button>
        </div>
      </form>
      </fieldset>
    </section>
  </>;
}
