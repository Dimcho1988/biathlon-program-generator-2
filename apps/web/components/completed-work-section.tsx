import type { CompletedWork } from "../lib/completed-work";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
const decimal = (value: number) => number.format(value);
const date = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));

export function CompletedWorkSection({ report, message, selectable = false, availablePeriodStart, availablePeriodEnd }: { report: CompletedWork | null; message?: string; selectable?: boolean; availablePeriodStart?: string; availablePeriodEnd?: string }) {
  if (!report) return message ? (
    <section className="completed-work-section" aria-labelledby="completed-work-title">
      <div className="section-heading"><div><p className="section-kicker">Запазен snapshot</p><h2 id="completed-work-title">Отчет за извършеното натоварване</h2></div></div>
      <p className="history-unavailable">{message}</p>
    </section>
  ) : null;

  return (
    <section className="completed-work-section" aria-labelledby="completed-work-title">
      <div className="section-heading">
        <div><p className="section-kicker">{date(report.period_start)} — {date(report.period_end)}</p><h2 id="completed-work-title">Отчет за извършеното натоварване</h2></div>
        <p>{report.quality.modeled_activities} моделирани активности · {report.quality.limited_activities} с ограничено HR покритие</p>
      </div>

      {selectable && <form className="report-period" method="get">
        <input type="hidden" name="wake" value="ready" />
        <label>От <input type="date" name="report_start" defaultValue={report.period_start} min={availablePeriodStart ?? report.period_start} max={availablePeriodEnd ?? report.period_end} required /></label>
        <label>До <input type="date" name="report_end" defaultValue={report.period_end} min={availablePeriodStart ?? report.period_start} max={availablePeriodEnd ?? report.period_end} required /></label>
        <button className="action-button secondary" type="submit">Покажи периода</button>
      </form>}

      <div className="report-totals">
        <dl><div><dt>Продължителност на активностите</dt><dd>{decimal(report.totals.activity_duration_min)} мин</dd></div><div><dt>HR-зонирано реално време</dt><dd>{decimal(report.totals.zoned_hr_time_min)} мин</dd></div></dl>
        {report.quality.missing_duration_activities > 0 && <p className="quality-limited">{report.quality.missing_duration_activities} активности са без надеждна обща продължителност и не са заместени с предполагаема стойност.</p>}
      </div>

      <div className="report-table-wrap"><table>
        <caption>Натоварване по пулсови зони</caption>
        <thead><tr><th>Зона</th><th>Реално време</th><th>Еквивалентно време</th><th>Ефективен товар E</th></tr></thead>
        <tbody>{report.zones.map((zone) => <tr key={zone.zone}><th>{zone.zone}</th><td>{decimal(zone.raw_time_min)} мин</td><td>{decimal(zone.equivalent_time_min)} мин</td><td>{decimal(zone.effective_load)}</td></tr>)}</tbody>
      </table></div>

      <div className="report-table-wrap"><table>
        <caption>По вид активност от Intervals</caption>
        <thead><tr><th>Етикет от източника</th><th>Активности</th><th>Продължителност</th><th>HR-зонирано време</th></tr></thead>
        <tbody>{report.sports.length > 0 ? report.sports.map((sport) => <tr key={sport.sport}><th>{sport.sport}</th><td>{sport.activities_count}</td><td>{decimal(sport.activity_duration_min)} мин</td><td>{decimal(sport.zoned_hr_time_min)} мин</td></tr>) : <tr><td colSpan={4}>Няма моделирани активности в избрания период.</td></tr>}</tbody>
      </table></div>
      <p className="report-note">Видовете активности са показани с точните етикети от Intervals. Те не са автоматично интерпретирани като научна класификация на тренировъчните средства.</p>
    </section>
  );
}
