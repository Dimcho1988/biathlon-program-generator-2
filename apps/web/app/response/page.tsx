import Image from "next/image";
import Link from "next/link";
import { currentAuthorizedAthlete } from "../../lib/account-access";
import { getResponseHistory } from "../../lib/api";
import { ErrorState } from "../../components/error-state";
import { ThemeToggle } from "../../components/theme-toggle";
import { ResponseMonitoring } from "../../components/response-monitoring";
import type { ResponseHistory } from "../../lib/response-monitoring";
import "./response.css";

export default async function ResponsePage({searchParams}:{searchParams:Promise<{start?:string;end?:string}>}) {
  const fixture=process.env.ONFLOWS_DATA_MODE==="fixture";
  const access=fixture?{athleteAlias:"fixture",displayName:"Примерни данни — демонстрация",isOwner:false,canEditPlan:false,canViewRecovery:true}:await currentAuthorizedAthlete();
  if(!access)return <ErrorState message="Влезте в профила си, за да видите личните оценки." integrationActions refreshAvailable={false}/>;
  if(!access.canViewRecovery)return <ErrorState message="Този спортист не е споделил данните си за възстановяване с вас." refreshAvailable={false}/>;
  const query=await searchParams;
  let history: ResponseHistory;
  try {
    history=fixture?(await import("../../lib/response-fixture")).responseFixture:await getResponseHistory(access.athleteAlias,query.start,query.end);
  } catch(error){return <ErrorState message={error instanceof Error?error.message:"Оценките временно не са достъпни."} retryAvailable retryHref="/response"/>;}
  return <main className="activities-page response-page"><header className="activities-hero"><nav aria-label="Основна навигация"><Link className="brand" href="/"><Image src="/brand/onflows-mark.png" width={33} height={40} alt="onFlows лого"/><span>onFlows</span></Link><div className="nav-actions"><Link href="/">Тренировъчен статус</Link><Link href="/activities">Активности</Link><Link href="/trainability">Индекс</Link><ThemeToggle/></div></nav><div className="activities-title"><div><p className="eyebrow">Индивидуален отговор към натоварването</p><h1>Стрес и възстановяване</h1><p>{access.displayName} · оценките следват местния ден ({history.timezone})</p></div></div></header>
      <section className="index-period-controls"><form method="get"><label>От<input type="date" name="start" required defaultValue={history.period_start}/></label><label>До<input type="date" name="end" max={history.today} required defaultValue={history.period_end}/></label><button className="action-button secondary">Покажи периода</button></form></section>
      <ResponseMonitoring key={`${history.period_start}:${history.period_end}`} history={history} canReport={access.isOwner} canEditPlan={access.canEditPlan}/>
    </main>;
}
