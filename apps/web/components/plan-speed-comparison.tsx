"use client";
import { useEffect, useRef, useState } from "react";
import { durationHms } from "../lib/duration-format";
import { isPlanSpeedComparison, planSpeedReason, type PlanSpeedSource, type PlanSpeedComparison as Comparison } from "../lib/speed-equivalence";
import { speedText } from "../lib/speed-format";
import { SpeedEquivalenceComparison } from "./speed-equivalence-comparison";
import { useRunningSpeedUnit } from "./running-speed-unit";
import { useManagementFetch } from "./management-scope";
import { isRecord } from "../lib/training-status";

const time = (value: number | null) => value == null ? "Няма съпоставима оценка" : durationHms(value);
const percent = (value: number | null) => value == null ? "—" : `${value.toLocaleString("bg-BG", {maximumFractionDigits: 1})}%`;

export function PlanSpeedComparison({ value, source }: { value?: Comparison; source?: PlanSpeedSource }) {
  return <PlanSpeedComparisonView key={`${source?.kind}:${source?.revision}:${source?.start_date}:${value?.version}`} initial={value} source={source}/>;
}
function PlanSpeedComparisonView({ initial, source }: { initial?: Comparison; source?: PlanSpeedSource }) {
  const unit = useRunningSpeedUnit();
  const scopedFetch = useManagementFetch();
  const [value, setValue] = useState(initial);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  async function refresh() {
    if (!source || request.current) return;
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError("");
    try {
      const query = new URLSearchParams({kind: source.kind, revision: String(source.revision)});
      if (source.kind === "DRAFT" && source.start_date) query.set("start_date", source.start_date);
      const response = await scopedFetch(`/api/athlete/management/speed-comparison?${query}`, {cache: "no-store", signal: controller.signal});
      const result: unknown = await response.json();
      if (!response.ok) throw new Error(isRecord(result) && typeof result.error === "string" ? result.error : "Сравнението временно не е достъпно.");
      if (!isPlanSpeedComparison(result) || result.stored_plan_unchanged !== true || result.source_plan?.kind !== source.kind
        || result.source_plan.revision !== source.revision || source.kind === "DRAFT" && result.source_plan.start_date !== source.start_date)
        throw new Error("Версията на програмата е променена. Презареди страницата за съпоставим отчет.");
      if (!controller.signal.aborted) setValue(result);
    } catch (caught) { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Сравнението временно не е достъпно."); }
    finally { if (!controller.signal.aborted) { request.current = null; setBusy(false); } }
  }
  if (!value && !source) return null;
  return <section className="management-panel" aria-label="План и изпълнение по скорост">
    <h3>Допълнителна съпоставка по скорост</h3>
    <p>Основният товар следва избрания в профила водещ показател — пулс или скорост — с допустим резервен източник за отделните участъци. Тук сравняваме плана и изпълнението само по скорост при еднакви индивидуални опори. Коефициентите 1; 1; 1; 1,2; 1,5 важат за поддържаните спортове.</p>
    {source && <><button type="button" className="action-button secondary" disabled={busy} onClick={refresh}>{busy ? "Обновяване на сравнението…" : "Обнови сравнението по скорост"}</button><p className="management-muted">Изчислява отчета при поискване с текущите индивидуални опори. Запазените тренировки и водещият товар не се променят.</p></>}
    {error && <p role="alert">{error}</p>}
    {value?.actual_through && <p>Изпълнение до {value.actual_through} · запазена програма, версия {value.source_plan?.revision}.</p>}
    {value?.sports.map(report => <div key={report.sport}>
      <h4>{report.sport === "Run" ? "Бягане" : report.sport === "VirtualRun" ? "Бягане на пътека" : report.sport}</h4>
      {report.actual.reason && <p className="management-muted">{planSpeedReason(report.actual.reason)} Съпоставими активности: {report.actual.available_activity_count} от {report.actual.activity_count}.</p>}
      <div className="management-table-wrap"><table><caption>Договорени коефициенти · еквивалентно време, ч:мм:сс</caption><thead><tr><th>Зона / опора</th><th>Цел</th><th>Планирано</th><th>Изпълнено</th><th>Оставащо</th><th>Превишение</th><th>Достигане</th></tr></thead><tbody>{report.targets.map(row => <tr key={row.zone}><th>{row.zone}<br/><small>{speedText(row.reference_speed_kmh, unit, report.sport)}</small></th><td>{time(row.target_equivalent_minutes)}</td><td>{time(row.planned_equivalent_minutes)}</td><td>{time(row.actual_equivalent_minutes)}</td><td>{time(row.remaining_equivalent_minutes)}</td><td>{time(row.excess_equivalent_minutes)}</td><td>{percent(row.target_attainment_percent)}</td></tr>)}</tbody></table></div>
      <details><summary>Чувствителност при c = 1 във всички зони</summary><div className="management-table-wrap"><table><thead><tr><th>Зона</th><th>Планирано</th><th>Изпълнено</th><th>Оставащо</th><th>Достигане · c = 1</th><th>Достигане · договорени c</th></tr></thead><tbody>{report.targets.map(row => <tr key={row.zone}><th>{row.zone}</th><td>{time(row.baseline_planned_equivalent_minutes)}</td><td>{time(row.baseline_actual_equivalent_minutes)}</td><td>{time(row.baseline_remaining_equivalent_minutes)}</td><td>{percent(row.baseline_target_attainment_percent)}</td><td>{percent(row.target_attainment_percent)}</td></tr>)}</tbody></table></div></details>
      {[...new Set(report.targets.map(row => row.reason).filter(Boolean))].map(reason => <p className="management-muted" key={reason}>{planSpeedReason(reason)}</p>)}
      <details><summary>Скоростни опори и покритие на плана</summary><SpeedEquivalenceComparison value={report.planned} title="Планирано еквивалентно време по скорост"/></details>
      {report.actual.comparison && <details><summary>Покритие и ограничения на изпълнението</summary><SpeedEquivalenceComparison value={report.actual.comparison} title="Изпълнено еквивалентно време по скорост"/></details>}
    </div>)}
    <p className="management-muted">Еквивалентните минути от различни зони не се сумират. Планираното включва цялата запазена програма и може да се припокрива с вече изпълнените сесии; тези две колони не се събират. Подготвителните части и почивките остават отделно в подробностите на всяка сесия.</p>
  </section>;
}
