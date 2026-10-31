import Link from "next/link";
import { isCalendarDate } from "../lib/training-status";

export function symptomMessage(day: unknown) {
  const label = isCalendarDate(day) ? `${day.slice(8,10)}.${day.slice(5,7)}.${day.slice(0,4)}` : "неуточнена дата";
  return `В последната записана оценка от ${label} има отметка за болка или заболяване. Това е сигнал от този отчет, а не нова оценка на текущото състояние.`;
}
export function SymptomNotice({day}: {day: unknown}) {
  return <div className="management-notice"><p>{symptomMessage(day)}</p><p>Запиши актуална сутрешна оценка и след това избери „Обнови сега“ в програмата. Само премахването на отметката във формата не я записва.</p><Link href="/response#daily-report">Към сутрешната оценка →</Link></div>;
}
