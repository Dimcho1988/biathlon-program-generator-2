import Link from "next/link";
import { indexNumber, indexTime, invalidLabel, type TrainabilityIndex } from "../lib/trainability";

export function TrainabilitySummary({ index }: { index: TrainabilityIndex | null }) {
  if (!index) return <section className="trainability-summary"><h2>Индекс на тренираност</h2><p>За тази активност още няма изчислен индекс. Използвайте „Обнови данните“ в <Link href="/trainability">страницата за динамиката</Link>.</p></section>;
  return <section className="trainability-summary" aria-label="Индекси на активността">
    <div className="section-heading"><div><p className="section-kicker">HRmod / Vflat</p><h2>Индекс на тренираност</h2></div><Link href="/trainability">Динамика на индекса →</Link></div>
    <p>По-ниска стойност: по-малко пулс спрямо разпределената еквивалентна скорост. Скоростите се разпределят по ранг; не са измерени непременно в същите секунди като пулса.</p>
    <div className="shadow-table-wrap"><table><caption className="sr-only">Зонални индекси и общ индекс 75–92% HRmax</caption><thead><tr><th>Зона / диапазон</th><th>HRmod време</th><th>Дял HR</th><th>Скоростно време</th><th>HRmod, уд./мин</th><th>% HRmax</th><th>Разпределена Vflat, km/h</th><th>Индекс</th></tr></thead><tbody>
      {[...index.zones, index.general].map(band => <tr key={band.name} className={band.name === "GENERAL" ? "index-general-row" : ""}>
        <th scope="row">{band.name === "GENERAL" ? "Общ · 75–92%" : band.name}<small>{indexNumber(band.lower_bpm, 1)}–{indexNumber(band.upper_bpm, 1)} уд./мин</small></th>
        <td>{indexTime(band.hr_seconds)}</td><td>{indexNumber(band.hr_percent, 1)}%</td><td>{indexTime(band.speed_seconds)}</td>
        <td>{indexNumber(band.mean_hrmod_bpm, 1)}</td><td>{indexNumber(band.mean_hrmax_percent, 1)}</td><td>{indexNumber(band.mean_vflat_kmh)}</td>
        <td><strong>{indexNumber(band.index)}</strong>{!band.valid && <small>{invalidLabel(band.invalid_reason)}</small>}</td>
      </tr>)}
    </tbody></table></div>
    <p className="index-coverage">Активност: {index.activity_duration_s === null ? "—" : indexTime(index.activity_duration_s)} · HRmod: {indexTime(index.hr_seconds)} · Допустими скорости: {indexTime(index.eligible_speed_seconds)} · Изключени спускания под −3%: {indexTime(index.downhill_excluded_seconds)} · Други невалидни скорости: {indexTime(index.unavailable_speed_seconds)}.</p>
    <p className="index-note">Минимум 7 минути за активността и 60 секунди за всяко от двете времена. Общият индекс се изчислява отделно за 75–92% HRmax. Границите са работен диапазон за сравнение, а не индивидуално измерени физиологични прагове.</p>
  </section>;
}
