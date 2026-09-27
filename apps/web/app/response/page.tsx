import { currentAuthorizedAthlete } from "../../lib/account-access";
import { getResponseHistory } from "../../lib/api";
import { ErrorState } from "../../components/error-state";
import { ResponseMonitoring } from "../../components/response-monitoring";
import type { ResponseHistory } from "../../lib/response-monitoring";
import "./response.css";
import Link from "next/link";

export default async function ResponsePage({searchParams}:{searchParams:Promise<{start?:string;end?:string;demo?:string}>}) {
  const fixture=process.env.ONFLOWS_DATA_MODE==="fixture";
  const access=fixture?{athleteAlias:"fixture",displayName:"Примерни данни — демонстрация",isOwner:false,canEditPlan:false,canViewRecovery:true}:await currentAuthorizedAthlete();
  if(!access)return <ErrorState message="Влезте в профила си, за да видите личните оценки." integrationActions refreshAvailable={false}/>;
  if(!access.canViewRecovery)return <ErrorState message="Този спортист не е споделил данните си за възстановяване с вас." refreshAvailable={false}/>;
  const query=await searchParams;
  const demo=fixture||query.demo==="1";
  let history: ResponseHistory;
  try {
    history=demo?(await import("../../lib/response-fixture")).responseFixture:await getResponseHistory(access.athleteAlias,query.start,query.end);
  } catch(error){return <ErrorState message={error instanceof Error?error.message:"Оценките временно не са достъпни."} retryAvailable retryHref="/response"/>;}
  return <main className="activities-page response-page"><header className="activities-hero"><div className="activities-title"><div><p className="eyebrow">Индивидуален отговор към натоварването</p><h1>Стрес и възстановяване</h1><p>{access.displayName} · оценките следват местния ден ({history.timezone})</p></div></div></header>
      {demo?<aside className="stress-demo"><span>Примерни данни · демонстрация на всички канали. Записването е изключено.</span>{!fixture&&<Link href="/response">Към моите данни</Link>}</aside>:<div className="stress-demo"><span>Следете общата реакция и разгънете показателите, които ви интересуват.</span><Link href="/response?demo=1" className="stress-demo-link">Разгледай пример</Link></div>}
      {!demo&&<section className="index-period-controls"><form method="get"><label>От<input type="date" name="start" required defaultValue={history.period_start}/></label><label>До<input type="date" name="end" max={history.today} required defaultValue={history.period_end}/></label><button className="action-button secondary">Покажи периода</button></form></section>}
      <ResponseMonitoring key={`${demo}:${history.period_start}:${history.period_end}`} history={history} canReport={!demo&&access.isOwner} canEditPlan={!demo&&access.canEditPlan}/>
    </main>;
}
