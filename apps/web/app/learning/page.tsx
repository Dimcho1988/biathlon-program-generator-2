import { currentAuthorizedAthlete } from "../../lib/account-access";
import { getLearningView } from "../../lib/api";
import { ErrorState } from "../../components/error-state";
import { RecoveryLearning } from "../../components/recovery-learning-view";
import { LearningPreparingError, learningDate } from "../../lib/recovery-learning";
import "./learning.css";

export const dynamic = "force-dynamic";

export default async function LearningPage({ searchParams }: { searchParams: Promise<{ days?: string; end?: string; components?: string; total?: string }> }) {
  const access = await currentAuthorizedAthlete();
  if (!access) return <ErrorState message="Влезте в профила си, за да видите самообучението." integrationActions refreshAvailable={false}/>;
  if (!access.canViewRecovery) return <ErrorState message="Този спортист не е споделил данните си за възстановяване с вас." refreshAvailable={false}/>;
  const query = await searchParams;
  const days = [14,30,60,90,366].includes(Number(query.days)) ? Number(query.days) : 30;
  const end = /^\d{4}-\d{2}-\d{2}$/.test(query.end??"")?query.end:undefined;
  let view: Awaited<ReturnType<typeof getLearningView>>;
  try {
    view = await getLearningView(access.athleteAlias, days, end);
  } catch (error) {
    if (error instanceof LearningPreparingError) return <main className="state-page learning-page"><p className="eyebrow">Самообучение · {access.displayName}</p><h1>Подготвяме оценката във фонов режим</h1><p className="muted" role="status">Реалните наблюдения се обработват. Оценката ще се появи, когато е готова.</p><div className="integration-actions"><a className="action-button" href={`/learning?${new URLSearchParams({days:String(days),...(end?{end}:{})})}`}>Провери отново</a></div></main>;
    return <ErrorState message={error instanceof Error ? error.message : "Самообучението временно не е достъпно."} retryAvailable retryHref="/learning"/>;
  }
  return <main className="activities-page learning-page"><header className="activities-hero"><div className="activities-title"><div><p className="eyebrow">Индивидуална реакция към тренировките</p><h1>Самообучение</h1><p>{access.displayName} · текуща оценка към {learningDate(view.as_of)}</p><p>{view.source_date?`Натоварване до ${learningDate(view.source_date)}`:"Няма актуална измерена товарна история"}{view.timezone?` · ${view.timezone}`:""}</p></div></div></header>
      <RecoveryLearning view={view} period={{days,end:end??view.as_of,components:query.components,total:query.total==="1"}}/>

    </main>;
}
