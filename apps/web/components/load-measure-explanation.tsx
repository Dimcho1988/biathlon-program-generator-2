import { isRecord } from "../lib/training-status";
import { COMPONENTS, type PlanProjection } from "../lib/training-management";
import { durationHms } from "../lib/duration-format";

export function LoadMeasureExplanation({plan}: {plan?: PlanProjection}) {
  const history = Array.isArray(plan?.component_history) ? plan.component_history.filter(isRecord) : [];
  const weeks = Array.isArray(plan?.history_comparison) ? plan.history_comparison.filter(isRecord) : [];
  const examples = weeks.flatMap(w => {
    const match = history.find(h => h.start_date === w.start_date && h.end_date === w.end_date && h.complete === true);
    const components = isRecord(match?.components) ? match.components : {};
    const volumes = COMPONENTS.map(z => isRecord(components[z]) ? components[z].weekly_q : null);
    return typeof w.actual_minutes === "number" && w.actual_minutes > 0 && volumes.every(v => typeof v === "number" && Number.isFinite(v))
      ? [{start: String(w.start_date), end: String(w.end_date), minutes: w.actual_minutes, equivalent: volumes.reduce<number>((sum, v) => sum + Number(v), 0)}] : [];
  }).sort((a,b) => b.minutes-a.minutes);
  const example = examples[0];
  return <details className="management-detail"><summary>Продължителност и приравнен обем — каква е разликата?</summary>
    <p><strong>Продължителност</strong> е времето за тренировки. В плана включва загряване, основна работа, паузи и разпускане.</p>
    <p><strong>Приравнен обем</strong> отчита и интензивността в зоната. Използва се за сравнение и управление на натоварването. Показва се във формат ч:мм:сс, но не е времето, което трябва да отделиш за тренировка.</p>
    <p><strong>Общ товар E</strong> е прекият приравнен обем Q плюс преноса от всички по-високи зони и добавките между непосредствени съседи. Към всяка по-ниска зона се отчита Q на Z2 × 1,4; Z3 × 1,8; Z4/Z5 × 2,3. Това са общи експертни множители: 1,8 означава 180%, а не 100% + 180%. Те действат и под прага 50%. Силата е отделна.</p>
    <div className="management-table-wrap"><table><caption>Допълнителни проценти върху оригиналния Q към непосредствените съседи</caption><thead><tr><th>Пряк Q / Tmax</th><th>Към горната зона</th><th>Към долната зона</th></tr></thead><tbody><tr><th>Под 50%</th><td>0%</td><td>0%</td></tr><tr><th>От 50% до 80% включително</th><td>20%</td><td>10%</td></tr><tr><th>Над 80%</th><td>30%</td><td>20%</td></tr></tbody></table></div>
    <p>Пример: 30 минути Q в Z3 при Tmax 60 минути дават E: Z1 — 54, Z2 — 57 (54 + 3), Z3 — 30 и Z4 — 6 минути. Всичко се изчислява от прекия Q за всяка тренировка отделно; полученият товар не се прелива повторно. Това не добавя реално време и стойностите E по зони не се сумират като продължителност. Една и съща история E участва в Recovery и 7/40.</p>
    <p><strong>Tmax</strong> е максималната непрекъсната продължителност при избраното усилие, приведена към същата опора като Q. Използва се индивидуалната крива скорост–време; при липсваща използваема крива — експертна оценка. Историческият 40-дневен Tref е отделен параметър.</p>
    {example && <p>Пример от твоята история, {example.start} – {example.end}: <strong>{durationHms(example.minutes)} продължителност</strong> и <strong>{durationHms(example.equivalent)} приравнен обем</strong> за същите дни. Съотношението се променя според изпълнението; няма постоянен коефициент за превръщане.</p>}
    <p>За бъдещите микроцикли се показва целевият приравнен обем. Продължителността е известна след съставяне на конкретните сесии. 7/40 е индекс на натоварването, а не процентен прираст.</p>
  </details>;
}
