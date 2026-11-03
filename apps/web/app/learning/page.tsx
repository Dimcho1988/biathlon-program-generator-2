import { currentAuthorizedAthlete } from "../../lib/account-access";
import { getLearningView } from "../../lib/api";
import { ErrorState } from "../../components/error-state";
import { RecoveryLearning } from "../../components/recovery-learning-view";
import { learningDate } from "../../lib/recovery-learning";
import "./learning.css";

export const dynamic = "force-dynamic";

export default async function LearningPage({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const access = await currentAuthorizedAthlete();
  if (!access) return <ErrorState message="Влезте в профила си, за да видите самообучението." integrationActions refreshAvailable={false}/>;
  if (!access.canViewRecovery) return <ErrorState message="Този спортист не е споделил данните си за възстановяване с вас." refreshAvailable={false}/>;
  const query = await searchParams;
  const days = [30,90].includes(Number(query.days)) ? Number(query.days) : 90;
  let view: Awaited<ReturnType<typeof getLearningView>>;
  try {
    view = await getLearningView(access.athleteAlias, days);
  } catch (error) { return <ErrorState message={error instanceof Error ? error.message : "Самообучението временно не е достъпно."} retryAvailable retryHref="/learning"/>; }
  return <main className="activities-page learning-page"><header className="activities-hero"><div className="activities-title"><div><p className="eyebrow">Индивидуална реакция към тренировките</p><h1>Самообучение</h1><p>{access.displayName} · оценка към {learningDate(view.as_of)}</p><p>{view.source_date?`Натоварване до ${learningDate(view.source_date)}`:"Няма актуална измерена товарна история"}{view.timezone?` · ${view.timezone}`:""}</p></div></div></header>
      <form className="learning-period" method="get"><label>Период на графиките<select name="days" defaultValue={days}><option value="30">Последни 30 дни</option><option value="90">Последни 90 дни</option></select></label><button className="action-button secondary" type="submit">Покажи</button></form>
      <RecoveryLearning view={view}/>
    </main>;
}
