"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { displayDate } from "../lib/dashboard-periods";
import { heartRateChartRows, loadComparison, speedChartRows, speedEquivalentWindow, type LoadSource } from "../lib/load-dynamics";
import type { LoadHistory } from "../lib/load-history";
import type { SpeedLoad } from "../lib/speed-load";
import { readSpeedLoad } from "../lib/speed-load-client";
import { ZONES } from "../lib/training-status";
import { LoadHistorySection } from "./load-history-section";
import { LoadSourceChart, type LoadSeries } from "./load-source-chart";

type Props = { athleteId: string; history: LoadHistory | null; message?: string; generation: string | null; revision: number | null; cacheScope?: string };
const choices = [["hr", "По пулс"], ["speed", "По скорост"], ["compare", "Сравнение"]] as const;
const decimal = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("bg-BG", { maximumFractionDigits: 1 });
const minutes = (value: number | null | undefined) => value == null ? "—" : `${decimal(value)} мин`;

export function LoadDynamics(props: Props) {
  // A profile switch or refreshed generation must never reuse another analysis.
  return <LoadDynamicsView key={`${props.cacheScope}:${props.athleteId}:${props.generation}:${props.revision}`} {...props}/>;
}

function LoadDynamicsView({ history, message, generation, revision, cacheScope }: Props) {
  const [source, setSource] = useState<LoadSource>("hr");
  const [speed, setSpeed] = useState<SpeedLoad | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function readSpeed(force = false) {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError(""); setSpeed(null);
    try {
      const result = await readSpeedLoad({ cacheScope, generation, revision, signal: controller.signal, force,
        errorMessage: "Скоростният отчет временно не е достъпен. Опитай отново.",
        generationError: "Данните са обновени. Презареди страницата, за да сравниш една и съща версия." });
      if (!controller.signal.aborted) setSpeed(result);
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Неуспешно зареждане на скоростния отчет.");
    } finally {
      if (!controller.signal.aborted) { request.current = null; setBusy(false); }
    }
  }

  function selectSource(next: LoadSource) {
    setSource(next);
    if (next !== "hr" && !speed) void readSpeed();
  }

  return <div className="load-dynamics">
    <div className="load-source-toolbar">
      <div><p className="section-kicker">Основа на натоварването</p>
        <div className="load-source-switch" role="group" aria-label="Основа на натоварването">
          {choices.map(([key, label]) => <button key={key} type="button" aria-pressed={source === key} onClick={() => selectSource(key)}>{label}</button>)}
        </div>
      </div>
      {source !== "hr" && speed && <button className="action-button secondary" type="button" onClick={() => void readSpeed(true)}>Обнови скоростния отчет</button>}
    </div>
    <p className="load-source-note">Изборът променя показания отчет. Recovery и планирането използват пулсовия товар.</p>
    {source === "hr" ? <LoadHistorySection history={history} message={message}/> : <div aria-busy={busy}>
      <div role="status" aria-live="polite">
        {busy && <p className="load-source-loading">Изчисляване на Q, E и 7/40 по скорост…</p>}
        {error && <div className="history-unavailable"><p>{error}</p><button type="button" className="action-button secondary" onClick={() => void readSpeed()}>Опитай отново</button></div>}
      </div>
      {speed && <SpeedDynamics speed={speed} history={history} comparison={source === "compare"}/>}
    </div>}
  </div>;
}

function SpeedDynamics({ speed, history, comparison }: { speed: SpeedLoad; history: LoadHistory | null; comparison: boolean }) {
  const aligned = comparison ? loadComparison(history, speed) : null;
  const short = speedEquivalentWindow(speed, 7, aligned?.start);
  const long = speedEquivalentWindow(speed, 40, aligned?.start);
  const start = aligned?.start ?? long.start;
  const series: LoadSeries[] = [{ source: "speed", rows: speedChartRows(speed) }];
  if (comparison && history) series.unshift({ source: "hr", rows: heartRateChartRows(history) });
  return <section className="history-section" aria-labelledby="speed-dynamics-title">
    <div className="section-heading">
      <div><p className="section-kicker">{displayDate(start)} — {displayDate(speed.end_date)}</p><h2 id="speed-dynamics-title">Натоварване и динамика · {comparison ? "сравнение" : "по скорост"}</h2></div>
      <p>Бягане · ролки/ски · колело</p>
    </div>
    <div className="load-speed-coverage">
      <strong>Скоростно покритие: {decimal(speed.coverage_percent)}%</strong>
      <span>{decimal(speed.classified_minutes)} от {decimal(speed.recorded_minutes)} записани минути за поддържаните спортове · {displayDate(speed.start_date)} – {displayDate(speed.end_date)}</span>
    </div>
    {speed.warnings.includes("TREADMILL_GRADE_ASSUMED_FLAT") && <p>Пътека: при липсващ запис за наклона използваме скоростта с допускане за 0% наклон.</p>}
    {speed.warnings.includes("TREADMILL_PRIOR_RUN_INDEX_FALLBACK") && <p>За пътека без предходен собствен индекс е използван предходният индекс от бягане на същия спортист.</p>}
    {speed.status === "UNAVAILABLE" ? <p className="history-unavailable">Още няма достатъчно данни за скоростен товар. Нужни са валидни индекси от предходни тренировки за същия спорт и преизчислени скорости. Липсващите стойности не означават нулево натоварване.</p> : aligned?.error ? <div className="history-unavailable"><p>{aligned.error}</p><p>По пулс: {history ? displayDate(history.period_end) : "няма данни"} · По скорост: {displayDate(speed.end_date)}</p></div> : <>
      <div className="history-explainer">
        <strong>{comparison ? "Как да сравняваш" : "Как се изчислява"}</strong>
        <p>{comparison ? "Един цвят за всяка зона: плътна линия по пулс, прекъсната по скорост. Графиките използват общ период и скала. Двата товара са отделни оценки и не се събират." : "Vflat и предходните индекси за същия спорт определят зоната и Q. E включва влиянието между зоните. 7/40 сравнява средния дневен E за 7 и 40 дни със стабилизираща база."}</p>
      </div>
      <div className="volume-period-note">
        <p><strong>Приравнен обем Q:</strong> сбор за {displayDate(short.start)} – {displayDate(short.end)}; седмичен еквивалент за {displayDate(long.start)} – {displayDate(long.end)} = сбор ÷ {long.days} × 7. Крайната дата е включена.</p>
        <p>Q, E и 7/40 по скорост описват само минутите с покритие. Силовите тренировки се отчитат отделно по продължителност в пулсовия изглед.</p>
        {comparison && <p>Q се сравнява за еднакви дати. E7, E40 и 7/40 са оценките на всеки модел; покритието и наличната история могат да се различават.</p>}
        {(short.partial || long.partial) && <p>Непълна скоростна история: {short.days}/7 и {long.days}/40 календарни дни. Седмичният еквивалент е предварителен.</p>}
        {(!short.complete || !long.complete) && <p>Липсват дневни данни; засегнатият Q не се изчислява.</p>}
        {speed.activities.some(a => a.reason === "SPEED_RECOMPUTATION_REQUIRED") && <p>Има активности със стар модел. Използвай „Обнови данните“ от горния бутон.</p>}
      </div>
      <div className={`load-summary${comparison ? " load-comparison-cards" : ""}`} role="list" aria-label={comparison ? "Сравнение на показателите по зони" : "Показатели по скоростни зони"}>
        {speed.zones.map((zone, index) => {
          const key = ZONES[index], hr = history?.zones.find(z => z.zone === key);
          const metrics = [
            ["Q · 7 дни", aligned?.hrShort?.totals?.[key], short.totals?.[key]],
            ["Q · 40 → 7 дни", aligned?.hrLong?.weekly?.[key], long.weekly?.[key]],
            ["E7 · на ден", hr?.e7_daily, zone.e7_daily],
            ["E40 · на ден", hr?.e40_daily, zone.e40_daily],
          ] as const;
          return <article key={key} className={`load-summary-card ${key.toLowerCase()}`} style={{ "--series": `var(--zone-${index + 1})` } as CSSProperties} role="listitem" aria-label={key}>
            {comparison ? <>
              <h3 className="summary-zone">{key}</h3>
              <table className="load-comparison-table"><caption className="sr-only">{key} · Q в приравнени минути, E в ефективни минути</caption>
                <thead><tr><th scope="col"><span className="sr-only">Показател</span></th><th scope="col">Пулс</th><th scope="col">Скорост</th></tr></thead>
                <tbody><tr className="load-ratio-row"><th scope="row">7/40</th><td>{decimal(hr?.status_7_40)}</td><td>{decimal(zone.ratio_7_40)}</td></tr>
                  {metrics.map(([label, pulse, value]) => <tr key={label}><th scope="row">{label}</th><td>{decimal(pulse)}</td><td>{decimal(value)}</td></tr>)}
                </tbody>
              </table>
            </> : <>
              <div><span className="summary-zone">{key}</span><strong>{decimal(zone.ratio_7_40)}</strong><small>7/40</small></div>
              <dl>{metrics.map(([label, , value]) => <div key={label}><dt>{label}</dt><dd>{minutes(value)}</dd></div>)}</dl>
            </>}
          </article>;
        })}
      </div>
      {comparison && <p className="load-source-note">Q е в приравнени минути; E7 и E40 са средни ефективни минути на ден.</p>}
      <LoadSourceChart series={series} metric="ratio" start={start} end={speed.end_date}/>
      <LoadSourceChart series={series} metric="effective" start={start} end={speed.end_date}/>
      <p className="load-source-note"><a href="/speed">Индекси по спортове и връзка пулс–скорост →</a></p>
      {!comparison && <>
        <div className="activities-heading"><div><p className="section-kicker">Последни сесии</p><h3>Скоростно покритие по активности</h3></div></div>
        <div className="activity-list">{[...speed.activities].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 12).map(activity => <a key={activity.activity_ref} className="activity-row speed-load-activity" href={`/activities/${encodeURIComponent(activity.activity_ref)}`}>
          <span><strong>{activity.sport}</strong><small>{displayDate(activity.date)}</small></span>
          <span>{decimal(activity.classified_minutes)} от {decimal(activity.recorded_minutes)} мин</span>
          <span className={activity.classified_minutes >= activity.recorded_minutes ? "quality-valid" : "quality-limited"}>{activity.classified_minutes > 0 ? `${decimal(activity.recorded_minutes ? 100 * activity.classified_minutes / activity.recorded_minutes : 0)}% по скорост` : "Няма скоростна оценка"}</span>
        </a>)}</div>
      </>}
    </>}
  </section>;
}
