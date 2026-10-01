"use client";

import { useState } from "react";
import { SpeedWorkReport } from "./speed-work-report";
import { durationHms } from "../lib/duration-format";
import type { CompletedWork } from "../lib/completed-work";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
const decimal = (value: number) => number.format(value);
const date = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));

export function CompletedWorkSection({ report, message, selectable = false, availablePeriodStart, availablePeriodEnd, generation = null, revision = null, initialSource = "hr", allowSpeed = false }: { report: CompletedWork | null; message?: string; selectable?: boolean; availablePeriodStart?: string; availablePeriodEnd?: string; generation?: string | null; revision?: number | null; initialSource?: "hr" | "speed"; allowSpeed?: boolean }) {
  const [source, setSource] = useState<"hr" | "speed">(selectable && allowSpeed ? initialSource : "hr");
  if (!report) return message ? (
    <section className="completed-work-section" aria-labelledby="completed-work-title">
      <div className="section-heading"><div><p className="section-kicker">Извършено натоварване</p><h2 id="completed-work-title">Отчет за извършеното натоварване</h2></div></div>
      <p className="history-unavailable">{message}</p>
    </section>
  ) : null;

  return (
    <section className="completed-work-section" aria-labelledby="completed-work-title">
      <div className="section-heading">
        <div><p className="section-kicker">{date(report.period_start)} — {date(report.period_end)}</p><h2 id="completed-work-title">Отчет за извършеното натоварване</h2></div>
        {source === "hr" && <p>{report.quality.modeled_activities} моделирани активности · {report.quality.limited_activities} с ограничено HR покритие</p>}
      </div>

      {selectable && allowSpeed && <div className="load-source-switch" role="group" aria-label="Основа на отчета">
        <button type="button" aria-pressed={source === "hr"} onClick={() => setSource("hr")}>По пулс</button>
        <button type="button" aria-pressed={source === "speed"} onClick={() => setSource("speed")}>По скорост</button>
      </div>}
      {selectable && <form className="report-period" method="get">
        <input type="hidden" name="view" value="report" />
        <input type="hidden" name="report_source" value={source} />
        <label>От <input type="date" name="report_start" defaultValue={report.period_start} min={availablePeriodStart ?? report.period_start} max={availablePeriodEnd ?? report.period_end} required /></label>
        <label>До <input type="date" name="report_end" defaultValue={report.period_end} min={availablePeriodStart ?? report.period_start} max={availablePeriodEnd ?? report.period_end} required /></label>
        <button className="action-button secondary" type="submit">Покажи периода</button>
      </form>}

      {source === "speed" ? <SpeedWorkReport key={`${report.athlete_id}:${generation}:${revision}:${report.period_start}:${report.period_end}`} start={report.period_start} end={report.period_end} generation={generation} revision={revision} totalDuration={report.totals.activity_duration_min}/> : <>
      <p className="report-note">Всички продължителности са във формат ч:мм:сс. Приравненото време е към горната пулсова граница на зоната; ефективният товар E е отделна моделна величина.</p>
      <div className="report-totals">
        <dl><div><dt>Продължителност на активностите</dt><dd>{durationHms(report.totals.activity_duration_min)}</dd></div><div><dt>HR-зонирано реално време</dt><dd>{durationHms(report.totals.zoned_hr_time_min)}</dd></div></dl>
        {report.quality.missing_duration_activities > 0 && <p className="quality-limited">{report.quality.missing_duration_activities} активности са без надеждна обща продължителност и не са заместени с предполагаема стойност.</p>}
      </div>

      <div className="report-table-wrap"><table>
        <caption>Натоварване по пулсови зони</caption>
        <thead><tr><th>Зона</th><th>Реално време</th><th>Еквивалентно време</th><th>Ефективен товар E</th></tr></thead>
        <tbody>{report.zones.map((zone) => <tr key={zone.zone}><th>{zone.zone}</th><td>{durationHms(zone.raw_time_min)}</td><td>{durationHms(zone.equivalent_time_min)}</td><td>{decimal(zone.effective_load)}</td></tr>)}</tbody>
      </table></div>

      <div className="report-table-wrap"><table>
        <caption>По вид активност от Intervals</caption>
        <thead><tr><th>Етикет от източника</th><th>Активности</th><th>Продължителност</th><th>HR-зонирано време</th></tr></thead>
        <tbody>{report.sports.length > 0 ? report.sports.map((sport) => <tr key={sport.sport}><th>{sport.sport}</th><td>{sport.activities_count}</td><td>{durationHms(sport.activity_duration_min)}</td><td>{durationHms(sport.zoned_hr_time_min)}</td></tr>) : <tr><td colSpan={4}>Няма моделирани активности в избрания период.</td></tr>}</tbody>
      </table></div>
      <p className="report-note">Видовете активности са показани с точните етикети от Intervals. Те не са автоматично интерпретирани като научна класификация на тренировъчните средства.</p>
    </>}
    </section>
  );
}
