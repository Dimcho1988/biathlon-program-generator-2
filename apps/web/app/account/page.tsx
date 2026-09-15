import Link from "next/link";
import { redirect } from "next/navigation";
import { AccountWorkspacePanel } from "../../components/account-workspace";
import { loadAccountWorkspace } from "../../lib/account-access";
import { currentAthleteAlias } from "../../lib/athlete-session";

const notices: Record<string, string> = {
  profile: "Името е запазено.",
  organization: "Отборът е създаден и профилът „Главен треньор“ е активен.",
  invite: "Поканата е създадена.",
  accepted: "Поканата е приета и новата роля е активна.",
  linked: "Intervals профилът е свързан с акаунта.",
  selected: "Избраният профил на спортист е активен.",
  assignment: "Спортистът е възложен на треньора.",
};

const errors: Record<string, string> = {
  invalid: "Въведените данни са невалидни.",
  save: "Промяната не беше запазена. Опитай отново.",
  access: "Нямаш право да извършиш тази операция.",
  conflict: "Този профил или покана вече принадлежи на друг акаунт.",
  profile: "Първо запази името на своя акаунт.",
  unavailable: "Ролевият профил временно не е достъпен.",
};

export default async function AccountPage({ searchParams }: { searchParams: Promise<{ saved?: string; error?: string }> }) {
  const query = await searchParams;
  const workspace = await loadAccountWorkspace();
  if (!workspace) redirect("/login");
  const activeAthleteAlias = await currentAthleteAlias();
  const activeAthleteAccessible = workspace.accessibleAthletes.some((athlete) => athlete.athleteAlias === activeAthleteAlias);
  return <main className="account-page"><section className="account-card account-card-wide account-workspace">
    <div><p className="section-kicker">Моят акаунт</p><h1>{workspace.displayName ?? "Добре дошъл в onFlows"}</h1>
      <p>{workspace.displayName ? "Управлявай личния профил, ролите и спортистите, до които имаш достъп." : "Добави името, с което спортистите и треньорите ще те разпознават."}</p></div>
    <form className="account-form" method="post" action="/api/account/profile">
      <label><span>Име</span><input name="display_name" minLength={1} maxLength={100} defaultValue={workspace.displayName ?? ""} required /></label>
      <button className="action-button" type="submit">{workspace.displayName ? "Запази името" : "Създай профила"}</button>
      {query.saved && notices[query.saved] && <p className="form-success">{notices[query.saved]}</p>}
      {query.error && errors[query.error] && <p className="form-error">{errors[query.error]}</p>}
    </form>
    <AccountWorkspacePanel
      workspace={workspace}
      activeAthleteAlias={activeAthleteAccessible ? activeAthleteAlias : null}
      unlinkedAthleteAlias={activeAthleteAlias && !activeAthleteAccessible ? activeAthleteAlias : null}
    />
    <div className="account-actions"><Link href="/">Към приложението</Link><form method="post" action="/api/auth/logout"><button type="submit">Изход</button></form></div>
  </section></main>;
}
