import Link from "next/link";
import {durationHms} from "../lib/duration-format";
import {isRecord} from "../lib/training-status";
import type {PlanProjection} from "../lib/training-management";

export function planningHistoryEstimated(plan?:PlanProjection):boolean {
  const source=isRecord(plan)&&isRecord(plan.source)?plan.source:{};
  const outlook=isRecord(plan?.long_term)?plan.long_term:{};
  const history=isRecord(source.planning_history)?source.planning_history:isRecord(outlook.planning_history)?outlook.planning_history:{};
  return history.estimated===true;
}

export function PlanningEvidenceNotice({plan}:{plan?:PlanProjection}){
  const source=isRecord(plan)&&isRecord(plan.source)?plan.source:{};
  const outlook=isRecord(plan?.long_term)?plan.long_term:{};
  const history=isRecord(source.planning_history)?source.planning_history:isRecord(outlook.planning_history)?outlook.planning_history:{};
  const periodization=isRecord(plan?.periodization)?plan.periodization:{};
  const warnings=Array.isArray(periodization.warnings)?periodization.warnings.filter(isRecord):[];
  const noMainRace=warnings.some(warning=>warning.code==="NO_MAIN_RACE_IN_ANNUAL_WINDOW");
  const coverage=isRecord(history.load_coverage)?history.load_coverage:{};
  const unified=history.policy==="UNIFIED_RECORDED_INDICATORS"||history.policy==="UNIFIED_RECORDED_INDICATORS_WITH_PLANNING_ESTIMATES";
  return <>
    {unified&&history.supported===false&&<aside className="management-notice" role="status"><strong>Непълният товар ограничава програмата</strong><p>Историята съдържа липсващи записи, дни или непроверимо покритие. За тях няма достатъчна основа за оценка и предложението остава ограничено.</p>{typeof history.unresolved_activity_count==="number"&&<p>Неоценими активности в предоставената история: {history.unresolved_activity_count}.</p>}</aside>}
    {history.estimated===true&&<aside className="management-notice" role="status"><strong>План с предварителна оценка по зони</strong><p>{unified?"За записаните участъци без допустим пулс или скорост товарът за планиране е оценен по експертните Q опори.":"Липсващото пулсово покритие е оценено по допустимите скоростни участъци и експертните Q опори за остатъка."} Наличният товар и известната почивка са запазени. Показаните 7/40 и готовност за плана използват тази оценка.</p>{typeof history.estimated_minutes==="number"&&history.estimated_minutes>0&&<p>Оценен зонов товар за {durationHms(history.estimated_minutes)} от записаното време.</p>}{typeof coverage.unknown_seconds==="number"&&<p>В измерената история остават {durationHms(coverage.unknown_seconds/60)} без допустим източник.</p>}<p>Допълнената част е оценка; измереното разпределение по зони остава запазено. Дозата се проверява според метода, възстановяването и настройките; оценката не участва в ИТ, зоните или самообучението.</p></aside>}
    {noMainRace&&<aside className="management-notice" role="status"><strong>Добави основния старт, за да подредим подготовката към него.</strong><p>Краят на програмата не задава автоматично състезание. Без основен старт се показва обща подготовка. След въвеждането му периодите се подреждат назад от старта според оставащото време.</p><Link href="/planning#planning-calendar">Задай основен старт в календара →</Link></aside>}
  </>;
}
