"use client";
import { durationHms } from "../lib/duration-format";
import type { CSSProperties } from "react";
import type { DailyZoneLoad, LoadHistory } from "../lib/load-history";
import { ZONES, type Zone } from "../lib/training-status";

import { equivalentWindow, displayDate } from "../lib/dashboard-periods";
import { VolumePeriodNote } from "./volume-period-note";
import { MetricChart } from "./metric-chart";
import { TrefDetails } from "./tref-details";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
const decimal = (value: number) => number.format(value);
const date = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "short", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));

const zoneStyle = (zone: Zone): CSSProperties => ({ "--series": `var(--zone-${ZONES.indexOf(zone) + 1})` } as CSSProperties);
const strengthStyle = { "--series": "var(--strength)" } as CSSProperties;

function ZoneHistoryChart({ rows, ratio }: { rows: DailyZoneLoad[]; ratio: boolean }) {
  const title = ratio ? "Динамика на индекса 7/40 по зони" : "Дневен ефективен товар E по зони";
  const values = rows.map(row => ratio ? row.status_7_40 : row.effective_load);
  const low = ratio ? Math.min(.6, ...values) : 0, high = ratio ? Math.max(1.4, ...values) : Math.max(1, ...values) * 1.05;
  return <figure className="history-chart"><div className="history-chart-heading"><h3>{title}</h3><p>Дневни стойности без изглаждане или сумиране; линиите не се сумират до нов общ резултат. Липсващите дни не се свързват.</p></div>
    <MetricChart title={title} unit={ratio ? "Индекс 7/40" : "Ефективен товар E"} domain={[low, high]} maxGap={86400000} reference={ratio ? { value: 1, label: "7/40 = 1 · стабилен товар" } : undefined} series={ZONES.map((zone, i) => ({ key: zone, label: zone, color: `var(--zone-${i+1})`, points: rows.filter(row => row.zone === zone).sort((a,b) => a.date.localeCompare(b.date)).map(row => ({ x: Date.parse(row.date), y: ratio ? row.status_7_40 : row.effective_load })) }))} />
  </figure>;
}

export function LoadHistorySection({ history, message }: { history: LoadHistory | null; message?: string }) {
  if (!history) return message ? (
    <section className="history-section" aria-labelledby="history-title">
      <div className="section-heading"><div><p className="section-kicker">90-дневен прозорец</p><h2 id="history-title">Натоварване и динамика</h2></div></div>
      <p className="history-unavailable">{message} Обновете реалните данни след публикуването на новата API версия.</p>
    </section>
  ) : null;

  const short = equivalentWindow(history, 7), long = equivalentWindow(history, 40);
  const volume = (value: number | undefined) => value === undefined ? "Няма данни" : durationHms(value);
  return (
    <section className="history-section" aria-labelledby="history-title">
      <div className="section-heading">
        <div><p className="section-kicker">{date(history.period_start)} — {date(history.period_end)}</p><h2 id="history-title">Натоварване и динамика</h2></div>
        <p>{history.quality.processed_activities} обработени активности · {history.quality.no_activity_days} дни без активност</p>
      </div>

      <div className="history-explainer">
        <strong>Как се чете 7/40</strong>
        <p>Индексът сравнява средния дневен ефективен товар E за последните 7 и 40 календарни дни със стабилизираща база. Над 1 означава покачване, под 1 — спад. Историята преди тези прозорци подпомага изчисленията, но не удължава сравняваните периоди.</p>
      </div>

      <VolumePeriodNote history={history} />
      <div className="load-summary" role="list" aria-label="Текущи показатели по зони">
        {history.zones.map((zone) => <article key={zone.zone} className={`load-summary-card ${zone.zone.toLowerCase()}`} style={zoneStyle(zone.zone)} role="listitem">
          <div><span className="summary-zone">{zone.zone}</span><strong>{decimal(zone.status_7_40)}</strong><small>7/40</small></div>
          <dl>
            <div><dt>Приравнено · 7 дни</dt><dd>{volume(short.totals?.[zone.zone])}</dd></div>
            <div><dt>Приравнено · седмица от 40 дни</dt><dd>{volume(long.weekly?.[zone.zone])}</dd></div>
            <div><dt>E7 · средно/ден</dt><dd>{decimal(zone.e7_daily)} мин E</dd></div>
            <div><dt>E40 · средно/ден</dt><dd>{decimal(zone.e40_daily)} мин E</dd></div>
          </dl>
        </article>)}
      </div>

      {history.strength && <div className="strength-load-panel" style={strengthStyle}>
        <div className="history-chart-heading">
          <div><p className="section-kicker">Intervals · реална продължителност</p><h3>Силова тренировка</h3></div>
          <p>Всички изрично разпознати силови активности влизат в един общ компонент STR. Пулсовите им минути не се добавят към Z1–Z5.</p>
        </div>
        <div className="load-summary strength-summary" role="list" aria-label="Текущо силово натоварване">
          <article className="load-summary-card" style={strengthStyle} role="listitem">
            <div><span className="summary-zone">STR</span><strong>{decimal(history.strength.summary.status_7_40)}</strong><small>7/40</small></div>
            <dl>
              <div><dt>Последни 7 дни</dt><dd>{durationHms(history.strength.summary.real_time_7d_min)}</dd></div>
              <div><dt>Последни 40 дни</dt><dd>{durationHms(history.strength.summary.real_time_40d_min)}</dd></div>
              <div><dt>Тренировки · целият период</dt><dd>{history.strength.summary.recorded_activities}</dd></div>
            </dl>
          </article>
          <div className="strength-method-note">
            <strong>Коефициент {decimal(history.strength.model.equivalent_time_coefficient)}</strong>
            <p>Брой тренировки за {displayDate(history.period_start)} – {displayDate(history.period_end)}. Обемите за 7 и 40 дни са сборове до {displayDate(history.period_end)} включително.</p>
            <p>Една записана минута е една STR минута. Моделът не предполага разновидност на силата и не използва името на активността за класификация.</p>
          </div>
        </div>
      </div>}

      <TrefDetails zones={history.zones} strength={history.strength?.summary.tref_min} />
      <ZoneHistoryChart rows={history.daily} ratio />

      <ZoneHistoryChart rows={history.daily} ratio={false} />

      <div className="activities-heading"><div><p className="section-kicker">Последни сесии</p><h3>Реално → приравнено → ефективно</h3></div><p>{history.quality.limited_activities} с ограничено HR покритие · {history.quality.excluded_activities} изключени</p></div>
      <div className="activity-list">
        {history.activities.slice(0, 12).map((activity) => <details key={activity.activity_ref} className="activity-row">
          <summary>
            <span><strong>{activity.sport}</strong><small>{date(activity.date)}</small></span>
            <span>{activity.duration_min === null ? "—" : durationHms(activity.duration_min)}</span>
            <span className={activity.quality_status === "limited" ? "quality-limited" : "quality-valid"}>{activity.strength_time_min > 0 ? "STR · без двойно HR" : `${decimal(activity.hr_coverage_percent)}% HR`}</span>
            <span className="chevron" aria-hidden="true">⌄</span>
          </summary>
          {activity.strength_time_min > 0 ? <div className="strength-activity-detail"><strong>STR</strong><span>{durationHms(activity.strength_time_min)} реално време</span><span>{durationHms(activity.strength_time_min)} приравнено време</span><small>Коефициент 1,0 · Z1–Z5 = 0</small></div> : <div className="activity-table-wrap"><table>
            <thead><tr><th>Зона</th><th>Реално</th><th>Приравнено</th><th>Ефективно E</th><th>Среден HR</th><th>Стойност/мин</th></tr></thead>
            <tbody>{activity.zones.map((zone) => <tr key={zone.zone}>
              <th>{zone.zone}</th><td>{durationHms(zone.raw_time_min)}</td><td>{durationHms(zone.equivalent_time_min)}</td><td>{decimal(zone.effective_load)}</td><td>{zone.mean_effective_hr_bpm === null ? "—" : `${decimal(zone.mean_effective_hr_bpm)} bpm`}</td><td>{zone.average_minute_value_percent === null ? "—" : `${decimal(zone.average_minute_value_percent)}%`}</td>
            </tr>)}</tbody>
          </table></div>}
        </details>)}
      </div>
    </section>
  );
}
