import { durationHms } from "../lib/duration-format";
import type { RecoveryV2 } from "../lib/models";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });

const reasons:Record<string,string> = {
  PROVIDER_RECORD_MISSING:"Активността не е извлечена изцяло. Обновете данните.",
  Q_REANALYSIS_REQUIRED:"Товарът изисква преизчисляване от първичния запис. Обновете данните.",
  INVALID_RECORDING_LEDGER:"Времето в записа не съответства на отчетения товар. Нужна е проверка на активността.",
  UNKNOWN_INTENSITY_INTERVALS:"Част от записаното време няма надежден показател за интензивност.",
  STRENGTH_LOAD_INCOMPLETE:"Липсват достатъчно данни за силовото натоварване.",
  LEGACY_HR_INCOMPLETE:"Пулсовият запис не позволява пълна оценка на товара.",
  ACTIVITY_Q_INCOMPLETE:"Няма пълно разпределение на товара по компоненти.",
  ACTIVITY_DATE_MISSING:"Липсва дата на активността.",
  RECORDING_DURATION_MISSING:"Липсва общата продължителност на записа.",
  ACTIVITY_SPORT_MISSING:"Липсва разпознаваем вид спорт.",
};

export function RecoveryCoverageIssues({history}:{history:RecoveryV2}) {
  const issues=history.data_quality?.issues;
  if(!issues?.length)return null;
  return <details><summary>Кои активности изискват проверка ({issues.length})</summary>
    <ul>{issues.map((issue,index)=><li key={`${issue.activity_ref??"unknown"}-${index}`}>
      {issue.date??"Без дата"} · {issue.sport??"Активност"}: {reasons[issue.reason]??"Недостатъчни данни за оценка на товара."}
    </li>)}</ul>
  </details>;
}

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
