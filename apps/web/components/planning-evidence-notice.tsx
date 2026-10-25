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
  return <>
    {history.policy==="UNIFIED_RECORDED_INDICATORS"&&history.supported===false&&<aside className="management-notice" role="status"><strong>Непълният товар ограничава програмата</strong><p>Общият разчет вече използва наличните пулс и скорост. Останалите непокрити участъци задържат компонентните цели и готовността; предложението съдържа ограничени леки задачи.</p>{typeof history.unresolved_activity_count==="number"&&<p>Непълни активности в предоставената история: {history.unresolved_activity_count}.</p>}{typeof coverage.unknown_seconds==="number"&&typeof coverage.recorded_seconds==="number"&&<p>Без допустим източник: {durationHms(coverage.unknown_seconds/60)} от {durationHms(coverage.recorded_seconds/60)} записано аеробно време.</p>}</aside>}
    {history.estimated===true&&<aside className="management-notice" role="status"><strong>План с предварителна оценка по зони</strong><p>Общото време е от записаните активности. За активностите без достатъчно пулс зоновият товар е оценен по валидни данни за скоростта, когато са налични, или по експертните граници. Показаните 7/40 и готовност използват тази оценка.</p>{typeof history.estimated_minutes==="number"&&history.estimated_minutes>0&&<p>Оценен зонов товар за {durationHms(history.estimated_minutes)} от записаното тренировъчно време.</p>}<p>Допълнената част е оценка; измереното разпределение по зони остава запазено. Дозата се проверява според метода, възстановяването и настройките; оценките остават отделни от реалната история.</p></aside>}
    {noMainRace&&<aside className="management-notice" role="status"><strong>Добави основния старт, за да подредим подготовката към него.</strong><p>Краят на програмата не задава автоматично състезание. Без основен старт се показва обща подготовка. След въвеждането му периодите се подреждат назад от старта според оставащото време.</p><Link href="/planning#planning-calendar">Задай основен старт в календара →</Link></aside>}
  </>;
}
