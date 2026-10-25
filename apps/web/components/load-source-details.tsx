import { durationHms } from "../lib/duration-format";
import type { LoadSelectionMetadata } from "../lib/training-status";

const time = (seconds: number) => durationHms(seconds / 60);
const HR_LABELS: Record<string, string> = { HRMOD: "HRmod", RAW_HR: "Суров пулс", MIXED_HR_INTERPOLATION: "Преход между HRmod и суров пулс" };

export function LoadSourceDetails({ value }: { value: LoadSelectionMetadata }) {
  return <div className="load-source-details">
    <p>Източници за {value.activity_count} от {value.total_aerobic_activities} аеробни активности в периода. Времето по източник включва и отчетената почивка.</p>
    <dl>
      <div><dt>Водещ показател</dt><dd>{{ HEART_RATE: "Пулс", SPEED: "Скорост", MIXED: "Различен според запазената активност" }[value.leading_indicator]}</dd></div>
      <div><dt>Версия на общия разчет</dt><dd>{value.ledger_versions.join("; ")}</dd></div>
      <div><dt>Товар по пулс</dt><dd>{time(value.source_seconds.HEART_RATE)}</dd></div>
      <div><dt>Товар по скорост</dt><dd>{time(value.source_seconds.SPEED)}</dd></div>
      {value.hr_source_seconds && Object.entries(value.hr_source_seconds).map(([key, seconds]) => <div key={key}><dt>{HR_LABELS[key] ?? key}</dt><dd>{time(seconds)}</dd></div>)}
      <div><dt>Резервен източник</dt><dd>{time(value.fallback_seconds)}</dd></div>
      <div><dt>Активно време</dt><dd>{time(value.active_seconds)}</dd></div>
      <div><dt>Отчетена почивка</dt><dd>{time(value.rest_seconds)}</dd></div>
      <div><dt>Време без допустим източник</dt><dd>{time(value.unknown_seconds)}</dd></div>
      {value.speed_status_seconds && <div><dt>Време без допустима скорост</dt><dd>{time(value.speed_status_seconds.UNAVAILABLE ?? 0)}</dd></div>}
      {value.unavailable_speed_hr_seconds !== null && <div><dt>От него покрито чрез пулса</dt><dd>{time(value.unavailable_speed_hr_seconds)}</dd></div>}
      {value.speed_unavailable_reasons_seconds?.NEGATIVE_DESCENT_RESIDUAL !== undefined && <div><dt>Отрицателен остатък при спускане</dt><dd>{time(value.speed_unavailable_reasons_seconds.NEGATIVE_DESCENT_RESIDUAL)}</dd></div>}
      {value.estimated_descent_seconds !== null && <div><dt>Балансово оценени спускания</dt><dd>{time(value.estimated_descent_seconds)}</dd></div>}
      <div><dt>Наблюдаван ИТ</dt><dd>Суров измерен пулс</dd></div>
    </dl>
    {value.hr_source_seconds === null && <p>Запазената версия различава пулс и скорост. Разбивката на пулса по HRmod и суров сигнал се появява след пълно обновяване.</p>}
  </div>;
}
