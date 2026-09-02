import Image from "next/image";
import Link from "next/link";
import { redirect } from "next/navigation";
import { MagicLinkForm } from "../../components/magic-link-form";
import { supabaseAuthConfigured } from "../../lib/supabase/config";
import { createClient } from "../../lib/supabase/server";

export default async function LoginPage() {
  if (!supabaseAuthConfigured()) return <main className="account-page"><section className="account-card">
    <p className="section-kicker">onFlows account</p><h1>Входът още не е активиран</h1>
    <p>Supabase Auth ще бъде включен първо в staging след настройване на публичния ключ.</p>
    <Link href="/">Назад към приложението</Link>
  </section></main>;
  const supabase = await createClient();
  const { data } = await supabase.auth.getClaims();
  if (data?.claims?.sub) redirect("/account");
  return <main className="account-page"><section className="account-card">
    <Link className="brand" href="/"><Image src="/brand/onflows-mark.png" alt="" width={33} height={40} /><span>onFlows</span></Link>
    <div><p className="section-kicker">Защитен вход</p><h1>Твоят onFlows акаунт</h1>
      <p>Въвеждаш email и получаваш еднократен линк. Не създаваме и не пазим парола.</p></div>
    <MagicLinkForm />
    <small>Intervals ще се свързва след входа и ще служи само като източник на тренировъчни данни.</small>
  </section></main>;
}
