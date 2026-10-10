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
    <p><strong>Общ товар E</strong> е прекият приравнен обем Q плюс влиянието от съседните зони. Влиянието се определя от дела Q/Tmax в изходната зона и се изчислява върху целия неин пряк Q.</p>
    <div className="management-table-wrap"><table><caption>Влияние на една зона върху непосредствените ѝ съседи</caption><thead><tr><th>Пряк Q / Tmax</th><th>Към горната зона</th><th>Към долната зона</th></tr></thead><tbody><tr><th>Под 50%</th><td>0%</td><td>0%</td></tr><tr><th>От 50% до 80% включително</th><td>20%</td><td>10%</td></tr><tr><th>Над 80%</th><td>30%</td><td>20%</td></tr></tbody></table></div>
    <p>Пример: 60 приравнени минути в Z3 при Tmax 100 минути добавят 12 минути товар към Z4 и 6 към Z2. Тези добавки не се преливат отново към Z5 или Z1 и не добавят реални минути тренировка.</p>
    <p><strong>Tmax</strong> е максималната непрекъсната продължителност при избраното усилие, приведена към същата опора като Q. Използва се индивидуалната крива скорост–време; при липсваща използваема крива — експертна оценка. Историческият 40-дневен Tref е отделен параметър.</p>
    {example && <p>Пример от твоята история, {example.start} – {example.end}: <strong>{durationHms(example.minutes)} продължителност</strong> и <strong>{durationHms(example.equivalent)} приравнен обем</strong> за същите дни. Съотношението се променя според изпълнението; няма постоянен коефициент за превръщане.</p>}
    <p>За бъдещите микроцикли се показва целевият приравнен обем. Продължителността е известна след съставяне на конкретните сесии. 7/40 е индекс на натоварването, а не процентен прираст.</p>
  </details>;
}
