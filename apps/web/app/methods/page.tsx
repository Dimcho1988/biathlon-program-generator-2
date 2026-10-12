import { currentAuthorizedAthlete } from "../../lib/account-access";
import { fetchApiResource } from "../../lib/api";
import { parseMethodLibrary, type MethodLibraryResponse } from "../../lib/method-library";
import { ErrorState } from "../../components/error-state";
import { TrainingMethodLibrary } from "../../components/training-method-library";
import "./methods.css";

export const dynamic = "force-dynamic";

export default async function MethodsPage({ searchParams }: { searchParams: Promise<{ method?: string }> }) {
  const access = await currentAuthorizedAthlete();
  if (!access) return <ErrorState message="Влезте в профила си, за да отворите базата от тренировъчни методи." integrationActions refreshAvailable={false}/>;
  if (!access.canViewPlan) return <ErrorState message="Този спортист не е споделил тренировъчния си план с вас." refreshAvailable={false}/>;
  let data: MethodLibraryResponse;
  try {
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!token) throw new Error("Базата от методи временно не е достъпна.");
    data = parseMethodLibrary(await fetchApiResource("/api/v2/athlete/management/methods", token, access.athleteAlias, { actorUserId: access.actorUserId }));
  } catch (caught) {
    return <ErrorState message={caught instanceof Error ? caught.message : "Базата от методи временно не е достъпна."} retryAvailable retryHref="/methods"/>;
  }
  const query = await searchParams;
  return <TrainingMethodLibrary key={access.athleteAlias} initial={data} athleteName={access.displayName} canEdit={access.canEditPlan} selectedId={query.method ?? ""}/>;
}
