import Link from "next/link";
import { indexNumber, indexTime, invalidLabel, type TrainabilityIndex } from "../lib/trainability";

export function TrainabilitySummary({ index }: { index: TrainabilityIndex | null }) {
  if (!index) return <section className="trainability-summary"><h2>Индекс на тренираност</h2><p>За тази активност още няма изчислен индекс. Използвайте „Обнови данните“ в <Link href="/trainability">страницата за динамиката</Link>.</p></section>;
  return <section className="trainability-summary" aria-label="Индекси на активността">
    <div className="section-heading"><div><p className="section-kicker">Суров пулс (%HRmax) / Vflat (km/h)</p><h2>Индекс на тренираност</h2></div><Link href="/trainability">Динамика на индекса →</Link></div>
    <p>По-ниска стойност: по-малък процент от максималния пулс спрямо съпоставената еквивалентна скорост. Използва се процентното число, например 78 / 20 = 3,90. Скоростта се съпоставя със суровия пулс 20 секунди по-късно; пулсът определя зоната. Няма сортиране на скоростите.</p>
    {index.admission?.status === "EXCLUDED" && <p role="status">{invalidLabel(index.admission.reason, 420)}</p>}
    {index.admission?.flagged_bands.map(b => <p key={b.zone}>{b.zone}: отклонение {indexNumber(b.deviation_fraction * 100, 1)}% спрямо предходните съпоставими тренировки.</p>)}
    {index.admission?.reference_status === "INSUFFICIENT_HISTORY" && index.admission.status !== "EXCLUDED" && <p className="index-note">За проверката ±20% още няма поне 7 предходни съпоставими тренировки с валиден индекс в съответната зона.</p>}
    {!index.admission && <p className="index-note">Тук са индексите от записа. Участието им след сравнение с предходните тренировки е показано в <Link href="/trainability">динамиката на индекса</Link>.</p>}
    <div className="shadow-table-wrap"><table><caption className="sr-only">Зонални индекси и общ индекс 75–92% HRmax</caption><thead><tr><th>Зона / диапазон</th><th>Съпоставено време</th><th>Дял HR</th><th>Скоростно време</th><th>Пулс, уд./мин</th><th>% HRmax</th><th>Съпоставена Vflat, km/h</th><th>Индекс</th></tr></thead><tbody>
      {[...index.zones, index.general].map(band => <tr key={band.name} className={band.name === "GENERAL" ? "index-general-row" : ""}>
        <th scope="row">{band.name === "GENERAL" ? "Общ · 75–92%" : band.name}<small>{indexNumber(band.lower_bpm, 1)}–{indexNumber(band.upper_bpm, 1)} уд./мин</small></th>
        <td>{indexTime(band.hr_seconds)}</td><td>{indexNumber(band.hr_percent, 1)}%</td><td>{indexTime(band.speed_seconds)}</td>
        <td>{indexNumber(band.mean_hr_bpm, 1)}</td><td>{indexNumber(band.mean_hrmax_percent, 1)}</td><td>{indexNumber(band.mean_vflat_kmh)}</td>
        <td><strong>{indexNumber(band.index)}</strong>{!band.valid && <small>{invalidLabel(band.invalid_reason, band.minimum_seconds)}</small>}</td>
      </tr>)}
    </tbody></table></div>
    <p className="index-coverage">Активност: {index.activity_duration_s === null ? "—" : indexTime(index.activity_duration_s)} · Съпоставен пулс: {indexTime(index.hr_seconds)} · Допустими скорости: {indexTime(index.eligible_speed_seconds)} · Изключени спускания под −3%: {indexTime(index.downhill_excluded_seconds)} · Други невалидни скорости: {indexTime(index.unavailable_speed_seconds)}.</p>
    <p className="index-note">Минимум 7 минути за активността. Сумарното време на валидните двойки пулс–скорост трябва да е поне 7 минути за Z1–Z4 и общия диапазон, или 5 минути за Z5. Общият индекс се изчислява отделно за 75–92% HRmax. Сравнявайте при сходен спорт, техника, екипировка и условия.</p>
  </section>;
}
