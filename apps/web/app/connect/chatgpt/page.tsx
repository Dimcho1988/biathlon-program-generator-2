import styles from "./consent.module.css";
import { LoginMethods } from "../../../components/login-methods";
import { loadAccountWorkspace } from "../../../lib/account-access";

export const dynamic = "force-dynamic";

type Query = Record<string, string | string[] | undefined>;

export default async function ConsentPage({ searchParams }: { searchParams: Promise<Query> }) {
  const query = await searchParams;
  const valid = query.client_id === "https://chatgpt.com/oauth/client.json"
    && query.redirect_uri === "https://chatgpt.com/connector_platform_oauth_redirect"
    && query.resource === "https://onflows-api-staging.onrender.com/mcp" && query.response_type === "code"
    && query.code_challenge_method === "S256" && typeof query.code_challenge === "string"
    && /^[A-Za-z0-9_-]{43}$/.test(query.code_challenge)
    && Object.values(query).every(value => typeof value === "string" && value.length <= 2048);
  if (!valid) return <main className="account-page"><section className="account-card">
    <h1>Свързване с ChatGPT</h1><p>Отвори onFlows от менюто за приложения в ChatGPT, за да започнеш свързването.</p>
  </section></main>;
  const workspace = await loadAccountWorkspace();
  const fields = new URLSearchParams(query as Record<string, string>);
  const write = fields.get("scope")?.split(" ").includes("calendar.write");
  return <main className="account-page"><section className="account-card">
    <h1>Свързване с ChatGPT</h1>
    {workspace ? <form className="account-form" method="post" action="/api/integrations/chatgpt">
      <input type="hidden" name="params" value={fields.toString()} />
      <input type="hidden" name="displayedAccount" value={workspace.userId} />
      <fieldset className={styles.roster}><legend>Избери един или повече спортисти</legend>
        <p>ChatGPT получава достъп до дневната програма и стартовете на отбелязаните спортисти.</p>
        {workspace.accessibleAthletes.filter(a => a.canViewPlan).map(a => <label className={styles.choice} key={a.athleteAlias}>
          <input type="checkbox" name="athlete" value={a.athleteAlias} />
          {a.displayName} · {a.athleteAlias}
        </label>)}
      </fieldset>
      {write && <label className={styles.choice}><input type="checkbox" name="allowWrite" /> Разрешавам промяна на датите на стартовете за избраните спортисти, чиито планове мога да редактирам.</label>}
      <button type="submit" name="decision" value="approve" className="action-button">Свържи избраните профили</button>
      <button type="submit" formNoValidate name="decision" value="deny">Откажи</button>
    </form> : <>
      <p>Влез в onFlows, за да избереш спортист и да разрешиш достъпа.</p>
      <LoginMethods resumeConsent />
      <p>При вход с имейл се върни в този раздел и избери <a href={`/connect/chatgpt?${fields}`}>Продължи след вход</a>.</p>
    </>}
  </section></main>;
}
