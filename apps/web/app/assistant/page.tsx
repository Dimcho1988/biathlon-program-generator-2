import { currentAuthorizedAthlete } from "../../lib/account-access";
import { AssistantChat } from "../../components/assistant-chat";
import "./assistant.css";

export const dynamic = "force-dynamic";

export default async function AssistantPage() {
  const access = await currentAuthorizedAthlete();
  if (!access) return <main className="assistant-page"><header><p className="eyebrow">onFlows</p><h1>ИИ асистент</h1><p>Влезте и изберете достъпен профил на спортист, за да използвате асистента.</p><a className="action-button" href="/account">Акаунт и спортисти</a></header></main>;
  if (!access.canViewPlan && !access.canViewRecovery) return <main className="assistant-page"><h1>ИИ асистент</h1><p>Няма споделени данни за този профил.</p><a href="/account">Избери друг спортист</a></main>;
  return <AssistantChat key={`${access.actorUserId}:${access.athleteAlias}`} athleteAlias={access.athleteAlias} actorUserId={access.actorUserId} displayName={access.displayName} canEditPlan={access.canEditPlan} isOwner={access.isOwner} canViewPlan={access.canViewPlan === true} canViewRecovery={access.canViewRecovery}/>;
}
