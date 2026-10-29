import { durationHms } from "../lib/duration-format";
import type { RecoveryV2 } from "../lib/models";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });

export function RecoveryEstimateNotice({ history }: { history: RecoveryV2 }) {
  const quality = history.data_quality;
  return <p role="status" className="integration-notice">
    <strong>Моделна оценка на възстановяването.</strong>{" "}
    Използвани са наличните данни за пулс и скорост. Непокритата част от записаното време е оценена по същите правила като в програмата.
    {quality?.coverage_percent != null && <> Покритие на товара с наличните показатели: {number.format(quality.coverage_percent)}%.</>}
    {quality?.unknown_seconds != null && quality.unknown_seconds > 0 && <> Време без измерена интензивност: {durationHms(quality.unknown_seconds / 60)}.</>}
    {" "}Оценката не променя отчетените активности и не е измерено възстановяване.
  </p>;
}
