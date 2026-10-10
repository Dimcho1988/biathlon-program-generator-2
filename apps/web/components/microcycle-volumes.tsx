"use client";

import { useState } from "react";
import { durationHms } from "../lib/duration-format";
import { microcycleVolume } from "../lib/microcycle-volume";
import { COMPONENTS, type Component, type PlanningDraft } from "../lib/training-management";

export interface VolumeWeek {
  start_date: string; end_date: string; accents: string[];
  volume_budget_minutes: number | null;
  cycle?: Record<string, unknown>;
  components: Partial<Record<Component, { target_period_q: number | null; target_index_7_40?: number | null }>>;
}
const dateLabel = (day: string) => `${day.slice(8, 10)}.${day.slice(5, 7)}.${day.slice(0, 4)}`;
const time = (v: number | null | undefined) => v == null ? "—" : durationHms(v);

export function MicrocycleVolumes({ weeks, sessions, status, metric = "volume" }: { weeks: VolumeWeek[]; sessions?: PlanningDraft; status: string; metric?: "volume" | "index" }) {
  const [mode, setMode] = useState<"targets" | "sessions">("targets");
  const index = metric === "index";
  return <section className="management-panel" aria-label="Обем по микроцикли">
    <h2>{index ? "Цели 7/40 по микроцикли" : "Обем по микроцикли"}</h2>
    {!index && <div className="management-view-switch" role="group" aria-label="Вид обем">
      <button type="button" aria-pressed={mode === "targets"} onClick={() => setMode("targets")}>Приравнен обем по зони</button>
      <button type="button" aria-pressed={mode === "sessions"} onClick={() => setMode("sessions")}>Продължителност на съставените сесии</button>
    </div>}
    <p className="management-muted">{index ? "Цели за съотношението 7/40, включително при непълен микроцикъл. Не са часове и не се събират в общ обем. Това са планови цели преди дневните ограничения." : mode === "targets"
      ? "Целеви приравнен обем в ч:мм:сс за точните дати на всеки ред. Непълните микроцикли съдържат само показаните дни. Това е приравнен обем, а не часовникова продължителност на тренировките. Дневната готовност и ограниченията могат да намалят съставените сесии."
      : "Време ч:мм:сс от отделните части на вече съставените сесии: загряване, основна работа, паузи и разпускане. Разпределението е по зададената зона на частта, а не по очакван измерен пулс. Бъдещ ден без съставена задача е неизвестен; планираната почивка е нула."}</p>
    {!index && mode === "sessions" && <p className="management-notice">{status}</p>}
    <div className="management-table-wrap"><table>
      <caption>{index ? "Планови индекси 7/40" : mode === "targets" ? "Приравнен обем по микроцикли · ч:мм:сс" : "Продължителност по микроцикли · ч:мм:сс"}</caption>
      <thead><tr><th>Период</th><th>Акценти / поддържане</th>{COMPONENTS.map(z => <th key={z}>{z === "STR" ? "Сила" : z}</th>)}
        {!index && <>{mode === "sessions" && <th>Неразпределено</th>}<th>{mode === "targets" ? "Общо приравнен обем" : "Обща продължителност"}</th>{mode === "sessions" && <th>Обхват</th>}</>}</tr></thead>
      <tbody>{weeks.map(w => {
        const v = microcycleVolume(sessions, w.start_date, w.end_date);
        return <tr key={w.start_date}>
          <th>{dateLabel(w.start_date)} – {dateLabel(w.end_date)}</th>
          <td>{w.cycle?.kind === "RECOVERY" ? `Разтоварване${w.accents.length ? ` · поддържане ${w.accents.join(", ")}` : ""}` : `${w.cycle?.kind === "STRESS" ? "Ударен · " : ""}${w.accents.join(", ") || "Без акцент"}`}</td>
          {COMPONENTS.map(z => <td key={z}>{index ? w.components[z]?.target_index_7_40 == null ? "—" : w.components[z]!.target_index_7_40!.toLocaleString("bg-BG",{minimumFractionDigits:2,maximumFractionDigits:2}) : mode === "targets" ? time(w.components[z]?.target_period_q) : v.available ? durationHms(v.components[z]) : "—"}</td>)}
          {!index && <>{mode === "sessions" && <td>{v.available ? durationHms(v.unallocated) : "—"}</td>}
            <td>{mode === "targets" ? COMPONENTS.every(z => w.components[z]?.target_period_q != null) ? durationHms(COMPONENTS.reduce((sum, z) => sum + w.components[z]!.target_period_q!, 0)) : "—" : v.available ? durationHms(v.total) : "—"}</td>
            {mode === "sessions" && <td>{v.available ? `${v.coveredDays} / ${v.expectedDays} дни${v.complete ? "" : " · частичен обем"}` : "Няма съставени сесии"}</td>}</>}
        </tr>;
      })}</tbody>
    </table></div>
  </section>;
}
