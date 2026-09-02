import Link from "next/link";
import { redirect } from "next/navigation";
import { createClient } from "../../lib/supabase/server";

type Profile = { display_name: string };

export default async function AccountPage({ searchParams }: { searchParams: Promise<{ saved?: string; error?: string }> }) {
  const query = await searchParams;
  const supabase = await createClient();
  const { data } = await supabase.auth.getClaims();
  const userId = data?.claims?.sub;
  if (!userId) redirect("/login");
  const { data: profile } = await supabase.from("onflows_profiles")
    .select("display_name").eq("user_id", userId).maybeSingle<Profile>();
  return <main className="account-page"><section className="account-card account-card-wide">
    <div><p className="section-kicker">Моят профил</p><h1>{profile?.display_name ?? "Добре дошъл в onFlows"}</h1>
      <p>{profile ? "Акаунтът е активен. Следващата стъпка е да свържем Intervals профила към тази самоличност." : "Добави името, с което приятели и треньори ще те разпознават."}</p></div>
    <form className="account-form" method="post" action="/api/account/profile">
      <label><span>Име</span><input name="display_name" minLength={1} maxLength={100} defaultValue={profile?.display_name ?? ""} required /></label>
      <button className="action-button" type="submit">{profile ? "Запази името" : "Създай профила"}</button>
      {query.saved && <p className="form-success">Профилът е запазен.</p>}
      {query.error && <p className="form-error">Името не беше запазено. Опитай отново.</p>}
    </form>
    <div className="account-actions"><Link href="/">Към приложението</Link><form method="post" action="/api/auth/logout"><button type="submit">Изход</button></form></div>
  </section></main>;
}
