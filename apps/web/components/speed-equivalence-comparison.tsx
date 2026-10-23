"use client";

import { durationHms } from "../lib/duration-format";
import { speedRangeText, speedText } from "../lib/speed-format";
import { speedEquivalenceReason, type SpeedEquivalenceComparison as Comparison } from "../lib/speed-equivalence";
import { useRunningSpeedUnit } from "./running-speed-unit";

const number = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("bg-BG", { maximumFractionDigits: 3 });
const time = (value: number | null) => value === null ? "Няма оценка" : durationHms(value);

export function SpeedEquivalenceComparison({ value, title = "Еквивалентно време по скорост · сравнителен режим" }: { value?: Comparison | null; title?: string }) {
  const unit = useRunningSpeedUnit();
  if (!value) return null;
  return <section className="history-section" aria-label={title}>
    <h3>{title}</h3>
    <p>{value.sport === "VirtualRun" ? "Бягане на пътека" : !value.sport || value.sport === "Run" ? "Бягане" : value.sport} · c = 1; 1; 1; 1,2; 1,5 срещу c = 1 във всички зони. Това са експертни тегла на работното време. Показателят не е измерен енергоразход и не заменя водещия товар за Recovery и 7/40.</p>
    <p>Покритие: <strong>{number(value.coverage_percent)}%</strong> · {durationHms(value.classified_minutes)} от {durationHms(value.recorded_minutes)}. Липсващата оценка не означава нулева работа; досегашният отчет остава отделен резервен показател.</p>
    <div className="activity-table-wrap"><table><caption>Отделни зонални опори · еквивалентно време, ч:мм:сс</caption><thead><tr><th>Зона</th><th>Реално време</th><th>Еквивалентно · c = 1</th><th>Еквивалентно · договорени c</th><th>Q = E/Tref · c = 1</th><th>Q = E/Tref · договорени c</th></tr></thead>
      <tbody>{value.zones.map(z => <tr key={z.zone}><th>{z.zone}</th><td>{durationHms(z.real_minutes)}</td><td>{time(z.baseline_equivalent_minutes)}</td><td>{time(z.equivalent_minutes)}</td><td>{number(z.baseline_normalized_q)}</td><td>{number(z.normalized_q)}</td></tr>)}</tbody></table></div>
    {value.parts && <details><summary>Работни части, загрявка и почивки</summary><div className="activity-table-wrap"><table><thead><tr><th>Част</th><th>Зона</th><th>Реално време</th><th>Избрано еквивалентно време</th><th>Допустим диапазон</th></tr></thead><tbody>{Object.entries(value.parts).flatMap(([kind, part]) => part.zones.filter(z => z.real_minutes > 0).map(z => <tr key={`${kind}-${z.zone}`}><th>{{WORK:"Основна работа",WARMUP:"Загрявка",COOLDOWN:"Разпускане",RECOVERY:"Почивка с движение",PREPARATION:"Подготовка"}[kind] ?? "Друга част"}</th><td>{z.zone}</td><td>{durationHms(z.real_minutes)}</td><td>{time(z.equivalent_minutes)}</td><td>{z.equivalent_minutes_range ? z.equivalent_minutes_range.map(time).join("–") : "Диапазонът не е оценен"}</td></tr>))}</tbody></table></div>{Boolean(value.passive_rest_minutes) && <p>Пасивна почивка: {durationHms(value.passive_rest_minutes!)}.</p>}<p>Показаната избрана стойност се използва за планирането. Диапазоните са граници за отделните части; всички крайни стойности не са непременно съвместими в една тренировка.</p></details>}
    <p>Тук E е еквивалентно време по скорост; нормализираният Q = E/Tref е безразмерен. Еквивалентните минути с различни зонални опори не се сумират. Q по пулс и Q по скорост не се събират за една и съща работа.</p>
    <details><summary>Опори, чувствителност и качество</summary><div className="activity-table-wrap"><table><thead><tr><th>Зона</th><th>Диапазон</th><th>Опора</th><th>Tref, мин</th><th>Наклон W за 1 км/ч</th><th>Състояние</th></tr></thead><tbody>{value.zones.map(z => <tr key={z.zone}><th>{z.zone}</th><td>{z.low_kmh !== null && z.high_kmh !== null ? speedRangeText([z.low_kmh, z.high_kmh], unit, "Run") : "Няма валиден диапазон"}</td><td>{speedText(z.reference_speed_kmh, unit, "Run")}</td><td>{number(z.tref_minutes)}</td><td>{number(z.slope_per_kmh)}</td><td>{z.reason ? speedEquivalenceReason(z.reason) : z.status === "AVAILABLE" ? "Валидна опора" : "Частична оценка"}</td></tr>)}</tbody></table></div>
      <p>Опората е горната граница за Z1–Z4 и долната за Z5. Горната граница на Z5 е максималната средна скорост V(10 сек), а не единичен GPS пик. При тесен диапазон теглото се изменя по-стръмно.</p>
      {value.warnings.length > 0 && <ul>{[...new Set(value.warnings.map(speedEquivalenceReason))].map(w => <li key={w}>{w}</li>)}</ul>}
      {value.zones.find(z => z.zone === "Z5")?.v10_provenance && <p>V(10 сек): {(() => { const evidence = value.zones.find(z => z.zone === "Z5")!.v10_provenance!; return evidence.evidence === "MEASURED" ? "измерена максимална средна скорост" : evidence.extrapolated ? "екстраполирана моделна оценка" : evidence.evidence === "INTERPOLATED" ? "моделна интерполация" : speedEquivalenceReason(evidence.reason ?? "V10_REQUIRED"); })()}</p>}
      {value.start_types && <p>Начало на усилията: {value.start_types.map(start => ({STANDING:"старт от място",FLYING:"летящ старт",UNKNOWN:"не е уточнено"})[start] ?? "не е уточнено").join(", ")}.</p>}
      <p>Версия на показателя: {value.version} · настройки: {value.settings.version} · произход: експертно зададени. Съвпадение с пулса или Recovery не доказва енергийна точност.</p>
    </details>
  </section>;
}
