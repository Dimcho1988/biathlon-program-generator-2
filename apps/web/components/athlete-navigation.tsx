import Link from "next/link";
import { currentAuthorizedAthlete } from "../lib/account-access";
import { multiProfileMode } from "../lib/athlete-session";

export async function AthleteNavigation() {
  if (!multiProfileMode()) return <p className="workspace-profile-name">{process.env.ONFLOWS_DATA_MODE === "fixture" ? "Демо данни" : "Тренировъчен анализ"}</p>;
  const access = await currentAuthorizedAthlete();
  if (!access) return <Link href="/account" prefetch={false}>Избери спортист →</Link>;
  return <>
    {access.canEditPlan && <nav className="workspace-profile-links" aria-label="Планиране и настройки"><Link href="/?settings=edit" prefetch={false}>Зони и HRmax</Link></nav>}
    <Link className="workspace-profile" href="/account" prefetch={false}><span className="workspace-avatar" aria-hidden="true">{access.displayName.trim().charAt(0)}</span><span><small>Избран спортист</small><strong>{access.displayName}</strong><small>Смени спортиста →</small></span></Link>
  </>;
}
