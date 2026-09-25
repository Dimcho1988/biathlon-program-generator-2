"use client";

import { useState } from "react";
import { durationHms } from "../lib/duration-format";
import { microcycleVolume } from "../lib/microcycle-volume";
import { COMPONENTS, type Component, type PlanningDraft } from "../lib/training-management";

export interface VolumeWeek {
  start_date: string; end_date: string; accents: string[];
  volume_budget_minutes: number | null;
  cycle?: Record<string, unknown>;
  components: Partial<Record<Component, { target_period_effective: number | null }>>;
}
const dateLabel = (day: string) => `${day.slice(8, 10)}.${day.slice(5, 7)}.${day.slice(0, 4)}`;
const number = (v: number | null | undefined) => v == null ? "—" : v.toLocaleString("bg-BG", { maximumFractionDigits: 1 });

export function MicrocycleVolumes({ weeks, sessions, status }: { weeks: VolumeWeek[]; sessions?: PlanningDraft; status: string }) {
  const [mode, setMode] = useState<"targets" | "sessions">("targets");
  return <section className="management-panel" aria-label="Обем по микроцикли">
    <h2>Обем по микроцикли</h2>
    <div className="management-view-switch" role="group" aria-label="Вид обем">
      <button type="button" aria-pressed={mode === "targets"} onClick={() => setMode("targets")}>Целеви обем по компоненти</button>
      <button type="button" aria-pressed={mode === "sessions"} onClick={() => setMode("sessions")}>Време от съставените сесии</button>
    </div>
    <p className="management-muted">{mode === "targets"
      ? "Целеви ефективен обем E в приравнени минути за точните дати на всеки ред. Непълните микроцикли не са преизчислени като цели седмици. Компонентите включват разливане и не се събират като часове тренировка. Прогнозното време е ориентир при досегашната структура."
      : "Време ч:мм:сс от отделните части на вече съставените сесии: загряване, основна работа, паузи и разпускане. Разпределението е по зададената зона на частта, а не по очакван измерен пулс. Бъдещ ден без съставена задача е неизвестен; планираната почивка е нула."}</p>
    {mode === "sessions" && <p className="management-notice">{status}</p>}
    <div className="management-table-wrap"><table>
      <caption>{mode === "targets" ? "Цели за всеки микроцикъл · приравнени минути E" : "Съставени сесии по микроцикли · ч:мм:сс"}</caption>
      <thead><tr><th>Период</th><th>Акценти / поддържане</th>{COMPONENTS.map(z => <th key={z}>{z === "STR" ? "Сила" : z}</th>)}
        {mode === "sessions" && <th>Неразпределено</th>}<th>{mode === "targets" ? "Прогнозно време" : "Общо време"}</th>{mode === "sessions" && <th>Обхват</th>}</tr></thead>
      <tbody>{weeks.map(w => {
        const v = microcycleVolume(sessions, w.start_date, w.end_date);
        return <tr key={w.start_date}>
          <th>{dateLabel(w.start_date)} – {dateLabel(w.end_date)}</th>
          <td>{w.cycle?.kind === "RECOVERY" ? `Разтоварване${w.accents.length ? ` · поддържане ${w.accents.join(", ")}` : ""}` : w.accents.join(", ") || "Без акцент"}</td>
          {COMPONENTS.map(z => <td key={z}>{mode === "targets" ? number(w.components[z]?.target_period_effective) : v.available ? durationHms(v.components[z]) : "—"}</td>)}
          {mode === "sessions" && <td>{v.available ? durationHms(v.unallocated) : "—"}</td>}
          <td>{mode === "targets" ? w.volume_budget_minutes === null ? "—" : durationHms(w.volume_budget_minutes) : v.available ? durationHms(v.total) : "—"}</td>
          {mode === "sessions" && <td>{v.available ? `${v.coveredDays} / ${v.expectedDays} дни${v.complete ? "" : " · частичен обем"}` : "Няма съставени сесии"}</td>}
        </tr>;
      })}</tbody>
    </table></div>
  </section>;
}
