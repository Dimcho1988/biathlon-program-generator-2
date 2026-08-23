import Link from "next/link";
import type { ActivityDetail, ActivitySeries, ActivitySeriesPoint } from "../lib/activities";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
const longDate = new Intl.DateTimeFormat("bg-BG", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
const metric = (value: number | null, suffix: string) => value === null ? "—" : `${number.format(value)} ${suffix}`;
const duration = (minutes: number | null) => minutes === null ? "—" : minutes >= 60 ? `${Math.floor(minutes / 60)} ч ${Math.round(minutes % 60)} мин` : `${number.format(minutes)} мин`;
const pace = (speed: number | null) => {
  if (!speed || speed <= 0) return "—";
  const seconds = Math.round(1000 / speed); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")} /km`;
};

type Channel = { key: keyof ActivitySeriesPoint; label: string; color: string; unit: string };

function TimelineChart({ title, series, channels }: { title: string; series: ActivitySeriesPoint[]; channels: Channel[] }) {
  const visible = channels.map((channel) => ({ channel, points: series.map((row, index) => [index, row[channel.key]] as const).filter((point): point is readonly [number, number] => typeof point[1] === "number" && Number.isFinite(point[1])) })).filter((item) => item.points.length > 1);
  if (!visible.length) return <section className="canonical-chart"><h3>{title}</h3><p>Няма наличен канал.</p></section>;
  const width = 1000, height = 250, left = 42, top = 20, plotWidth = 930, plotHeight = 190;
  const allValues = visible.flatMap((item) => item.points.map((point) => point[1]));
  const min = Math.min(...allValues), max = Math.max(...allValues), span = Math.max(1, max - min);
  const x = (index: number) => left + (index / Math.max(1, series.length - 1)) * plotWidth;
  const y = (value: number) => top + plotHeight - ((value - min) / span) * plotHeight;
  return <section className="canonical-chart"><h3>{title}</h3><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
    {[0, .25, .5, .75, 1].map((ratio) => <line key={ratio} x1={left} x2={left + plotWidth} y1={top + ratio * plotHeight} y2={top + ratio * plotHeight} />)}
    {visible.map(({ channel, points }) => <polyline key={String(channel.key)} fill="none" stroke={channel.color} strokeWidth="2.5" points={points.map(([index, value]) => `${x(index)},${y(value)}`).join(" ")} />)}
    <text x={left} y={height - 8}>{number.format(min)}</text><text x={left + plotWidth} y={height - 8} textAnchor="end">{number.format(max)}</text>
  </svg><p className="chart-legend">{visible.map(({ channel }) => <span key={String(channel.key)} style={{ color: channel.color }}>● {channel.label} ({channel.unit})</span>)}</p></section>;
}

export function ActivityDetailView({ activity, series }: { activity: ActivityDetail; series: ActivitySeries | null }) {
  const isRun = activity.sport.toLowerCase().includes("run");
  const localDate = longDate.format(new Date(`${activity.local_date}T00:00:00Z`));
  return <>
    <header className="activity-detail-hero">
      <div><p className="eyebrow">Canonical activity</p><h1>{activity.name || `${activity.sport} · ${activity.local_time}`}</h1><p>{localDate} · {activity.local_time} · {activity.sport}</p></div>
      <span className={`quality-badge ${activity.quality_status}`}>{activity.quality_status === "valid" ? "Валидна" : activity.quality_status === "limited" ? "Ограничена" : "Изключена"}</span>
    </header>
    <nav className="activity-tabs" aria-label="Изглед на активността"><span aria-current="page">Canonical</span>{activity.shadow_available ? <Link href={`/activities/${activity.activity_ref}/shadow`}>Experimental · Raw ↔ Shadow</Link> : <span className="disabled">Няма shadow run</span>}</nav>
    <section className="activity-detail-layout">
      <div className="activity-detail-main">
        <dl className="activity-summary-grid">
          <div><dt>Продължителност</dt><dd>{duration(activity.duration_min)}</dd></div>
          <div><dt>Дистанция</dt><dd>{activity.distance_m === null ? "—" : `${number.format(activity.distance_m / 1000)} km`}</dd></div>
          <div><dt>Денивелация</dt><dd>{metric(activity.elevation_gain_m, "m")}</dd></div>
          <div><dt>Среден / max HR</dt><dd>{activity.average_hr_bpm === null ? "—" : `${number.format(activity.average_hr_bpm)} / ${number.format(activity.max_hr_bpm ?? activity.average_hr_bpm)} bpm`}</dd></div>
          <div><dt>{isRun ? "Темпо" : "Средна скорост"}</dt><dd>{isRun ? pace(activity.average_speed_mps) : activity.average_speed_mps === null ? "—" : `${number.format(activity.average_speed_mps * 3.6)} km/h`}</dd></div>
          <div className="canonical-load"><dt>Canonical load</dt><dd>{metric(activity.canonical_training_load, "")}</dd></div>
        </dl>
        {series ? <div className="canonical-charts"><TimelineChart title="Пулс" series={series.series} channels={[{ key: "hr_bpm", label: "HR", color: "#df4e5b", unit: "bpm" }]} /><TimelineChart title={isRun ? "Скорост / темпо" : "Скорост"} series={series.series} channels={[{ key: "speed_kmh", label: "Скорост", color: "#07888d", unit: "km/h" }]} /><TimelineChart title="Височина" series={series.series} channels={[{ key: "altitude_m", label: "Височина", color: "#6558d5", unit: "m" }]} /><TimelineChart title="Наклон" series={series.series} channels={[{ key: "grade_pct", label: "Наклон", color: "#a76400", unit: "%" }]} /></div> : <section className="canonical-chart"><h3>Времеви серии</h3><p>За тази активност няма съхранени графични канали.</p></section>}
        <section className="activity-zones"><div className="section-heading"><div><p className="section-kicker">Реални данни</p><h2>HR време по зони</h2></div></div><div className="detail-zone-list">{activity.zones.map((zone) => <article key={zone.zone} className={zone.zone.toLowerCase()}><strong>{zone.zone}</strong><span>{number.format(zone.raw_time_s / 60)} мин</span><i style={{ width: `${Math.min(100, zone.raw_time_s / Math.max(1, ...activity.zones.map((item) => item.raw_time_s)) * 100)}%` }} /></article>)}</div></section>
        <section className="activity-intervals"><div className="section-heading"><div><p className="section-kicker">Структура</p><h2>Интервали</h2></div></div>{activity.intervals.length ? <div className="shadow-table-wrap"><table><thead><tr><th>Интервал</th><th>Време</th><th>Дистанция</th><th>Среден HR</th></tr></thead><tbody>{activity.intervals.map((interval, index) => <tr key={index}><td>{String(interval.name || `Интервал ${index + 1}`)}</td><td>{interval.elapsed_time_s ? duration(Number(interval.elapsed_time_s) / 60) : "—"}</td><td>{interval.distance_m ? `${number.format(Number(interval.distance_m) / 1000)} km` : "—"}</td><td>{interval.average_hr_bpm ? `${number.format(Number(interval.average_hr_bpm))} bpm` : "—"}</td></tr>)}</tbody></table></div> : <p className="detail-empty">Intervals не е върнал структурирани интервали за тази активност.</p>}</section>
      </div>
      <aside className="activity-private-panel"><p className="section-kicker">Лично съдържание</p><h2>Бележка</h2><p>{activity.description || "Няма добавена бележка."}</p><hr /><h3>Качество</h3><p>{activity.quality_reason || `HR покритие ${number.format(activity.hr_coverage_percent ?? 0)}%.`}</p><dl><div><dt>Moving time</dt><dd>{duration(activity.moving_time_min)}</dd></div><div><dt>Elapsed time</dt><dd>{duration(activity.elapsed_time_min)}</dd></div><div><dt>Recording time</dt><dd>{duration(activity.recording_time_min)}</dd></div></dl></aside>
    </section>
    <nav className="activity-neighbors" aria-label="Предишна и следваща активност">{activity.previous_activity_ref ? <Link href={`/activities/${activity.previous_activity_ref}`}>← Предишна активност</Link> : <span>← Предишна активност</span>}<Link href="/activities">Към календара</Link>{activity.next_activity_ref ? <Link href={`/activities/${activity.next_activity_ref}`}>Следваща активност →</Link> : <span>Следваща активност →</span>}</nav>
  </>;
}
