"use client";

import { useEffect, useState } from "react";
import { parseSpeedLoad, type SpeedLoad } from "../lib/speed-load";
import { durationHms } from "../lib/duration-format";
import { componentColor } from "../lib/training-visuals";

const decimal = (value: number) => value.toLocaleString("bg-BG", { maximumFractionDigits: 1 });
type Props = { start: string; end: string; generation: string | null; revision: number | null; totalDuration: number };
export function SpeedWorkReport({ start, end, generation, revision, totalDuration }: Props) {
  const [data, setData] = useState<SpeedLoad | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    async function read() {
      try {
        const query = new URLSearchParams({ period_start: start, period_end: end });
        const response = await fetch(`/api/athlete/models/speed-load?${query}`, { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error("Отчетът по скорост временно не е достъпен. Опитайте отново.");
        const result = parseSpeedLoad(await response.json());
        if (result.source_generation_id !== generation || result.source_revision !== revision)
          throw new Error("Данните са обновени. Презаредете страницата за съгласуван отчет.");
        if (result.start_date !== start || result.end_date !== end)
          throw new Error("Скоростният отчет не съответства на избрания период. Опитайте след обновяването на услугата.");
        if (!controller.signal.aborted) setData(result);
      } catch (e) {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Неуспешно зареждане.");
      }
    }
    void read();
    return () => controller.abort();
  }, [start, end, generation, revision, attempt]);
  const sports = [...new Set(data?.activities.map(a => a.sport) ?? [])].sort();
  return <div aria-label="Отчет по скорост" aria-busy={!data && !error}>
    <div role="status">{error ? <><p>{error}</p><button type="button" className="action-button secondary" onClick={() => { setError(""); setData(null); setAttempt(a => a + 1); }}>Опитай отново</button></> : !data && <p>Изчисляване на отчета по скорост за избрания период…</p>}</div>
    {data && <>
      <p className="report-note">Време по скоростни зони, приравнен обем Q и ефективен товар E за избрания период. Зоните се определят чрез Vflat и предходните индекси за съответния спорт. Продължителностите са във формат ч:мм:сс.</p>
      <div className="report-totals"><dl>
        <div><dt>Продължителност на всички активности</dt><dd>{durationHms(totalDuration)}</dd></div>
        <div><dt>Поддържани за скоростен анализ</dt><dd>{durationHms(data.recorded_minutes)}</dd></div>
        <div><dt>Скоростно зонирано реално време</dt><dd>{durationHms(data.classified_minutes)}</dd></div>
      </dl></div>
      <p className="load-speed-coverage"><strong>Скоростно покритие: {decimal(data.coverage_percent)}%</strong> от записаното време за поддържаните спортове.</p>
      {data.status === "UNAVAILABLE" ? <p className="history-unavailable">Няма достатъчно данни за скоростно натоварване в този период. Липсващата оценка не означава нулево натоварване.</p> : <div className="report-table-wrap"><table>
        <caption>Натоварване по скоростни зони</caption>
        <thead><tr><th>Зона</th><th>Реално време</th><th>Еквивалентно време Q</th><th>Ефективен товар E</th></tr></thead>
        <tbody>{data.zones.map(z => <tr key={z.zone}><th style={{ color: componentColor(z.zone) }}>{z.zone}</th><td>{durationHms(z.minutes)}</td><td>{durationHms(z.equivalent_minutes)}</td><td>{decimal(z.effective_load)}</td></tr>)}</tbody>
      </table></div>}
      <div className="report-table-wrap"><table><caption>По вид спорт · скоростно покритие</caption>
        <thead><tr><th>Спорт</th><th>Активности</th><th>Продължителност</th><th>По скоростни зони</th></tr></thead>
        <tbody>{sports.map(sport => { const rows = data.activities.filter(a => a.sport === sport); return <tr key={sport}><th>{sport}</th><td>{rows.length}</td><td>{durationHms(rows.reduce((sum, a) => sum + a.recorded_minutes, 0))}</td><td>{rows.some(a => a.classified_minutes > 0) ? durationHms(rows.reduce((sum, a) => sum + a.classified_minutes, 0)) : "Няма оценка"}</td></tr>; })}</tbody>
      </table></div>
      <p className="report-note">Q и E описват само покритото скоростно време. Двата отчета са отделни оценки на една и съща работа и не се събират. Силата и неподдържаните спортове остават в пулсовия отчет. Видът спорт включва одобрените индивидуални корекции на етикетите.</p>
      {data.status === "PARTIAL" && <p className="quality-limited">Покритието е частично: липсващите скорости, неподходящите участъци и липсващите предходни индекси не се заместват с предполагаем товар.</p>}
      {data.activities.some(a => a.reason === "SPEED_RECOMPUTATION_REQUIRED") && <p>Има активности със стар анализ. Използвайте „Обнови данните“.</p>}
      {data.warnings.includes("TREADMILL_GRADE_ASSUMED_FLAT") && <p>За пътека без записан наклон е приет 0% наклон.</p>}
      {data.warnings.includes("TREADMILL_PRIOR_RUN_INDEX_FALLBACK") && <p>За пътека без собствен предходен индекс е използван предходният индекс от бягане на същия спортист.</p>}
    </>}
  </div>;
}
