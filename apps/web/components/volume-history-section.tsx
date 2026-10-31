"use client";
import { MetricChart } from "./metric-chart";
import type { VolumeHistory, WeeklyVolume } from "../lib/volume-history";

const date = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "short", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));
function VolumeChart({ rows }: { rows: WeeklyVolume[]; periodStart: string; periodEnd: string }) {
  return <figure className="history-chart volume-chart"><div className="history-chart-heading"><h3>Реален седмичен обем</h3><p>Колоните сравняват календарни седмици. Това не е сбор на ефективен товар E.</p></div>
    <MetricChart title="Обща динамика на реалния тренировъчен обем" unit="Време · ч:мм:сс" xLabel="Седмица от" yKind="duration" zero series={[
      { key: "duration", label: "Продължителност на активностите", color: "var(--accent)", kind: "bar", points: rows.map(row => ({ x: Date.parse(row.week_start), y: row.activity_duration_min * 60 })) },
      { key: "zoned", label: "HR-зонирано време Z1–Z5", color: "var(--zone-2)", kind: "bar", points: rows.map(row => ({ x: Date.parse(row.week_start), y: row.zoned_hr_time_min * 60 })) },
    ]} />
  </figure>;
}

export function VolumeHistorySection({ history, message }: { history: VolumeHistory | null; message?: string }) {
  if (!history) return message ? (
    <section className="history-section" aria-labelledby="volume-title">
      <div className="section-heading"><div><p className="section-kicker">Реален обем</p><h2 id="volume-title">Обща динамика на реалния обем</h2></div></div>
      <p className="history-unavailable">{message}</p>
    </section>
  ) : null;

  return (
    <section className="history-section" aria-labelledby="volume-title">
      <div className="section-heading">
        <div><p className="section-kicker">{date(history.period_start)} — {date(history.period_end)}</p><h2 id="volume-title">Обща динамика на реалния обем</h2></div>
        <p>{history.quality.modeled_activities} моделирани активности · {history.weekly.length} календарни седмици</p>
      </div>
      <div className="history-explainer">
        <strong>Две различни мерки</strong>
        <p>Продължителността използва наличната стойност от активността. HR-зонираното време е точната сума на реалните минути T<sub>z</sub> в Z1–Z5. Двете мерки се разглеждат отделно; STR компонентът от пълния план все още не е интегриран.</p>
      </div>
      {history.quality.missing_duration_activities > 0 && <p className="volume-quality-note">За {history.quality.missing_duration_activities} активности липсва обща продължителност; те остават в HR-зонирания обем и са изключени само от колоната за продължителност.</p>}
      <VolumeChart rows={history.weekly} periodStart={history.period_start} periodEnd={history.period_end} />
      <p className="report-note">Договор: {history.schema_version} · агрегация: {history.model.aggregation_version} · източник: {history.model.source_schema_version}</p>
    </section>
  );
}
