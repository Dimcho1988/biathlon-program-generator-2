"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import type { ActivityDetail, ActivitySeries } from "../lib/activities";
import { durationSeconds } from "../lib/duration-format";
import { MetricChart } from "./metric-chart";
import { PreferredSpeed, useRunningSpeedUnit } from "./running-speed-unit";
import { isRunningSport } from "../lib/speed-format";

type Lap = Record<string, unknown>;
const n = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
const format = (value: unknown, digits = 0) => n(value) === null ? "—" : Number(value).toLocaleString("bg-BG", {maximumFractionDigits: digits});
const typeLabel: Record<string, string> = {REST: "Почивка", RECOVERY: "Възстановяване", WARMUP: "Загряване", COOLDOWN: "Разпускане", WORK: "Усилие", INTERVAL: "Усилие", ACTIVE: "Работа"};

export function lapDuration(value: unknown) {
  const seconds = n(value);
  if (seconds === null) return "—";
  const tenths = Math.round(seconds*10);
  return tenths % 10 ? `${durationSeconds(Math.floor(tenths/10))},${tenths%10}` : durationSeconds(tenths/10);
}

export function ActivityLaps({activity, series, seriesUnavailable = false}: {activity: ActivityDetail; series: ActivitySeries | null; seriesUnavailable?: boolean}) {
  const hasDevice = activity.intervals.some(row => row.source === "device");
  const hasIntervals = activity.intervals.some(row => row.source !== "device");
  const [source, setSource] = useState(hasDevice ? "device" : "intervals");
  const [selected, setSelected] = useState<number | null>(null);
  const rows = useMemo(() => activity.intervals.filter(row => (row.source === "device" ? "device" : "intervals") === source), [activity.intervals, source]);
  const current = selected === null ? null : rows[selected];
  const label = (row: Lap, i: number) => String(row.name || `${source === "device" ? "Обиколка" : "Интервал"} ${i+1}`);
  const start = n(current?.start_time_s), end = n(current?.end_time_s);
  const zoom = start !== null && end !== null && end > start;
  const unit = useRunningSpeedUnit();
  const pace = isRunningSport(activity.sport) && unit === "pace";
  const points = useMemo(() => (series?.series ?? []).filter(row => row.elapsed_s !== null && (!zoom || row.elapsed_s! >= start! && row.elapsed_s! <= end!)), [series, zoom, start, end]);
  const fullEnd = Math.max((activity.elapsed_time_min ?? activity.duration_min ?? 0)*60, series?.series.at(-1)?.elapsed_s ?? 0, 1);
  const xDomain: [number, number] = zoom ? [start!, end!] : [0, fullEnd];
  const bands = rows.flatMap((row, i) => n(row.start_time_s) !== null && n(row.end_time_s) !== null ? [{start: Number(row.start_time_s), end: Number(row.end_time_s), label: label(row, i)}] : []);
  const gaps = (series?.series ?? []).slice(1).flatMap((row, i) => row.elapsed_s !== null && series?.series[i].elapsed_s !== null ? [row.elapsed_s!-series!.series[i].elapsed_s!] : []).filter(value => value > 0).sort((a,b) => a-b);
  const maxGap = (gaps[Math.floor(gaps.length/2)] ?? 1)*3;
  const chartSeries = (keys: Array<{key: "hr_bpm" | "hrmod_bpm" | "speed_kmh" | "vflat_kmh"; label: string; color: string}>, asPace = false) => keys.map((channel, i) => ({...channel, dashed: i === 0, points: points.map(row => {
    const value = n(row[channel.key]);
    return {x: row.elapsed_s!, y: asPace ? value !== null && value > 0 ? 3600/value : null : value};
  })}));
  const changeSource = (value: string) => {setSource(value); setSelected(null);};
  return <div className="activity-lap-analysis">
    <section className="activity-intervals activity-laps" aria-label="Обиколки и интервали">
      <div className="lap-heading"><div><p className="section-kicker">Структура на тренировката</p><h2>Обиколки и интервали</h2></div><span>{rows.length ? `${rows.length} ${source === "device" ? "обиколки" : "интервала"}` : ""}</span></div>
      {hasDevice && hasIntervals && <div className="lap-source-controls" role="group" aria-label="Източник на разделянето"><button type="button" aria-pressed={source === "device"} onClick={() => changeSource("device")}>От часовника</button><button type="button" aria-pressed={source === "intervals"} onClick={() => changeSource("intervals")}>От Intervals.icu</button></div>}
      <p className="lap-intro">{source === "device" ? "Записаните от часовника обиколки, включително почивките." : "Интервали от Intervals.icu. Границите им може да се различават от обиколките на часовника."} {rows.length > 0 && "Избери ред, за да разгледаш участъка в графиките."}</p>
      {!hasDevice && <p className="lap-availability">{activity.device_laps_status === "refresh_required" || !activity.device_laps_status ? <>Обиколките още не са изтеглени. Използвай „Обнови данните“ в <Link href="/">общия статус</Link>.</> : activity.device_laps_status === "retry" ? "Оригиналните обиколки временно не са достъпни. Ще бъдат проверени при следващото обновяване." : "Няма достъпни оригинални обиколки за тази активност."}</p>}
      {rows.length > 0 ? <div className="lap-table-wrap"><table className="lap-table"><caption className="sr-only">Средни стойности за всяка обиколка. HRmod е модулиран пулс, Vflat е приравнена скорост.</caption><thead><tr><th scope="col">№</th><th scope="col">Време</th><th scope="col">км</th><th scope="col">Пулс<small>уд./мин</small></th><th scope="col" className="lap-desktop">HRmod<small>уд./мин</small></th><th scope="col">{pace ? "Темпо" : "Скорост"}<small>{pace ? "мин/км" : "км/ч"}</small></th><th scope="col" className="lap-desktop">Vflat<small>{pace ? "мин/км" : "км/ч"}</small></th></tr></thead><tbody>{rows.map((row, i) => <tr key={i} className={selected === i ? "lap-selected" : ""} onClick={() => setSelected(selected === i ? null : i)}>
        <th scope="row"><button type="button" aria-label={label(row, i)} aria-pressed={selected === i} onClick={event => {event.stopPropagation();setSelected(selected === i ? null : i);}}>{i+1}</button><span className="lap-row-name">{row.name ? String(row.name) : typeLabel[String(row.intensity)] ?? ""}</span></th>
        <td>{lapDuration(row.timer_time_s ?? row.elapsed_time_s)}</td><td>{n(row.distance_m) === null ? "—" : format(Number(row.distance_m)/1000, 2)}</td>
        <td><span>{format(row.average_hr_bpm)}</span><span className="lap-mobile lap-model">{format(row.average_hrmod_bpm)}<small>HRmod</small></span></td><td className="lap-desktop lap-model">{format(row.average_hrmod_bpm)}</td>
        <td><PreferredSpeed speed={n(row.average_speed_kmh)} sport={activity.sport} includeUnit={false}/><span className="lap-mobile lap-model"><PreferredSpeed speed={n(row.average_vflat_kmh)} sport={activity.sport} includeUnit={false}/><small>Vflat</small></span></td><td className="lap-desktop lap-model"><PreferredSpeed speed={n(row.average_vflat_kmh)} sport={activity.sport} includeUnit={false}/></td>
      </tr>)}</tbody></table></div> : <p className="detail-empty">Няма налични обиколки или интервали.</p>}
      <p className="lap-explanation">Пулсът и скоростта са средни стойности. <strong>HRmod</strong> е модулираният пулс, а <strong>Vflat</strong> — приравнената към равен терен скорост. „—“ означава липсващи данни.</p>
      {current && <div className="lap-selection" aria-live="polite"><div className="lap-heading"><h3>{label(current, selected!)}</h3><button type="button" onClick={() => setSelected(null)}>Цялата тренировка</button></div>{!zoom && <p>Липсват точните времеви граници на този интервал.</p>}
        <dl><div><dt>Изминало време</dt><dd>{lapDuration(current.elapsed_time_s)}</dd></div><div><dt>Време по таймера</dt><dd>{lapDuration(current.timer_time_s)}</dd></div><div><dt>Максимален пулс</dt><dd>{format(current.max_hr_bpm)} уд./мин</dd></div></dl>
        <details><summary>Данни за сравнението</summary><p>Изчислените стойности използват наличните валидни измервания в този участък. При непълни данни те могат да обхващат само част от обиколката.</p><dl>
          <div><dt>Пулс: реален / HRmod върху еднакво време</dt><dd>{format(current.comparison_hr_bpm)} / {format(current.average_hrmod_bpm)} уд./мин · {durationSeconds(n(current.hr_comparison_seconds))}</dd></div>
          <div><dt>Скорост: реална / Vflat върху еднакво време</dt><dd><PreferredSpeed speed={n(current.comparison_speed_kmh)} sport={activity.sport}/> / <PreferredSpeed speed={n(current.average_vflat_kmh)} sport={activity.sport}/> · {durationSeconds(n(current.speed_comparison_seconds))}</dd></div>
        </dl></details>
      </div>}
    </section>
    {series ? <div className="canonical-charts">
      <section className="canonical-chart"><h3>Пулс{current && zoom ? ` · ${label(current, selected!)}` : ""}</h3><MetricChart title="Пулс" xKind="duration" unit="уд./мин" xDomain={xDomain} bands={bands} maxGap={maxGap} series={chartSeries([{key:"hr_bpm", label:"Реален пулс", color:"#64748b"}, {key:"hrmod_bpm", label:"Модулиран пулс · HRmod", color:"#df4e5b"}])}/></section>
      <section className="canonical-chart"><h3>{pace ? "Темпо" : "Скорост"}{current && zoom ? ` · ${label(current, selected!)}` : ""}</h3><MetricChart title={isRunningSport(activity.sport) ? "Скорост / темпо" : "Скорост"} xKind="duration" unit={pace ? "мин/км" : "км/ч"} yKind={pace ? "pace" : "number"} xDomain={xDomain} bands={bands} maxGap={maxGap} zero={!pace} series={chartSeries([{key:"speed_kmh", label:pace ? "Реално темпо" : "Реална скорост", color:"#64748b"}, {key:"vflat_kmh", label:pace ? "Приравнено темпо · Vflat" : "Приравнена скорост · Vflat", color:"#07888d"}], pace)}/></section>
    </div> : <p className="detail-empty">{seriesUnavailable ? "Графиките временно не са заредени." : "За тази активност няма съхранени графични канали."}</p>}
  </div>;
}
